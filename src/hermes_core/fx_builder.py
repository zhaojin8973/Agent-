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

    支持 VCA/FET/Opto（通过 _COMP_TRANSLATORS）、RVox（特殊 multiplier）、
    CLA-76（crest 驱动 attack + BPM 驱动 release）。
    """
    from hermes_core.comp_engine import (
        _derive_compressor_intent, _compute_cla76_attack_knob,
        _apply_cla76_params, _apply_rvox_params,
        _ms_to_cla76_release,
    )
    from hermes_core.genre_tables import _GENRE_RVOX_MULTIPLIER
    from hermes_core.profiles import _get_compressor_preset, get_bpm_timing

    rms = ctx.raw_rms_db
    peak = ctx.raw_peak_db
    if rms is None or peak is None:
        return None

    intent = _derive_compressor_intent(rms, peak, genre=ctx.genre)

    # BPM-aware timing
    preset = _get_compressor_preset(ctx.role, ctx.genre)
    if (
        ctx.bpm is not None and ctx.bpm > 0
        and "cla-76" not in ctx.fx_name.lower()
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
    if "cla-76" in ctx.fx_name.lower() or "1176" in ctx.fx_name.lower():
        attack_knob = _compute_cla76_attack_knob(intent.crest_factor_db, ctx.genre)
        release_knob = None
        if ctx.bpm is not None and ctx.bpm > 0:
            release_ms = 60000.0 / ctx.bpm
            release_knob = _ms_to_cla76_release(release_ms)
            log.info(
                "BPM-aware timing: %.0f BPM → release=%.0fms (knob %.2f)",
                ctx.bpm, release_ms, release_knob,
            )
        physical = _apply_cla76_params(intent, attack_knob, release_knob)
        log.info(
            "1176/CLA-76 attack: crest=%.1f → knob=%.2f (genre=%s)",
            intent.crest_factor_db, attack_knob, ctx.genre,
        )
    elif ctx.fx_type == "rvox":
        rvox_mult = _GENRE_RVOX_MULTIPLIER.get(ctx.genre, 1.0)
        physical = _apply_rvox_params(intent, preset, rvox_mult)
    elif ctx.fx_type in _COMP_TRANSLATORS and _COMP_TRANSLATORS[ctx.fx_type] is not None:
        physical = _COMP_TRANSLATORS[ctx.fx_type](intent, preset)  # type: ignore[misc]
    else:
        return None

    # No BPM → strip timing keys (leave at plugin defaults)
    if ctx.bpm is None:
        if "cla-76" in ctx.fx_name.lower() or "1176" in ctx.fx_name.lower():
            physical.pop("Release", None)
        else:
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
    """De-Esser 参数推导 — 存在感缺失 → 阈值 + 流派感知 Range。

    Pro-DS: 固定检测频段 HPF=5.5kHz / LPF=12kHz，Single Vocal 模式。
    """
    from hermes_core.genre_tables import _GENRE_PRODS_RANGE

    presence_def = ctx.presence_deficit

    threshold_db = -32.0 + presence_def * 0.1
    threshold_db = max(-60.0, min(0.0, threshold_db))

    range_db = _GENRE_PRODS_RANGE.get(ctx.genre, 8.5)

    hpf_norm = math.log10(5500.0 / 2000.0)
    lpf_norm = math.log10(12000.0 / 2000.0)

    physical = {
        "Mode":              0.0,
        "Band Processing":   0.0,
        "Threshold":         round(threshold_db, 1),
        "Range":             range_db,
        "Lookahead":         10.0,
        "Lookahead Enabled": 1.0,
        "High-Pass Frequency": round(hpf_norm, 3),
        "Low-Pass Frequency":  round(lpf_norm, 3),
        "Input Level":       0.0,
        "Output Level":      0.0,
        "Wet":               1.0,
    }
    log.info(
        "Auto-deesser: band=5.5k–12kHz, presence_def=%.1f → "
        "threshold=%.1f dB, range=%.1f dB (genre=%s)",
        presence_def, threshold_db, range_db, ctx.genre,
    )
    return physical


# ════════════════════════════════════════════════════════════════
# Saturation 策略
# ════════════════════════════════════════════════════════════════


def _build_saturation_params(ctx: FXBuildContext) -> dict | None:
    """饱和参数推导 — Crest Factor → Drive 量。

    高波峰 → 保留瞬态，少饱和；低波峰 → 增加谐波密度。
    """
    crest_db = 12.0
    if ctx.raw_rms_db is not None and ctx.raw_peak_db is not None:
        crest_db = ctx.raw_peak_db - ctx.raw_rms_db

    drive = round(max(0.1, 1.0 - (crest_db - 8.0) * 0.05), 2)
    drive = max(0.1, min(1.0, drive))

    log.info(
        "Auto-saturation: crest=%.1fdB → drive=%.2f (plugin=%s)",
        crest_db, drive, ctx.fx_name,
    )
    return {"Drive": drive, "Mix": 0.5}


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
# Decapitator 饱和策略
# ════════════════════════════════════════════════════════════════


def _build_decapitator_params(ctx: FXBuildContext) -> dict:
    """Decapitator 谐波饱和 — crest 反比驱动 Drive。

    Style=E (EMI)、Tone=0.5、Mix 流派查表。
    放在压缩后——clip gain 峰值已受控，避免过载失真。
    """
    from hermes_core.genre_tables import _GENRE_DECAP_MIX, _DECAP_DRIVE_BASE

    crest_db = 12.0
    if ctx.raw_rms_db is not None and ctx.raw_peak_db is not None:
        crest_db = ctx.raw_peak_db - ctx.raw_rms_db

    base_drive = _DECAP_DRIVE_BASE.get(ctx.genre, 2.0)
    # crest 大 → 少给 Drive, crest 小 → 多给 Drive
    drive_adj = max(-1.0, min(1.0, (12.0 - crest_db) * 0.2))
    drive = round(max(1.0, min(4.0, base_drive + drive_adj)), 1)

    mix = _GENRE_DECAP_MIX.get(ctx.genre, 0.4)

    physical = {
        "Style":  0.25,  # E (EMI) — 最平滑人声模式
        "Drive":  drive,
        "Tone":   0.5,   # 中位
        "Mix":    mix,
        "High Cut": 0.0,  # OFF
        "Low Cut":  0.0,  # OFF
    }
    log.info(
        "Auto-Decapitator: crest=%.1fdB → Drive=%.1f Mix=%.0f%% (genre=%s)",
        crest_db, drive, mix * 100, ctx.genre,
    )
    return physical


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
        "Effect":     effect,
        "Curve":      0.0,     # 负曲线 = 最透明人声模式
        "Clip 0dB":   0.0,     # OFF — 不削波
        "Input":      0.0,     # unity
        "Output":     -0.5,    # 微退防推大
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
        "Air Freq":   air_freq,
        "Air Boost":  air_boost,
        "160Hz Boost": hz160_boost,
        "2.5kHz Boost": hz2500_boost,
        "650Hz Boost":  0.0,
        "Sub 10Hz":     0.0,
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
    "harmonic":    _build_inflator_params,
    "tube_opto":   _build_cl1b_params,
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
