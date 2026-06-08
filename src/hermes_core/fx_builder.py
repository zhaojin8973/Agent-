"""
FX 参数推导策略 — 将 _build_audio_chain 中每种 FX 类型的参数推导逻辑
提取为纯函数，便于测试和扩展。

每个策略函数接受统一的上下文，返回物理参数字典。
REAPER 交互（FX 添加、set_param）由 engine.py 统一处理。
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass, field
from typing import Callable

from hermes_core.normalize import normalize_params
from hermes_core.spectrum import SpectrumAnalyzer

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 上下文
# ════════════════════════════════════════════════════════════════


@dataclass
class FXBuildContext:
    """FX 参数推导所需的全部输入上下文。

    所有字段由 engine._build_audio_chain 在调用策略前填充。
    """

    # ── FX 身份 ──
    fx_name: str                # REAPER 插件名
    fx_type: str                # 归一化类型: eq/comp/deesser/...
    role: str                   # "vocal" / "backing"
    genre: str                  # 流派键
    bpm: float | None = None    # 工程 BPM

    # ── 音频分析 ──
    raw_rms_db: float | None = None
    raw_peak_db: float | None = None
    stem_file_path: str = ""
    presence_deficit: float = 0.0

    # ── 额外状态（跨 FX 共享） ──
    last_eq_params: dict = field(default_factory=dict)
    eq_position: str = "solo"   # "solo" / "group"


# ════════════════════════════════════════════════════════════════
# 策略类型
# ════════════════════════════════════════════════════════════════

# 每个策略函数返回物理参数字典，失败返回 None
FXBuilderFn = Callable[[FXBuildContext], dict | None]


# ════════════════════════════════════════════════════════════════
# EQ 策略
# ════════════════════════════════════════════════════════════════


def _build_eq_params(ctx: FXBuildContext) -> dict | None:
    """EQ 参数推导 — 频谱分析 → EqIntent → 归一化参数。

    注意：此函数在 engine._apply_eq_baseline 之前被调用，
    实际物理参数由 _apply_eq_baseline 通过 set_param 写入。
    返回值用于 node.params 记录（跟踪用途）。
    """
    # EQ 的参数实际由 _apply_eq_baseline 推导和应用
    # 这里返回上下文中的 last_eq_params 用于 node 跟踪
    return dict(ctx.last_eq_params) if ctx.last_eq_params else None


# ════════════════════════════════════════════════════════════════
# 压缩器策略
# ════════════════════════════════════════════════════════════════


# ── 翻译器字典（模块级惰性导入） ──
_COMP_TRANSLATORS: dict[str, Callable | None] = {
    "vca": None, "fet": None, "opto": None,
}


def _init_comp_translators() -> None:
    """惰性导入压缩器翻译函数。"""
    from hermes_core.comp_engine import (
        _apply_vca_params, _apply_fet_params, _apply_opto_params,
    )
    _COMP_TRANSLATORS["vca"] = _apply_vca_params
    _COMP_TRANSLATORS["fet"] = _apply_fet_params
    _COMP_TRANSLATORS["opto"] = _apply_opto_params


def _build_compressor_params(ctx: FXBuildContext) -> dict | None:
    """压缩器参数推导 — crest/peak 分析 → CompressionIntent → 物理参数。

    VCA/FET/Opto/RVox 通过各翻译器处理。CLA-76 由 cla76.py 模块独立处理。
    """
    from hermes_core.comp_engine import (
        _derive_compressor_intent, _apply_rvox_params,
    )
    from hermes_core.genre_tables import _GENRE_RVOX_MULTIPLIER
    from hermes_core.profiles import _get_compressor_preset, get_bpm_timing

    # CLA-76 由独立模块处理
    if "cla-76" in ctx.fx_name.lower():
        from hermes_core.cla76 import build_params as cla76_build
        return cla76_build(ctx)

    rms = ctx.raw_rms_db
    peak = ctx.raw_peak_db
    if rms is None or peak is None:
        return None

    intent = _derive_compressor_intent(rms, peak, genre=ctx.genre)

    # BPM-aware timing
    preset = _get_compressor_preset(ctx.role, ctx.genre)
    if (
        ctx.bpm is not None and ctx.bpm > 0
        and "1176" not in ctx.fx_name.lower()
        and ctx.fx_type != "rvox"
    ):
        bpm_timing = get_bpm_timing(ctx.bpm)
        if bpm_timing is not None:
            preset = dict(preset, **bpm_timing)
            log.info(
                "BPM-aware timing: %.0f BPM → attack=%.0fms release=%.0fms",
                ctx.bpm, bpm_timing["attack_ms"], bpm_timing["release_ms"],
            )

    # Per-type dispatch
    if "1176" in ctx.fx_name.lower():
        physical = _COMP_TRANSLATORS["fet"](intent, preset) if _COMP_TRANSLATORS.get("fet") else None
    elif ctx.fx_type == "rvox":
        rvox_mult = _GENRE_RVOX_MULTIPLIER.get(ctx.genre, 1.0)
        physical = _apply_rvox_params(intent, preset, rvox_mult)
    elif ctx.fx_type in _COMP_TRANSLATORS and _COMP_TRANSLATORS[ctx.fx_type] is not None:
        physical = _COMP_TRANSLATORS[ctx.fx_type](intent, preset)  # type: ignore[misc]
    else:
        return None

    # No BPM — strip timing keys (leave at plugin defaults)
    if ctx.bpm is None and "1176" not in ctx.fx_name.lower():
        for timing_key in ("Attack", "Release"):
            physical.pop(timing_key, None)

    log.info(
        "Auto-compressor: %s → %s (crest=%.1f dB, gr=%.1f dB)",
        ctx.fx_name, intent.amount, intent.crest_factor_db,
        intent.gr_target_db,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# De-Esser 策略
# ════════════════════════════════════════════════════════════════


def _build_deesser_params(ctx: FXBuildContext) -> dict:
    """Pro-DS 齿音消除 — 委托到 :mod:`hermes_core.pro_ds`。

    所有参数逻辑、流派表、公式集中在 pro_ds.py 中。
    """
    from hermes_core.pro_ds import build_params as prods_build
    return prods_build(ctx)


# ════════════════════════════════════════════════════════════════
# Dynamic EQ 策略
# ════════════════════════════════════════════════════════════════


def _build_dynamic_eq_params(ctx: FXBuildContext) -> dict | None:
    """动态 EQ 参数推导 — 频谱分析 → 共振检测 → 动态模式 Pro-Q 3。

    对共振频段启用 Dynamics Enabled + Dynamic Range + Threshold。
    """
    from hermes_core.eq_engine import _derive_eq_intent, _apply_proq3_eq

    if not ctx.stem_file_path or not os.path.exists(ctx.stem_file_path):
        log.debug("Dynamic EQ: no stem file available, skipping")
        return None

    try:
        report = SpectrumAnalyzer.analyze(ctx.stem_file_path)
        eq_intent = _derive_eq_intent(
            report, role=ctx.role, genre=ctx.genre, position=ctx.eq_position,
        )
        normalized = _apply_proq3_eq(eq_intent)
        # 为共振频段启用动态模式
        for band_num in range(1, 9):
            band_key = f"Band {band_num} Used"
            if band_key in normalized and normalized.get(band_key, 0.0) > 0.0:
                normalized[f"Band {band_num} Dynamics Enabled"] = 1.0
                normalized[f"Band {band_num} Dynamic Range"] = 0.6
                normalized[f"Band {band_num} Threshold"] = 0.5
        log.info(
            "Auto-dynamic-EQ: %d resonance bands with dynamic mode (plugin=%s)",
            sum(1 for b in eq_intent.bands if b.gain_db < 0), ctx.fx_name,
        )
        return dict(normalized)
    except Exception as exc:
        log.debug("Dynamic EQ spectrum analysis failed (%s), skipping", exc)
        return None


# ════════════════════════════════════════════════════════════════
# Doubler 策略
# ════════════════════════════════════════════════════════════════


def _build_doubler_params(ctx: FXBuildContext) -> dict:
    """Doubler/MicroShift 参数推导 — 增加人声宽度和空间感。"""
    physical = {"Mix": 0.3, "Detune": 0.15, "Delay": 0.05}
    log.info(
        "Auto-doubler: Mix=%.2f Detune=%.2f (plugin=%s)",
        0.3, 0.15, ctx.fx_name,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Decapitator 饱和策略（委托到独立模块）
# ════════════════════════════════════════════════════════════════


def _build_decapitator_params(ctx: FXBuildContext) -> dict | None:
    """Decapitator 谐波饱和 — 委托到 :mod:`hermes_core.decapitator`。

    所有参数逻辑、流派表、公式集中在 decapitator.py 中。
    """
    from hermes_core.decapitator import build_params as decap_build
    return decap_build(ctx)


# ════════════════════════════════════════════════════════════════
# Pultec EQP-1A 电子管染色策略
# ════════════════════════════════════════════════════════════════


def _build_pultec_params(ctx: FXBuildContext) -> dict:
    """Pultec EQP-1A — 经典同时推拉 Trick。

    60Hz Boost+Atten 低频塑形, 8-12kHz High Boost 补亮,
    20kHz Atten 压齿音高段。即使参数为0，电子管级也会加微妙染色。
    """
    mud = ctx.presence_deficit  # 用 presence_deficit 作 mud proxy
    # 更准确：从 spectrum 获取
    sibilance = 0.0  # 需从 last_spectrum 获取

    # Low: 60Hz 经典 Trick
    low_boost = 3.5
    if mud > 3.0:
        low_atten = 2.5
    elif mud > 0:
        low_atten = 1.5
    else:
        low_atten = 0.5

    # High: presence 驱动
    deficit = ctx.presence_deficit
    high_freq = 12000.0 if deficit > 0 else 8000.0
    high_boost = round(max(0.0, min(5.0, deficit * 0.4)), 1)
    high_atten = 2.0 if sibilance > -30.0 else 0.0

    physical = {
        "Low Freq":    60.0,
        "Low Boost":   low_boost,
        "Low Atten":   low_atten,
        "High Freq":   high_freq,
        "High Boost":  high_boost,
        "High Atten":  high_atten,
        "High BW":     5.0,
    }
    log.info(
        "Auto-Pultec: 60Hz boost=%.1f atten=%.1f | %.0fHz boost=%.1f (deficit=%.1f)",
        low_boost, low_atten, high_freq, high_boost, deficit,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Bettermaker EQ232D 染色策略
# ════════════════════════════════════════════════════════════════


def _build_eq232d_params(ctx: FXBuildContext) -> dict:
    """Bettermaker EQ232D — 干净固态 Pultec 风格母带级 EQ。

    硬件原型为 EQ232P MKII。人声链策略：
    - CHANNEL=Dual Mono（人声是单声道）
    - Channel 1 PEQ 段：Pultec 经典低频推拉 + presence 驱动高频
    - Channel 2 完全关闭
    - EQ1/EQ2 参量段关闭（已有 Pro-Q 3 做手术 EQ）
    - HPF 关闭（Pro-Q 3 已做高通）
    - KCS 旁路（底鼓/军鼓滤波器，与人声无关）
    """
    deficit = ctx.presence_deficit
    sibilance = 0.0  # 后续可从 spectrum 获取

    # Low: Pultec 经典 60Hz 推拉 Trick
    # LO CPS — 低频频率选择器（CPS=Cycles Per Second）
    # 0.33 ≈ 60Hz（经典 Pultec 值）
    lo_cps = 0.33
    low_boost = max(0.0, min(1.0, 3.5 / 10.0))
    if deficit > 3.0:
        low_atten = 2.5 / 10.0
    elif deficit > 0:
        low_atten = 1.5 / 10.0
    else:
        low_atten = 0.5 / 10.0

    # High: presence 驱动（大而干净的高频提升）
    high_boost = max(0.0, min(1.0, deficit * 0.04))
    high_atten = 0.2 if sibilance > -30.0 else 0.0
    high_bw = 0.5

    physical = {
        # ── 通道配置 ──
        "CHANNEL":    1.0,   # Dual Mono（人声单声道）
        "MS MATRIX":  0.0,   # M/S 关闭（母带功能，人声不需要）
        # ── Channel 1: 启用 PEQ ──
        "ENGAGE 1":   1.0,   # Ch1 ON
        "HPF IN 1":   0.0,   # HPF 关闭（Pro-Q 3 已做）
        "EQ1 IN 1":   0.0,   # 参量段关闭（已有手术 EQ）
        "EQ2 IN 1":   0.0,
        "PEQ IN 1":   1.0,   # Pultec 被动 EQ ON
        "LO CPS 1":   lo_cps,
        "LO BOOST 1": low_boost,
        "LO ATTEN 1": low_atten,
        "HI BOOST 1": high_boost,
        "HI ATTEN 1": high_atten,
        "HI BW 1":    high_bw,
        "KCS BST 1":  0.0,   # Kick/Snare Boost 关闭
        "KCS ATT 1":  0.0,   # Kick/Snare Atten 关闭
        "LVL OUT 1":  0.5,   # unity
        # ── Channel 2: 完全关闭 ──
        "ENGAGE 2":   0.0,   # Ch2 OFF（避免默认直通）
    }
    log.info(
        "Auto-EQ232D: Ch1 PEQ @60Hz boost=%.2f atten=%.2f | "
        "Hi boost=%.2f bw=%.2f | Ch2=OFF | DualMono (deficit=%.1f)",
        low_boost, low_atten, high_boost, high_bw, deficit,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Oxford Inflator 谐波密度策略
# ════════════════════════════════════════════════════════════════


def _build_inflator_params(ctx: FXBuildContext) -> dict:
    """Oxford Inflator — 流派差异化谐波密度。

    Effect 20-40%, Curve 负值(透明), Clip 0dB OFF。
    人声保守使用，不超 50% 防失真。
    """
    from hermes_core.genre_tables import _GENRE_INFLATOR_EFFECT

    effect = _GENRE_INFLATOR_EFFECT.get(ctx.genre, 0.30)

    physical = {
        "Input Gain":  0.0,     # unity
        "Effect":      effect,
        "Curve":       0.0,     # 负曲线 = 最透明人声模式
        "Output Gain": 0.0,     # unity
        "In":          1.0,     # ON
        "Band Split":  0.0,     # OFF（全频段处理）
        "Clip 0dB":    0.0,     # OFF — 不削波
    }
    log.info(
        "Auto-Inflator: effect=%.0f%% curve=0 (genre=%s)",
        effect * 100, ctx.genre,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# CL 1B 光电压缩策略
# ════════════════════════════════════════════════════════════════


def _build_cl1b_params(ctx: FXBuildContext) -> dict:
    """UAD CL 1B — 光电体压缩 + tube 温暖塑形。

    Ratio 2:1-4:1, Attack 5ms 慢起振, Release BPM驱动,
    Threshold 基于 post-RVox RMS。
    """
    from hermes_core.genre_tables import _GENRE_CL1B_RATIO

    ratio = _GENRE_CL1B_RATIO.get(ctx.genre, 3.0)

    rms = ctx.raw_rms_db
    threshold = (rms + 4.0) if rms is not None else -12.0

    release_s = 0.3
    if ctx.bpm is not None and ctx.bpm > 0:
        release_s = (60000.0 / ctx.bpm) * 0.25 / 1000.0

    physical = {
        "Ratio":       ratio,
        "Threshold":   round(threshold, 1),
        "Attack":      5.0,    # ms — 慢起振保自然
        "Release":     round(release_s, 3),
        "Gain":        0.0,    # 稍后自动补偿
        "Sidechain HPF": 80.0,
    }
    log.info(
        "Auto-CL1B: ratio=%.0f:1 thresh=%.1fdB attack=5ms release=%.3fs (genre=%s)",
        ratio, threshold, release_s, ctx.genre,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Shadow Hills Mastering Compressor 策略
# ════════════════════════════════════════════════════════════════


def _build_shadow_hills_params(ctx: FXBuildContext) -> dict:
    """Shadow Hills Mastering Compressor — 仅光学级 + Iron 变压器染色。

    离散级完全 bypass，用光学级的自然起振/释放曲线做平滑人声控制。
    """
    from hermes_core.genre_tables import _GENRE_CL1B_RATIO

    rms = ctx.raw_rms_db
    # 光学阈值：基于 RMS 推导（0–1 范围）
    if rms is not None:
        # RMS 越热越需要压，阈值往低推
        optical_thresh = max(0.0, min(1.0, (rms + 18.0) / 30.0))
    else:
        optical_thresh = 0.3

    # 增益补偿：光学压了多少就补多少
    optical_gain = max(0.0, min(1.0, optical_thresh * 0.5 + 0.26))

    # 侧链 HPF — 流派映射
    sidechain_hpf_d = {
        "pop": 0.15,
        "rock": 0.12,
        "electronic": 0.10,
        "folk": 0.18,
        "ballad": 0.18,
        "chinese_folk_bel_canto": 0.20,
    }
    sidechain_hpf = sidechain_hpf_d.get(ctx.genre, 0.15)

    physical = {
        "Hardwire Bypass":      1.0,   # Hardwire 参考路径 ON
        "Optical Bypass 1":     1.0,   # 光学级 ON（1=engaged, 0=bypassed）
        "Optical Threshold 1":  round(optical_thresh, 3),
        "Optical Gain 1":       round(optical_gain, 3),
        "Discrete Bypass 1":    0.0,   # 离散级 BYPASS
        "Discrete Ratio 1":     0.4,   # 默认（不使用时无关）
        "Discrete Attack 1":    1.0,   # 默认
        "Discrete Recover 1":   0.0,   # 默认
        "Discrete Gain 1":      0.0,   # unity
        "Sidechain Filter 1":   0.0,   # 侧链滤波器 OFF（离散级旁路）
        "Transformer 1":        1.0,   # Iron 变压器 ON（染色）
        "Sidechain HP Freq":    sidechain_hpf,
        "Mix":                  1.0,
    }
    log.info(
        "Auto-Shadow Hills: optical thresh=%.3f gain=%.3f | "
        "discrete=bypass | iron=xfmr (genre=%s)",
        optical_thresh, optical_gain, ctx.genre,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Maag EQ4 Air Band 策略
# ════════════════════════════════════════════════════════════════


def _build_maag_params(ctx: FXBuildContext) -> dict:
    """Maag EQ4 — Air Band 最终抛光。

    Air 频率流派差异化 (10k/20k), Boost=deficit×0.3+|air|×0.2,
    160Hz 补瘦声, 2.5kHz 补亮度。
    """
    from hermes_core.genre_tables import _GENRE_MAAG_AIR_FREQ

    air_freq = _GENRE_MAAG_AIR_FREQ.get(ctx.genre, 20000.0)
    deficit = ctx.presence_deficit

    air_boost = round(max(0.0, min(6.0, deficit * 0.3 + abs(deficit) * 0.1)), 1)

    # mud 代理: presence_deficit 大 → 可能是混 → 不补 160Hz
    hz160_boost = 1.5 if deficit < -3.0 else 0.0
    hz2500_boost = 1.5 if deficit > 4.0 else 0.0

    physical = {
        "Sub":         0.0,    # OFF（人声不需要 Sub）
        "40 Hz":       0.0,    # OFF
        "160 Hz":      hz160_boost,
        "650 Hz":      0.0,    # OFF
        "2.5 kHz":     hz2500_boost,
        "Air Gain":    air_boost,
        "Air Band":    air_freq,
        "Level Trim":  1.0,    # unity（输出增益由 Inflator/Shadow Hills 控制）
        "In/Out":      1.0,    # IN
    }
    log.info(
        "Auto-Maag: air=%.0fHz +%.1fdB | 160Hz=+%.1f 2.5k=+%.1f (genre=%s)",
        air_freq, air_boost, hz160_boost, hz2500_boost, ctx.genre,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# 策略注册表
# ════════════════════════════════════════════════════════════════

# FX 类型 → 参数推导函数
_FX_BUILDERS: dict[str, FXBuilderFn] = {
    "eq":          _build_eq_params,
    "comp":        _build_compressor_params,  # vca/fet/opto + rvox + cla-76 + 1176
    "vca":         _build_compressor_params,
    "fet":         _build_compressor_params,
    "opto":        _build_compressor_params,
    "deesser":     _build_deesser_params,
    "saturation":  _build_decapitator_params,
    "color_eq":    _build_pultec_params,
    "color_eq_232d": _build_eq232d_params,
    "harmonic":    _build_inflator_params,
    "tube_opto":   _build_cl1b_params,
    "tube_opto_sh": _build_shadow_hills_params,
    "air_eq":      _build_maag_params,
    "dynamic_eq":  _build_dynamic_eq_params,
    "doubler":     _build_doubler_params,
}


def get_fx_builder(fx_type: str) -> FXBuilderFn | None:
    """获取指定 FX 类型的参数推导函数。

    返回 None 表示无注册策略（engine 将使用通用回退逻辑）。
    """
    # 统一 "comp" 类型别名（vca/fet/opto/rvox/cla-76 都归为 comp）
    if fx_type in ("vca", "fet", "opto", "rvox", "cla-76"):
        return _build_compressor_params
    return _FX_BUILDERS.get(fx_type)


def build_fx_params(ctx: FXBuildContext) -> dict | None:
    """根据 FX 类型推导物理参数。

    返回物理参数字典供 normalize_params + set_param 使用。
    返回 None 表示无可用策略（应跳过或使用通用回退）。
    """
    builder = get_fx_builder(ctx.fx_type)
    if builder is not None:
        return builder(ctx)
    # 回退：comp 类型统一处理（rvox/cla-76 也走压缩器路径）
    if ctx.fx_type in ("vca", "fet", "opto", "comp", "rvox", "cla-76"):
        return _build_compressor_params(ctx)
    return None


# ════════════════════════════════════════════════════════════════
# 参数应用到 REAPER 轨道
# ════════════════════════════════════════════════════════════════


def apply_params_to_track(
    fx_mgr,
    track_index: int,
    fx_idx: int,
    ctx: FXBuildContext,
) -> dict | None:
    """推导并应用 FX 参数到 REAPER 轨道插槽。

    策略推导 → normalize → set_param。所有 REAPER 交互通过 *fx_mgr* 代理。

    Parameters
    ----------
    fx_mgr : FxManager
        REAPER FX 管理器，提供 ``set_param(track, slot, name, value)``。
    track_index : int
        目标轨道索引。
    fx_idx : int
        轨道上的 FX 插槽索引。
    ctx : FXBuildContext
        推导上下文（含 fx_name、fx_type、音频分析数据等）。

    Returns
    -------
    dict or None
        成功时返回归一化前的物理参数字典（用于 node.params 跟踪）。
        无可用策略时返回 ``None``——调用方应使用通用回退参数。
    """
    physical = build_fx_params(ctx)
    if physical is not None:
        try:
            normalized = normalize_params(ctx.fx_name, physical)
            for pname, pval in normalized.items():
                fx_mgr.set_param(track_index, fx_idx, pname, pval)
        except Exception as exc:
            log.debug(
                "%s param application failed (%s), skipping",
                ctx.fx_name, exc,
            )
        return dict(physical)
    return None
