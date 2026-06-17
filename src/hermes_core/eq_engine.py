"""
EQ 参数引擎 — 从频谱分析推导 EQ 物理参数。

从 engine.py 提取的模块级辅助函数。
包含 EQ 基线应用、RMS 匹配补偿和校正性 EQ。
"""

from __future__ import annotations

import bisect
import logging
import math
import os
from typing import TYPE_CHECKING

from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
from hermes_core.spectrum import SpectrumReport, SpectrumAnalyzer
from hermes_core.genre_tables import (
    _GENRE_EQ_TWEAKS,
    _GENRE_EQ_PCT,
    _MIN_EQ_Q,
    _PROQ3_SHAPE,
    _PROQ3_FREQ_LOG_BASE,
    _LF_FRQ_TABLE,
    _LMF_FRQ_STEPS,
    _HMF_FRQ_TABLE,
    _HF_FRQ_TABLE,
    _HP_FRQ_TABLE,
    _SSL_Q_MIN,
    _SSL_Q_MAX,
    _SSL_Q_RANGE,
)
from hermes_core.profiles import _EQ_BASELINE

if TYPE_CHECKING:
    from hermes_core.fx import FxManager

log = logging.getLogger(__name__)


def _derive_eq_intent(
    report: SpectrumReport,
    role: str = "vocal",
    genre: str = "pop",
    position: str = "solo",
    vocal_profile: object | None = None,
    cross_report: object | None = None,
) -> EqIntent:
    """Derive EQ goals from spectrum analysis.

    Rule-based decision logic — no ML required.  Rules are applied
    selectively based on *position* in the FX chain:

    - ``"pre"`` — corrective EQ before compression.
      Runs all 6 rules, but boost thresholds are conservative
      (prefer subtraction; only boost when the signal truly needs it).
    - ``"post"`` — tonal / colour EQ after compression.
      Runs all 6 rules without restriction.  Cuts are allowed because
      compression (especially FET saturation) can introduce new peaks
      that need taming.
    - ``"solo"`` — all rules (standalone, backward-compatible default).

    The six rules are:
    1. **HPF** — frequency scales with sub-band energy.
    2. **Resonance cuts** — narrow peaks (Q > 15, non-harmonic) get
       bell cuts proportional to their prominence.
    3. **Low-mid mud cut** — broad attenuation when the low-mid band
       exceeds the mid band by the genre threshold.
    4. **Presence boost** — gentle bell lift when the presence band
       is quiet relative to mid / dark vocal.
    5. **Air shelf** — high shelf when the air band is low and the
       spectral tilt is steeply negative.
    6. **Genre adjustments** — pop gets extra presence, rock tolerates
       more mud, folk scales back all boosts.
    """
    _POSITIONS = ("pre", "post", "solo", "surgical")
    if position not in _POSITIONS:
        raise ValueError(f"position must be one of {_POSITIONS}, got {position!r}")

    # ── 模式语义 ──
    #   "surgical" — 仅减法（HPF + 共振切除 + 泥巴切）。
    #       跳过存在感/空气提升，留给下游 EQ232D/Maag 处理。
    #   "pre"      — 压缩前校正。全规则但 boost 保守。
    #   "post"     — 压缩后。全规则无限制。
    #   "solo"     — 独立使用。全规则无限制（向后兼容默认）。
    surgical_only = position == "surgical"
    conservative = position == "pre"

    vp = vocal_profile
    tweaks = _GENRE_EQ_TWEAKS.get(genre, _GENRE_EQ_TWEAKS["default"])
    bands: list[EqBandIntent] = []

    # ── 声纹感知安全范围 ──
    if role == "vocal" and vp:
        hpf_min = getattr(vp, "hpf_min_hz", 40.0)
        hpf_max = getattr(vp, "hpf_max_hz", 150.0)
        hpf_default = getattr(vp, "hpf_default_hz", 80.0)
        boost_scale = getattr(vp, "boost_scale", 1.0)
    else:
        hpf_min, hpf_max, hpf_default, boost_scale = 40.0, 150.0, 80.0, 1.0

    # ── Rule 1: HPF ─────────────────────────────────────────
    sub_energy = report.band_energy_db.get("sub", -60.0)
    mid_energy = report.band_energy_db.get("mid", -60.0)
    sub_excess = sub_energy - mid_energy

    # 低切频率：优先用频谱定位的精准频点，否则回退频段估算
    hpf_hz = float(getattr(report, "hpf_hz", 0.0) or 0.0)
    if hpf_hz <= 0:
        hpf_hz = hpf_default
        if role == "backing":
            hpf_hz = max(20.0, hpf_default * 0.5)
        if sub_excess > 3.0:
            hpf_hz = min(hpf_max, hpf_hz + (sub_excess - 3.0) * 10)
    hpf_hz = max(hpf_min, min(hpf_max, hpf_hz))

    # ── 交叉频谱参考：伴奏低频重 → 人声 HPF 略提高 ──
    if cross_report and role == "vocal":
        offset = getattr(cross_report, "vocal_hpf_offset_hz", 0.0)
        if offset > 0:
            hpf_hz = min(hpf_max, hpf_hz + offset)

    # 斜率动态：频率越低越陡
    if hpf_hz < 100.0:
        slope_db_per_oct = 24.0
    else:
        slope_db_per_oct = 18.0

    bands.append(EqBandIntent(
        band_type="hp", freq_hz=round(hpf_hz, 1), gain_db=0.0,
        q=0.7, slope=slope_db_per_oct,
        reason=f"HPF@{hpf_hz:.0f}Hz {slope_db_per_oct:.0f}dB/oct sub_excess={sub_excess:.1f}dB",
    ))

    # ── Rule 2: Resonance cuts ──────────────────────────────
    for res in report.resonances:
        # Skip harmonics — they're musical content, not problems
        if res.is_harmonic:
            bands.append(EqBandIntent(
                band_type="bell", freq_hz=res.freq_hz,
                gain_db=max(-2.0, -res.prominence_db * 0.3),
                q=min(res.q_factor * 0.5, 10.0),
                reason=f"{res.freq_hz}Hz harmonic Q={res.q_factor:.0f} (light touch)",
            ))
            continue

        # Q > 15 → genuine room resonance → cut
        if res.q_factor < _MIN_EQ_Q:
            continue

        # Skip presence band (2-5 kHz) — critical for intelligibility
        if 2000.0 <= res.freq_hz <= 5000.0:
            continue

        cut_db = -min(res.prominence_db, 6.0)
        bands.append(EqBandIntent(
            band_type="bell", freq_hz=res.freq_hz,
            gain_db=round(cut_db, 1),
            q=min(res.q_factor * 0.5, 10.0),
            reason=f"{res.freq_hz}Hz room mode Q={res.q_factor:.0f} prominence={res.prominence_db:.1f}dB",
        ))

    # ── Rule 3: Low-mid mud cut ─────────────────────────────
    mud_threshold = tweaks.get("mud_threshold_db", 3.0)
    if vp:
        mud_threshold = getattr(vp, "mud_threshold_db", mud_threshold)
    if report.mud_ratio_db > mud_threshold:
        cut_db = -min(report.mud_ratio_db - 2.0, 4.0)
        cut_db = max(cut_db, -4.0)
        mud_hz = getattr(report, "mud_peak_hz", 350.0) or 350.0
        bands.append(EqBandIntent(
            band_type="bell", freq_hz=mud_hz, gain_db=round(cut_db, 1),
            q=0.7,
            reason=f"Mud cut@{mud_hz:.0f}Hz mud_ratio={report.mud_ratio_db:.1f}dB",
        ))

    # ── Rule 4: Presence boost + Rule 5: Air shelf（百分比驱动）─
    if not surgical_only:
        from hermes_core.vocal_ref import get_ref, relativize, deviation

        band = getattr(report, "band_energy_db", {}) or {}
        ref = get_ref(getattr(vp, "gender", "") if vp else "") if band and "mid" in band else None

        if ref:
            rel = relativize(band)

            for rule_key, band_key, band_type, default_q in [
                ("presence", "presence", "bell",        1.0),
                ("air",      "air",      "high_shelf",  0.7),
            ]:
                band_rel = rel.get(band_key, -32.0)
                ref_center, ref_tol = ref[band_key]
                dev = deviation(band_rel, ref_center)

                # 仅在偏差超出容差时生效
                if abs(dev) <= ref_tol:
                    continue

                pct = _GENRE_EQ_PCT.get(genre, 30)
                gain_db = round(-dev * pct / 100.0, 1)
                if conservative:
                    gain_db = round(gain_db * 0.5, 1)

                if abs(gain_db) < 0.3:
                    continue

                if band_key == "presence":
                    gap_hz = getattr(report, "presence_gap_hz", 3000.0) or 3000.0
                    reason = f"Presence{gain_db:+.1f}dB@{gap_hz:.0f}Hz dev={dev:+.1f}dB"
                    bands.append(EqBandIntent(
                        band_type="bell", freq_hz=gap_hz, gain_db=gain_db,
                        q=default_q, reason=reason,
                    ))
                else:
                    rolloff_hz = getattr(report, "air_rolloff_hz", 8000.0) or 8000.0
                    reason = f"Air{gain_db:+.1f}dB@{rolloff_hz:.0f}Hz dev={dev:+.1f}dB"
                    bands.append(EqBandIntent(
                        band_type="high_shelf", freq_hz=rolloff_hz,
                        gain_db=gain_db, q=default_q, reason=reason,
                    ))

    # ── Assemble ─────────────────────────────────────────────
    # Cap at 8 bands (Pro-Q 3 limit).  Priority order:
    #   1. HPF (always included — structural necessity)
    #   2. Resonance cuts (most prominent first)
    #   3. Tonal balance (mud → presence → air)
    hpf_bands = [b for b in bands if b.band_type == "hp"]
    reso_bands = [b for b in bands if b.band_type == "bell" and b.gain_db <= -2.0]
    tonal_bands = [b for b in bands if b not in hpf_bands and b not in reso_bands]

    capped: list[EqBandIntent] = []
    capped.extend(hpf_bands[:1])                    # exactly 1 HPF
    capped.extend(reso_bands[:5])                    # top 5 resonance cuts
    remaining = 8 - len(capped)
    capped.extend(tonal_bands[:remaining])

    return EqIntent(
        bands=capped,
        spectral_tilt=(
            "dark" if report.spectral_tilt_db_per_octave < -2.0
            else "bright" if report.spectral_tilt_db_per_octave > 2.0
            else "neutral"
        ),
        mud_detected=report.mud_ratio_db > mud_threshold,
    )


