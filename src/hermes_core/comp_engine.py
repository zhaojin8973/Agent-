"""
压缩器参数引擎 — 从信号分析推导压缩器物理参数。

从 engine.py 提取的模块级辅助函数。
"""

import bisect

from hermes_core.loudness_optimizer import CompressionIntent
from hermes_core.genre_tables import (
    _GENRE_CLA76_ATTACK_BASE,
    _GENRE_CLA76_ATTACK_K,
    _CLA76_ATTACK_KNOB_MIN,
    _CLA76_ATTACK_KNOB_MAX,
    _GENRE_CREST_GR_RATIO,
    _CLA76_ATTACK_MS_TABLE,
    _CLA76_RELEASE_MS_TABLE,
    _GENRE_RVOX_MULTIPLIER,
)


def _compute_cla76_attack_knob(crest_db: float, genre: str = "pop") -> float:
    """Continuous CLA-76 attack knob from crest factor and genre.

    ``attack_knob = base - (crest - 10) × k``, clamped to
    [``_CLA76_ATTACK_KNOB_MIN``, ``_CLA76_ATTACK_KNOB_MAX``].

    Higher crest → slower attack (smaller knob) to preserve transients.
    """
    base = _GENRE_CLA76_ATTACK_BASE.get(genre, 4.0)
    k = _GENRE_CLA76_ATTACK_K.get(genre, 0.10)
    knob = base - (crest_db - 10.0) * k
    return round(max(_CLA76_ATTACK_KNOB_MIN, min(_CLA76_ATTACK_KNOB_MAX, knob)), 2)


def _derive_compressor_intent(
    rms_db: float, peak_db: float, *, genre: str = "pop"
) -> CompressionIntent:
    """Derive compression targets from Crest Factor (Peak – RMS).

    ==============  ============  ============================
    Crest Factor    Amount        Typical material
    ==============  ============  ============================
    ≥ 15 dB         ``"heavy"``   Folk ballad, classical vocal
    10–15 dB        ``"medium"``  Pop vocal, rock vocal
    < 10 dB         ``"light"``   Pre-compressed, synth, EDM
    ==============  ============  ============================

    *gr_target_db* is genre-aware — transparent genres (folk, ballad)
    use a lighter crest multiplier (0.10) while dense genres
    (electronic) use 0.20.  This keeps per-track compression aligned
    with the bus compressor's genre-based GR target.
    """
    crest = peak_db - rms_db
    ratio = _GENRE_CREST_GR_RATIO.get(genre, 0.15)

    if crest >= 15.0:
        amount = "heavy"
        gr_target = round(crest * ratio, 1)
    elif crest >= 10.0:
        amount = "medium"
        gr_target = round(crest * ratio, 1)
    else:
        amount = "light"
        gr_target = round(crest * ratio, 1)

    return CompressionIntent(
        amount=amount,
        gr_target_db=gr_target,
        crest_factor_db=round(crest, 1),
        rms_db=round(rms_db, 1),
        peak_db=round(peak_db, 1),
    )


def _apply_vca_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """VCA / digital compressor → physical parameter dict.

    Threshold is placed so that the signal's peak exceeds it by
    *gr_target_db* — the compressor catches the transient and
    reduces it by the target amount.
    """
    threshold = intent.peak_db - intent.gr_target_db
    ratio = {
        "light":  2.0,
        "medium": 4.0,
        "heavy":  8.0,
    }.get(intent.amount, 4.0)

    return {
        "Threshold":   round(threshold, 1),
        "Ratio":       ratio,
        "Attack":      preset["attack_ms"],
        "Release":     preset["release_ms"],
        # 0.6: conservative makeup gain coefficient (60% of GR)
        #      prevents over-compensation while restoring perceived loudness
        "Makeup Gain": round(intent.gr_target_db * 0.6, 1),
    }


def _apply_fet_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """FET compressor (1176-style) → physical parameter dict.

    The Input knob sets an *equivalent threshold* — we compute the
    threshold from peak + target GR, then let the normalisation layer
    reverse-lookup the knob position via the calibration table.
    """
    threshold = intent.peak_db - intent.gr_target_db
    return {
        "Input":    round(threshold, 1),
        "Output":   round(intent.gr_target_db * 0.5, 1),
        "Attack":   preset["attack_ms"],
        "Release":  preset["release_ms"],
    }


