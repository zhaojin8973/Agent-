"""
EQ 参数引擎 — 从频谱分析推导 EQ 物理参数。

从 engine.py 提取的模块级辅助函数。
"""

import bisect
import math

from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
from hermes_core.spectrum import SpectrumReport
from hermes_core.genre_tables import (
    _GENRE_EQ_TWEAKS,
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


def _derive_eq_intent(
    report: SpectrumReport,
    role: str = "vocal",
    genre: str = "pop",
    position: str = "solo",
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
    _POSITIONS = ("pre", "post", "solo")
    if position not in _POSITIONS:
        raise ValueError(f"position must be one of {_POSITIONS}, got {position!r}")

    # All positions run the full rule set.
    # Pre-comp is conservative on boosts: higher threshold, lower gain.
    conservative = position == "pre"

    tweaks = _GENRE_EQ_TWEAKS.get(genre, _GENRE_EQ_TWEAKS["default"])
    bands: list[EqBandIntent] = []

    # ── Rule 1: HPF ─────────────────────────────────────────
    sub_energy = report.band_energy_db.get("sub", -60.0)
    mid_energy = report.band_energy_db.get("mid", -60.0)
    sub_excess = sub_energy - mid_energy

    # HPF frequency selection based on sub-bass energy relative to midrange
    # 3.0 dB: threshold for "excessive" sub-bass (triggers HPF raise)
    # 10 Hz/dB: slope - how aggressively to raise HPF as sub-bass increases
    # 80/120 Hz (vocal) and 40/80 Hz (backing): safe HPF limits
    #   - vocal: 80 Hz default, max 120 Hz (avoid cutting fundamental)
    #   - backing: 40 Hz default, max 80 Hz (preserve low-end instruments)
    if role == "vocal":
        hpf_freq = 80.0
        if sub_excess > 3.0:
            hpf_freq = min(120.0, 80.0 + (sub_excess - 3.0) * 10)
    else:
        hpf_freq = 40.0
        if sub_excess > 3.0:
            hpf_freq = min(80.0, 40.0 + (sub_excess - 3.0) * 10)

    bands.append(EqBandIntent(
        band_type="hp", freq_hz=round(hpf_freq, 1), gain_db=0.0,
        q=0.7,
        reason=f"HPF@{hpf_freq:.0f}Hz sub_excess={sub_excess:.1f}dB",
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
    if report.mud_ratio_db > mud_threshold:
        cut_db = -min(report.mud_ratio_db - 2.0, 4.0)
        cut_db = max(cut_db, -4.0)
        bands.append(EqBandIntent(
            band_type="bell", freq_hz=350.0, gain_db=round(cut_db, 1),
            q=0.7,
            reason=f"Mud cut@{350}Hz mud_ratio={report.mud_ratio_db:.1f}dB",
        ))

    # ── Rule 4: Presence boost ──────────────────────────────
    # Pre-comp: higher threshold (4 dB deficit) to be conservative.
    # Post-comp: lower threshold (2 dB) since compression can reduce presence.
    #
    # 4.0 / 2.0 dB: presence deficit thresholds (dB below midrange)
    # 0.5: boost coefficient (50% of deficit, conservative correction)
    # 3.0 dB: maximum boost cap (avoid over-EQing)
    # 3000 Hz: presence band center frequency (vocal intelligibility region)
    presence_deficit_threshold = 4.0 if conservative else 2.0
    if report.presence_deficit_db > presence_deficit_threshold:
        boost = min(report.presence_deficit_db * 0.5, 3.0)
        boost += tweaks.get("presence_extra_db", 0.0)
        boost *= tweaks.get("boost_scale", 1.0)
        if conservative:
            boost *= 0.5  # pre-comp boosts at half strength
        bands.append(EqBandIntent(
            band_type="bell", freq_hz=3000.0, gain_db=round(boost, 1),
            q=1.0,
            reason=f"Presence boost@{3000}Hz deficit={report.presence_deficit_db:.1f}dB",
        ))

    # ── Rule 5: Air shelf ───────────────────────────────────
    # Graduated: severe tilt + moderately low air deserves air too.
    # Pre-comp: thresholds are stricter (air < -35, tilt < -5).
    if conservative:
        air_low = report.air_level_db < -35.0
        air_moderate = report.air_level_db < -28.0
        tilt_dark = report.spectral_tilt_db_per_octave < -5.0
        tilt_very_dark = report.spectral_tilt_db_per_octave < -6.5
        air_gain_scale = 0.5
    else:
        air_low = report.air_level_db < -30.0
        air_moderate = report.air_level_db < -22.0
        tilt_dark = report.spectral_tilt_db_per_octave < -3.0
        tilt_very_dark = report.spectral_tilt_db_per_octave < -4.5
        air_gain_scale = 1.0

    air_gain = 0.0
    if air_low and tilt_dark:
        air_gain = 1.5  # both severe: full boost
    elif tilt_very_dark and air_moderate:
        air_gain = 1.0  # very dark + moderately low air
    elif air_low and tilt_very_dark:
        air_gain = 1.5  # severe air loss + very dark

    if air_gain > 0.0:
        air_gain *= tweaks.get("boost_scale", 1.0)
        air_gain *= air_gain_scale
        bands.append(EqBandIntent(
            band_type="high_shelf", freq_hz=8000.0, gain_db=round(air_gain, 1),
            q=0.7,
            reason=f"Air shelf@8kHz air={report.air_level_db:.1f}dB tilt={report.spectral_tilt_db_per_octave:.1f}dB/oct",
        ))

    # ── Assemble ─────────────────────────────────────────────
    # Cap at 8 bands (Pro-Q 3 limit).  Priority order:
    #   1. HPF (always included — structural necessity)
    #   2. Resonance cuts (most prominent first)
    #   3. Tonal balance (mud → presence → air)
    hpf_bands = [b for b in bands if b.band_type == "hp"]
    reso_bands = [b for b in bands if b.band_type == "bell" and b.gain_db < -2.0]
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

    _SLOPE_12DB = 1.0 / 9.0    # 12 dB/oct (10 values 0–9, index 1)

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
            params[f"Band {n} Slope"] = _SLOPE_12DB

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