def _proq3_freq_norm(hz: float) -> float:
    """Convert Hz to Pro-Q 3 normalised frequency (0–1, log scale)."""
    return math.log10(max(float(hz), 10.0) / 10.0) / _PROQ3_FREQ_LOG_BASE


def _proq3_q_norm(q: float) -> float:
    """Convert Q value to Pro-Q 3 normalised Q (0–1, log scale).

    Verified: Q=1.0 ↔ norm=0.5.  Range is 0.025 – 40.
    Formula: norm = log10(Q / 0.025) / log10(40.0 / 0.025).
    """
    return math.log10(max(float(q), 0.025) / 0.025) / math.log10(40.0 / 0.025)


def _apply_proq3_eq(eq_intent: EqIntent) -> dict[str, float]:
    """Translate an :class:`EqIntent` into Pro-Q 3 normalised (0–1) parameters.

    Maps each :class:`EqBandIntent` to Pro-Q 3 band slots (1–8).
    **All** values are normalised to 0–1 and ready for direct REAPER use.
    Callers should write these values directly via ``FxManager.set_param``
    — do **not** route through :func:`normalize_params`.

    **Every** parameter is set explicitly so that no garbage values
    leak from previous plugin state.

    Verified parameter names, defaults, and curve formulas (reapy, 2026-05-31).
    """
    _GAIN_RANGE = 60.0  # -30 .. +30 dB

    _DEFAULTS = {
        "Dynamic Range":       0.5,    # 0 dB
        "Dynamics Enabled":    0.0,    # static EQ — no dynamic bands
        "Threshold":           1.0,    # Auto
        "Slope":               0.0,
        "Stereo Placement":    0.5,    # Stereo
        "Speakers":            0.0,    # Stereo (not Center/Surround)
        "Solo":                0.0,    # Disabled
    }

    # Pro-Q 3 slope: 0=6, 1=12, 2=18, 3=24, ..., 9=96 dB/oct
    # 公式: slope_index = (dB_per_oct - 6) / 6, 归一化 = slope_index / 9

    params: dict[str, float] = {}

    for i, band in enumerate(eq_intent.bands[:8]):
        n = i + 1
        shape = _PROQ3_SHAPE.get(band.band_type, 0.0)
        gain_norm = (band.gain_db + 30.0) / _GAIN_RANGE

        params[f"Band {n} Used"] = 1.0
        params[f"Band {n} Enabled"] = 1.0
        params[f"Band {n} Frequency"] = round(_proq3_freq_norm(band.freq_hz), 10)
        params[f"Band {n} Gain"] = round(max(0.0, min(1.0, gain_norm)), 10)
        params[f"Band {n} Q"] = round(_proq3_q_norm(band.q), 10)
        params[f"Band {n} Shape"] = round(shape, 10)

        for pname, pval in _DEFAULTS.items():
            params.setdefault(f"Band {n} {pname}", pval)

        if band.band_type in ("hp", "lp"):
            # 动态斜率：6-96 dB/oct → Pro-Q 3 归一化 (0–1)
            slope_idx = max(0, min(9, (band.slope - 6.0) / 6.0))
            params[f"Band {n} Slope"] = round(slope_idx / 9.0, 10)

    # Disable unused bands
    for n in range(len(eq_intent.bands) + 1, 9):
        params[f"Band {n} Used"] = 0.0
        params[f"Band {n} Enabled"] = 0.0
        params[f"Band {n} Speakers"] = 0.0        # Stereo
        params[f"Band {n} Stereo Placement"] = 0.5
        params[f"Band {n} Solo"] = 0.0

    # Global: Output Level with headroom protection.
    # Pro-Q 3's Output Level range is -36 .. +36 dB, norm = (dB + 36) / 72.
    # If the EQ adds any net boost, attenuate the output so the next plugin
    # (typically a compressor calibrated at -18 dBFS) doesn't clip internally.
    total_boost = sum(max(0.0, b.gain_db) for b in eq_intent.bands)
    if total_boost > 0.0:
        out_db = -total_boost
        params["Output Level"] = round((out_db + 36.0) / 72.0, 10)
    else:
        params["Output Level"] = 0.5  # 0 dB, unity

    return params