def _apply_cla76_params(intent: CompressionIntent,
                        attack_knob: float,
                        release_knob: float | None = None) -> dict[str, float]:
    r"""CLA-76 (Waves) physical parameter dict.

    CLA-76 (1176-style FET) has a **fixed internal threshold**.
    *Input* drives signal into that threshold.  *Output* attenuates
    to balance the boosted uncompressed signal.

    *attack_knob* is the CLA-76 knob position (1–7, CW=fast) computed
    from crest + genre via :func:`_compute_cla76_attack_knob`.

    *release_knob* is the BPM-derived knob position (1–7).  When
    ``None`` the release parameter is not included in the output
    (plugin default is left untouched).
    """
    gr = intent.gr_target_db
    peak = intent.peak_db

    # Calibrated on vocal (望归, crest≈20, peak≈-0.4, 2026-05-31).
    # Input positions signal relative to 1176 fixed threshold.
    # Higher peak → less Input needed (signal already near threshold).
    # More GR → more Input needed (push harder into threshold).
    #
    # Formula: input_db = BASELINE + (gr * SLOPE) - peak
    #   -40.4: baseline offset at -18 dBFS RMS (0 VU reference)
    #   0.8:   empirical slope from linear regression on calibration data
    #          (Input vs GR at fixed peak level)
    input_db = -40.4 + gr * 0.8 - peak
    input_db = max(-48.0, min(0.0, input_db))

    # Output: level-match — keep signal roughly unity through the 76
    # 3.25: empirical makeup gain coefficient (dB output per dB GR)
    #       tuned to compensate for perceived loudness loss
    output_db = -gr * 3.25
    output_db = max(-48.0, min(0.0, output_db))

    physical = {
        "Input":  round(input_db, 1),
        "Output": round(output_db, 1),
        "Attack": attack_knob,
    }
    if release_knob is not None:
        physical["Release"] = release_knob
    return physical


def _apply_opto_params(intent: CompressionIntent,
                        preset: dict[str, float]) -> dict[str, float]:
    """Optical compressor (LA-2A style) → physical parameter dict."""
    return {
        "Peak Reduction": round(intent.gr_target_db, 1),
        "Gain":           round(intent.gr_target_db * 0.4, 1),
    }


def _apply_rvox_params(intent: CompressionIntent,
                        preset: dict[str, float],
                        rvox_multiplier: float = 1.0) -> dict[str, float]:
    """Waves RVox → physical parameter dict.

    RVox is a single-fader dynamic processor with fixed internal
    ceiling (0 dBFS).  The Compression fader combines threshold,
    auto-ratio, and auto make-up gain into one control.

    Body compression = CLA-76 GR × *rvox_multiplier*.  Since CLA-76
    already grabbed the peaks, RVox focuses on body/RMS consistency.
    Dense genres use >1.0 for more body control; sparse genres use
    lower values.

    Calibration (望归 Vocal, 2026-05-31):
      Comp  →  GR_peak  (3 data points, 1:1 linear relationship)
      -12.3  →  -12 dB
      -6.0   →  -6 dB
      -3.0   →  -2.5 dB

    Level-match: Gain = Comp × 0.6 (verified by A/B bypass).
    Gate is a gentle downward expander, defaulting to off.
    """
    # Compression: genre-scaled body targeting.
    compression_db = -intent.gr_target_db * rvox_multiplier
    compression_db = max(-36.0, min(0.0, compression_db))

    # Gain: level-match — prevent auto-gain loudness from masking compression.
    # Coeff 0.6 is the user-verified sweet spot.
    gain_db = compression_db * 0.6
    gain_db = max(-36.0, min(0.0, gain_db))

    # Gate: off by default (-120 dB ≈ -Inf).  Signal analysis does not yet
    # produce a noise-floor estimate to justify an automatic gate.
    gate_db = -120.0

    return {
        "Compression": round(compression_db, 1),
        "Gate":        round(gate_db, 1),
        "Gain":        round(gain_db, 1),
    }


def _ms_to_cla76_attack(ms: float) -> float:
    """Convert attack time (ms) to CLA-76 knob position (1−7, CW=fast)."""
    return _lookup_ms_table(ms, _CLA76_ATTACK_MS_TABLE)


def _ms_to_cla76_release(ms: float) -> float:
    """Convert release time (ms) to CLA-76 knob position (1−7, CW=fast)."""
    return _lookup_ms_table(ms, _CLA76_RELEASE_MS_TABLE)


def _lookup_ms_table(ms: float, table: list[tuple[float, float]]) -> float:
    """Bisect *table* (sorted by ms) and return the knob position.

    Values outside the table range are clamped to the nearest endpoint.
    """
    ms_list = [row[0] for row in table]
    if ms <= ms_list[0]:
        return table[0][1]
    if ms >= ms_list[-1]:
        return table[-1][1]
    idx = bisect.bisect_left(ms_list, ms)
    # Interpolate between idx-1 and idx
    lo_ms, lo_knob = table[idx - 1]
    hi_ms, hi_knob = table[idx]
    t = (ms - lo_ms) / (hi_ms - lo_ms)
    return lo_knob + t * (hi_knob - lo_knob)
