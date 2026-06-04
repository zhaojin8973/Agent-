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
    if "cla-76" in ctx.fx_name.lower():
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
            "CLA-76 attack: crest=%.1f → knob=%.2f (genre=%s)",
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
        if "cla-76" in ctx.fx_name.lower():
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
# 策略注册表
# ════════════════════════════════════════════════════════════════

# FX 类型 → 参数推导函数
_FX_BUILDERS: dict[str, FXBuilderFn] = {
    "eq":         _build_eq_params,
    "comp":       _build_compressor_params,  # vca/fet/opto + rvox + cla-76
    "vca":        _build_compressor_params,
    "fet":        _build_compressor_params,
    "opto":       _build_compressor_params,
    "deesser":    _build_deesser_params,
    "saturation": _build_saturation_params,
    "dynamic_eq": _build_dynamic_eq_params,
    "doubler":    _build_doubler_params,
}


def get_fx_builder(fx_type: str) -> FXBuilderFn | None:
    """获取指定 FX 类型的参数推导函数。

    返回 None 表示无注册策略（engine 将使用通用回退逻辑）。
    """
    # 统一 "comp" 类型别名（vca/fet/opto/rvox/cla-76 都归为 comp）
    if fx_type in ("vca", "fet", "opto"):
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
    if ctx.fx_type in ("vca", "fet", "opto", "comp"):
        return _build_compressor_params(ctx)
    return None