def _ssleq_freq_norm(target_hz: float, table: list[tuple[float, float]]) -> float:
    """Find the norm value for *target_hz* via interpolation in *table*.

    The table is ``[(norm, Hz), …]`` sorted ascending by Hz.
    SSL EQ frequency is continuous in the VST — interpolation between
    calibration knots is correct even for detented knobs.
    Values outside the table range are clamped to the nearest endpoint.
    """
    hz_list = [row[1] for row in table]

    if target_hz <= hz_list[0]:
        return table[0][0]
    if target_hz >= hz_list[-1]:
        return table[-1][0]

    idx = bisect.bisect_left(hz_list, target_hz)
    if idx == 0:
        return table[0][0]

    lo_n, lo_hz = table[idx - 1]
    hi_n, hi_hz = table[idx]

    if hi_hz == lo_hz:
        return lo_n

    t = (target_hz - lo_hz) / (hi_hz - lo_hz)
    return lo_n + t * (hi_n - lo_n)


def _ssleq_q_norm(q: float) -> float:
    """Map SSL EQ Q value (0.1–3.5) to norm (1.0–0.0)."""
    clamped = max(_SSL_Q_MIN, min(_SSL_Q_MAX, q))
    return (_SSL_Q_MAX - clamped) / _SSL_Q_RANGE


def _apply_ssleq_eq(eq_intent: EqIntent) -> dict[str, float]:
    """Translate an :class:`EqIntent` into SSL EQ normalised (0–1) parameters.

    SSL EQ has 4 bands: LF (shelf), LMF (bell), HMF (bell), HF (shelf).
    All values are normalised to 0–1, ready for direct REAPER use.

    Band assignment by frequency:
    - ≤ 2 kHz → LMF (200–2500 Hz range, e.g. resonance / mud cuts)
    - > 2 kHz → HMF (600–7000 Hz range, e.g. presence boost)
    - ``high_shelf`` / ``air`` → HF
    - ``low_shelf`` / ``warmth`` → LF
    - ``hp`` → HP On/Off + HP Frq
    """
    _LF_GAIN_RANGE = 34.0   # ±17 dB
    _MF_GAIN_RANGE = 40.0   # ±20 dB
    _HF_GAIN_RANGE = 34.0   # ±17 dB
    _OUT_GAIN_RANGE = 24.0  # +12 dB (boost); cut side is 48.0 (-24 dB) — piecewise
    _LMF_HMF_BOUNDARY = 2000.0  # Hz — frequencies ≤ this go to LMF, above to HMF

    params: dict[str, float] = {
        "Bypass": 0.0,
        "EQ IN": 1.0,
        "Analog": 1.0,       # always on for character
        "HP On/Off": 0.0,
        "LMF Div3": 0.0,
        "HMF Mul3": 0.0,
        # Default gains at 0 dB
        "LF Gain": 0.5,
        "LMF Gain": 0.5,
        "HMF Gain": 0.5,
        "HF Gain": 0.5,
        "Gain": 0.5,
        # Default frequencies at mid-points
        "LF Frq": 0.5,
        "LMF Frq": 0.5,
        "HMF Frq": 0.5,
        "HF Frq": 0.5,
        "HP Frq": 0.012,
        # Default Q at mid-point
        "LMF Q": 0.5,
        "HMF Q": 0.5,
    }

    for band in eq_intent.bands:
        if band.band_type in ("high_shelf", "air"):
            # → HF shelf
            gain_norm = (band.gain_db + _HF_GAIN_RANGE / 2) / _HF_GAIN_RANGE
            params["HF Gain"] = round(max(0.0, min(1.0, gain_norm)), 10)
            params["HF Frq"] = round(_ssleq_freq_norm(band.freq_hz, _HF_FRQ_TABLE), 10)

        elif band.band_type in ("bell", "presence"):
            # Route by frequency: ≤2kHz → LMF, >2kHz → HMF
            if band.freq_hz <= _LMF_HMF_BOUNDARY:
                gain_norm = (band.gain_db + _MF_GAIN_RANGE / 2) / _MF_GAIN_RANGE
                params["LMF Gain"] = round(max(0.0, min(1.0, gain_norm)), 10)
                params["LMF Frq"] = round(_ssleq_freq_norm(band.freq_hz, _LMF_FRQ_STEPS), 10)
                params["LMF Q"] = round(_ssleq_q_norm(band.q), 10)
            else:
                gain_norm = (band.gain_db + _MF_GAIN_RANGE / 2) / _MF_GAIN_RANGE
                params["HMF Gain"] = round(max(0.0, min(1.0, gain_norm)), 10)
                params["HMF Frq"] = round(_ssleq_freq_norm(band.freq_hz, _HMF_FRQ_TABLE), 10)
                params["HMF Q"] = round(_ssleq_q_norm(band.q), 10)

        elif band.band_type == "low_shelf":
            # → LF shelf (optional low warmth)
            gain_norm = (band.gain_db + _LF_GAIN_RANGE / 2) / _LF_GAIN_RANGE
            params["LF Gain"] = round(max(0.0, min(1.0, gain_norm)), 10)
            params["LF Frq"] = round(_ssleq_freq_norm(band.freq_hz, _LF_FRQ_TABLE), 10)

        elif band.band_type in ("hp",):
            # HPF — unlikely in post-comp, but handle gracefully
            params["HP On/Off"] = 1.0
            params["HP Frq"] = round(_ssleq_freq_norm(band.freq_hz, _HP_FRQ_TABLE), 10)

    # Output level: check total boost, compensate.
    # SSL EQ Output Gain is piecewise-linear (verified reapy, 2026-05-31):
    #   boost side: norm = (dB + 12) / 24   0 .. +12 dB
    #   cut side:   norm = (dB + 24) / 48   -24 .. 0 dB
    total_boost = sum(max(0.0, b.gain_db) for b in eq_intent.bands)
    if total_boost > 0.0:
        out_db = -total_boost
        if out_db >= 0:
            out_norm = (out_db + 12.0) / 24.0
        else:
            out_norm = (out_db + 24.0) / 48.0
        params["Gain"] = round(max(0.0, min(1.0, out_norm)), 10)

    return params


# ════════════════════════════════════════════════════════════════
# RMS 匹配补偿
# ════════════════════════════════════════════════════════════════


def apply_eq_rms_match(
    fx: "FxManager",
    track_index: int,
    fx_index: int,
    pre_rms_db: float,
    post_rms_db: float,
) -> None:
    """补偿 EQ 增益变化，使下游节点看到一致的 RMS。

    如果 EQ 导致 RMS 下降了 *Δ* dB，则应用 *+Δ* dB 的输出增益。
    这可以防止在仅 EQ 频率变化时级联失效下游压缩器。

    在每次 EQ 参数更新后调用。
    """
    delta = pre_rms_db - post_rms_db
    if abs(delta) < 0.2:
        return  # 不可闻 — 跳过以避免参数抖动

    log.debug(
        "RMS match: track %d EQ@%d pre=%.1f → post=%.1f (Δ=%.1f dB)",
        track_index, fx_index, pre_rms_db, post_rms_db, delta,
    )
    fx.set_param(track_index, fx_index, "Output Gain", delta)
    fx.set_param(track_index, fx_index, "Output", delta)


# ════════════════════════════════════════════════════════════════
# EQ 基线应用
# ════════════════════════════════════════════════════════════════


def apply_eq_baseline(
    fx: "FxManager",
    track_index: int,
    fx_index: int,
    role: str,
    *,
    genre: str = "pop",
    stem_file_path: str = "",
    position: str = "solo",
    fx_name: str = "",
    last_eq_params: dict | None = None,
    last_spectrum: dict | None = None,
    vocal_profile: object | None = None,
    cross_report: object | None = None,
) -> dict | None:
    """对指定轨道/FX 应用角色感知的 EQ 基线。

    当 *stem_file_path* 指向可读 WAV 文件时，使用完整的频谱驱动管线::

        SpectrumAnalyzer → EqIntent → 翻译器 → FxManager

    翻译器根据 *fx_name* 选择：
    - ``SSLEQ`` → :func:`_apply_ssleq_eq`
    - 其他 → :func:`_apply_proq3_eq`

    否则回退到 :data:`_EQ_BASELINE` 静态基线。

    Parameters
    ----------
    fx : FxManager
        FX 管理器实例。
    track_index : int
        REAPER 轨道索引。
    fx_index : int
        轨道上的 FX 槽位索引。
    role : str
        ``"vocal"`` 或 ``"backing"``。
    genre : str
        流派键。
    stem_file_path : str
        音频文件路径（可选，用于频谱分析）。
    position : str
        ``"pre"`` / ``"post"`` / ``"solo"``。
    fx_name : str
        REAPER 插件名称。
    last_eq_params : dict | None
        输出字典，填充归一化后的 EQ 参数。
    last_spectrum : dict | None
        输出字典，填充频谱分析缓存数据。

    Returns
    -------
    dict | None
        归一化 EQ 参数字典，失败时返回 None。
    """
    if last_eq_params is not None:
        last_eq_params.clear()

    log.debug(
        "EQ baseline for %s/%s/%s: stem_file_path=%r, exists=%s, "
        "fx_name=%r, position=%s",
        role, genre,
        "spectrum" if (stem_file_path and os.path.exists(stem_file_path)) else "static",
        stem_file_path or "",
        os.path.exists(stem_file_path) if stem_file_path else False,
        fx_name, position,
    )

    # ── 频谱驱动 EQ（优先路径）─────────────────────────────
    if stem_file_path and os.path.exists(stem_file_path):
        try:
            report = SpectrumAnalyzer.analyze(
                stem_file_path, vocal_profile=vocal_profile,
            )
            if last_spectrum is not None:
                last_spectrum.update({
                    "presence_deficit": report.presence_deficit_db,
                    "air_level_db": report.air_level_db,
                    "sibilance_peak_hz": report.sibilance_peak_hz,
                    "mud_ratio": report.mud_ratio_db,
                })
            log.info(
                "Spectrum analysis: tilt=%.1f dB/oct, mud=%.1f dB, "
                "presence_deficit=%.1f dB, sib_peak=%.0f Hz, air=%.1f dB, "
                "resonances=%d, bands=%s",
                report.spectral_tilt_db_per_octave,
                report.mud_ratio_db,
                report.presence_deficit_db,
                report.sibilance_peak_hz,
                report.air_level_db,
                len(report.resonances),
                {k: v for k, v in report.band_energy_db.items()},
            )
            eq_intent = _derive_eq_intent(
                report, role=role, genre=genre, position=position,
                vocal_profile=vocal_profile,
                cross_report=cross_report,
            )

            is_ssl = "ssleq" in fx_name.lower()
            if is_ssl:
                normalized = _apply_ssleq_eq(eq_intent)
            else:
                normalized = _apply_proq3_eq(eq_intent)

            for pname, pval in normalized.items():
                fx.set_param(track_index, fx_index, pname, pval)

            if last_eq_params is not None:
                last_eq_params.update(normalized)
            log.info(
                "Auto-EQ (%s/%s/%s): %d bands @%s — %s",
                role, genre, position, len(eq_intent.bands),
                "SSLEQ" if is_ssl else "Pro-Q3",
                ", ".join(b.reason for b in eq_intent.bands),
            )
            return dict(normalized)
        except Exception as exc:
            log.warning(
                "Spectrum-driven EQ failed (%s), falling back to baseline",
                exc,
            )

    # ── 静态基线回退 ──────────────────────────────────────
    bands = _EQ_BASELINE.get(role, [])
    if not bands:
        log.debug("EQ baseline: no baseline bands for role=%r, skipping", role)
        return None

    log.info(
        "EQ baseline fallback (%s/%s/%s): %d bands — %s",
        role, genre, position, len(bands),
        [(b.get("type"), b.get("freq_hz"), b.get("gain_db", 0.0))
         for b in bands],
    )

    band_intents = []
    for b in bands:
        band_intents.append(EqBandIntent(
            band_type=b.get("type", "bell"),
            freq_hz=b.get("freq_hz", 1000.0),
            gain_db=b.get("gain_db", 0.0),
            q=b.get("q", 1.0),
            reason=f"baseline:{b.get('type','')}@{b.get('freq_hz',0):.0f}Hz",
        ))
    eq_intent = EqIntent(
        bands=band_intents,
        spectral_tilt="neutral",
        mud_detected=False,
    )

    is_ssl = "ssleq" in fx_name.lower()
    try:
        if is_ssl:
            normalized = _apply_ssleq_eq(eq_intent)
        else:
            normalized = _apply_proq3_eq(eq_intent)
        for pname, pval in normalized.items():
            fx.set_param(track_index, fx_index, pname, pval)
        if last_eq_params is not None:
            last_eq_params.update(normalized)
    except Exception as exc:
        log.warning("Baseline EQ apply failed: %s", exc)
        return None

    log.info(
        "EQ baseline (%s/%s): %d bands applied",
        role, genre, len(bands),
    )
    return dict(normalized)


# ════════════════════════════════════════════════════════════════
# 校正性 EQ
# ════════════════════════════════════════════════════════════════


def auto_corrective_eq(
    api,
    fx: "FxManager",
    track_idx: int,
    stems_cache: list[dict],
) -> dict:
    """基于频谱分析的共振检测自动生成校正性 EQ。

    分析轨道上的音频，检测共振频率，并自动设置 Pro-Q 3 的
    衰减频段来削减不需要的共振。

    Parameters
    ----------
    api : reapy API
        REAPER API 对象（bridge.api）。
    fx : FxManager
        FX 管理器实例。
    track_idx : int
        目标轨道索引。
    stems_cache : list[dict]
        prepare_stems 的缓存列表。

    Returns
    -------
    dict
        ``{track_idx, eq_bands, resonance_count, applied}``
    """
    track_ptr = api.GetTrack(0, track_idx)
    if not track_ptr:
        return {"track_idx": track_idx, "error": "Track not found",
                "eq_bands": [], "applied": False}

    # 1. 获取轨道上的音频文件路径
    stem_file = ""
    for s in stems_cache:
        if s.get("track_index") == track_idx and s.get("success"):
            stem_file = s.get("file_path", "")
            break

    if not stem_file or not os.path.exists(stem_file):
        log.warning("auto_corrective_eq: no audio source for track %d", track_idx)
        return {"track_idx": track_idx, "error": "No audio source",
                "eq_bands": [], "applied": False}

    # 2. 频谱分析 → 共振检测
    try:
        report = SpectrumAnalyzer.analyze(stem_file)
    except Exception as exc:
        log.warning("auto_corrective_eq: spectrum analysis failed — %s", exc)
        return {"track_idx": track_idx,
                "error": f"Spectrum analysis failed: {exc}",
                "eq_bands": [], "applied": False}

    # 3. 仅对非谐波共振（Q > 15）生成衰减频段
    eq_bands: list[dict] = []
    for resonance in report.resonances:
        if resonance.is_harmonic:
            continue
        if resonance.q_factor < 15.0:
            continue
        cut_db = -min(resonance.prominence_db, 6.0)
        eq_bands.append({
            "freq": resonance.freq_hz,
            "gain": cut_db,
            "q": min(resonance.q_factor * 0.5, 10.0),
            "type": "bell",
            "reason": f"{resonance.freq_hz}Hz room mode "
                      f"Q={resonance.q_factor:.0f} "
                      f"prominence={resonance.prominence_db:.1f}dB",
        })

    if not eq_bands:
        log.info("auto_corrective_eq: no resonances detected on track %d",
                 track_idx)
        return {"track_idx": track_idx,
                "resonance_count": len(report.resonances),
                "eq_bands": [], "applied": False}

    # 4. 查找轨道上第一个可用的 EQ 插件并应用频段
    n_fx = api.TrackFX_GetCount(track_ptr)
    eq_fx_idx = -1
    for f in range(n_fx):
        ret, name_buf = api.TrackFX_GetFXName(track_ptr, f, "", 256)
        if isinstance(ret, (list, tuple)):
            name = ret[4] if len(ret) > 4 else ""
        else:
            name = name_buf or ""
        if "pro-q" in name.lower() or "reaeq" in name.lower():
            eq_fx_idx = f
            break

    if eq_fx_idx < 0:
        eq_fx_idx = fx.add(track_idx, "ReaEQ (Cockos)")
        if eq_fx_idx < 0:
            log.warning("auto_corrective_eq: cannot add EQ plugin to track %d",
                        track_idx)
            return {"track_idx": track_idx,
                    "eq_bands": eq_bands,
                    "resonance_count": len(report.resonances),
                    "applied": False}

    # 5. 构建 EqIntent 并应用
    band_intents = []
    for b in eq_bands[:8]:
        band_intents.append(EqBandIntent(
            band_type=b["type"],
            freq_hz=b["freq"],
            gain_db=b["gain"],
            q=b["q"],
            reason=b.get("reason", f"corrective:{b['freq']:.0f}Hz"),
        ))

    eq_intent = EqIntent(
        bands=band_intents,
        spectral_tilt="neutral",
        mud_detected=report.mud_ratio_db > 3.0,
    )
    normalized = _apply_proq3_eq(eq_intent)
    for pname, pval in normalized.items():
        fx.set_param(track_idx, eq_fx_idx, pname, pval)

    log.info(
        "auto_corrective_eq: track %d — %d corrective bands applied "
        "(out of %d detected resonances)",
        track_idx, len(eq_bands), len(report.resonances),
    )
    return {
        "track_idx": track_idx,
        "eq_bands": eq_bands,
        "resonance_count": len(report.resonances),
        "applied": True,
        "eq_fx_idx": eq_fx_idx,
    }


# ════════════════════════════════════════════════════════════════
# 伴奏 EQ 推导（自身平衡 + 人声避让）
# ════════════════════════════════════════════════════════════════


def derive_backing_eq(
    backing_report: SpectrumReport,
    masking_zones: list | None = None,
    role: str = "backing",
) -> list[EqBandIntent]:
    """为伴奏轨推导 EQ 频段：自身平衡 + 人声避让。

    Parameters
    ----------
    backing_report : SpectrumReport
        伴奏的频谱分析报告。
    masking_zones : list[MaskingZone] | None
        来自 :class:`CrossSpectrumReport` 的掩蔽区列表。
    role : str
        ``"backing"``。

    Returns
    -------
    list[EqBandIntent]
        推导出的 EQ 频段列表（先自身平衡，后避让）。
    """
    bands: list[EqBandIntent] = []

    # ── 自身平衡：泥巴切 ──────────────────────────────────
    if backing_report.mud_ratio_db > 3.0:
        cut_db = -min(backing_report.mud_ratio_db - 2.0, 4.0)
        mud_hz = backing_report.mud_peak_hz or 350.0
        bands.append(EqBandIntent(
            band_type="bell", freq_hz=mud_hz, gain_db=round(cut_db, 1),
            q=0.7, reason=f"Backing mud cut@{mud_hz:.0f}Hz",
        ))

    # ── 自身平衡：过亮/过暗 ────────────────────────────────
    tilt = backing_report.spectral_tilt_db_per_octave
    if tilt > 2.0:
        rolloff = backing_report.air_rolloff_hz or 8000.0
        bands.append(EqBandIntent(
            band_type="high_shelf", freq_hz=rolloff,
            gain_db=-1.5, q=0.7,
            reason=f"Backing bright cut@{rolloff:.0f}Hz tilt={tilt:.1f}",
        ))
    elif tilt < -5.0:
        bands.append(EqBandIntent(
            band_type="high_shelf", freq_hz=6000.0,
            gain_db=1.0, q=0.7,
            reason=f"Backing dark boost tilt={tilt:.1f}",
        ))

    # ── 避让人声：窄 Q 微量静态衰减 ─────────────────────────
    # 不用动态 EQ——简单、透明、不引入额外处理痕迹。
    # 衰减量限制在 1–2dB，保持伴奏包裹感。
    if masking_zones:
        for zone in masking_zones:
            cut = min(zone.cut_db * 0.5, 2.0)  # 最多 -2dB
            if cut >= 0.5:
                bands.append(EqBandIntent(
                    band_type="bell",
                    freq_hz=zone.freq_hz,
                    gain_db=-cut,
                    q=zone.q,
                    reason=f"Duck {zone.zone_type}@{zone.freq_hz:.0f}Hz "
                           f"-{cut:.1f}dB masking={zone.masking_ratio_db:.1f}dB",
                ))

    return bands
