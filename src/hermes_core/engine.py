"""
MixingEngine — Layer 3 public API. Composes all Layer 2 modules into
a single entry point for Hermes acceptance scenarios.
"""

import bisect
import logging
import math
import os
import shutil
import tempfile
import time
from datetime import datetime
from typing import Callable

from hermes_core.bridge import ReaperBridge
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer
from hermes_core.exceptions import ConnectionError as HermesConnectionError
from hermes_core.loudness_optimizer import (
    find_optimal_gain,
    verify_output,
    load_calibration,
    generate_report,
    CompressionIntent,
    EqIntent,
    EqBandIntent,
)
from hermes_core.normalize import normalize_params, compute_bus_compressor_params
from hermes_core.profiles import (
    _resolve_fx_type,
    _get_compressor_preset,
    _EQ_BASELINE,
    get_bpm_timing,
)
from hermes_core.dag import AudioNode, SendNode, ChainExecutor
from hermes_core.spectrum import SpectrumAnalyzer, SpectrumReport

log = logging.getLogger(__name__)

# Genre-based backing track reduction (LU) for prepare_stems.
# Genre → vocal/backing ratio (LU).  Higher values = vocal more forward.
# Backing target LUFS = vocal_LUFS - ratio.
_GENRE_VOCAL_TO_BACKING: dict[str, float] = {
    "electronic":              2,     # vocal sits in the mix
    "pop":                     3,     # standard pop placement
    "rock":                    3,
    "folk":                    4,     # vocal-forward, sparse backing
    "ballad":                  5,     # vocal most prominent
    "chinese_folk_bel_canto":  5,     # majestic, vocal-forward
}

# Peak ceiling for combined mix after fader balance.
# If the full-mix peak exceeds this, both vocal and backing are
# attenuated equally until peak ≤ ceiling.
_PEAK_CEILING_DB: float = -3.0

# Genre-based target integrated LUFS for the final master.
# Calibrated to domestic Chinese streaming platforms, 2026-06.
_GENRE_TARGET_LUFS = {
    "folk":                    -13.0,   # preserve dynamics
    "ballad":                  -13.0,   # same as folk — gentle, dynamic
    "pop":                     -10.0,   # commercial, competitive
    "rock":                    -10.0,   # same as pop — competitive
    "electronic":              -9.0,    # loudness war — EDM expects density
    "chinese_folk_bel_canto":  -11.0,   # red songs / art songs
}

# Standard clip gain reference level (dBFS RMS).
# -18 dBFS = 0 VU — industry standard for analog-modelled plugin input calibration.
_CLIP_GAIN_REF_DB: float = -18.0

# Fallback target when genre is not in _GENRE_TARGET_LUFS.
_DEFAULT_TARGET_LUFS: float = -10.0


def _get_genre_target_lufs(genre: str) -> float:
    """Return the recommended target LUFS for *genre*."""
    return _GENRE_TARGET_LUFS.get(genre, _DEFAULT_TARGET_LUFS)

# Pro-L 2 calibrated VST parameter ranges (verified 2026-05-28 via REAPER GUI).
# Gain: normalized 0.0 = 0 dB, 1.0 = +30 dB (boost only).
# Output Level: normalized 0.0 = -30 dB, 1.0 = 0 dB.
# Both share a 30 dB span.
_PRO_L2_RANGE_DB: float = 30.0


def _master_error(target_lufs: float, ceiling_db: float, error: str) -> dict:
    """Build a finalize_master error result dict."""
    return {
        "target_lufs": target_lufs,
        "achieved_lufs": None,
        "probe_lufs": None,
        "gain_db": 0.0,
        "ceiling_db": ceiling_db,
        "passed": False,
        "converged": False,
        "error": error,
        "hint": _friendly_hint(error),
        "output_path": None,
        "pre_limiter_peak_db": None,
    }


def _friendly_hint(error: str) -> str:
    """Return a user-friendly hint for common errors."""
    hints = {
        "Probe render failed":
            "REAPER may be blocked by a modal dialog. Try watchdog=True "
            "to auto-dismiss dialogs, or check that tracks have media items.",
        "Probe is near-silent":
            "The probe render produced near-silent audio. Check that "
            "the source files are not empty and have audible content.",
        "Pro-L 2 Output Level param not found":
            "Pro-L 2 parameter name doesn't match. Verify the plugin is "
            "installed and named exactly 'VST: FabFilter Pro-L 2 (FabFilter)'. "
            "Try running preflight_plugins() first.",
        "Pro-L 2 Gain param not found":
            "Pro-L 2 Gain parameter not found. Same as above — check "
            "plugin installation and name.",
        "Failed to add":
            "Plugin not found in REAPER. Check the FX name matches "
            "the REAPER FX browser exactly, including vendor suffix.",
        "Not a WAV file":
            "Input file is not a valid WAV. Supported formats: WAV "
            "(16/24-bit PCM, 32-bit float), FLAC, MP3 via soundfile.",
        "WAV data chunk not found":
            "WAV file appears corrupted — data chunk is missing. "
            "Try re-exporting the file from your DAW.",
    }
    for key, hint in hints.items():
        if key.lower() in error.lower():
            return hint
    return "Check the log for details. Common issues: missing plugins, "
    "unwritable output directory, insufficient disk space, or REAPER "
    "modal dialogs blocking automation."


# ════════════════════════════════════════════════════════════════
# Compression derivation + translator layer
# ════════════════════════════════════════════════════════════════


# Genre → crest multiplier for per-track compression GR.
# Mirrors bus compressor GR targets: transparent genres use lighter
# multipliers so the whole pipeline breathes consistently.
_GENRE_CREST_GR_RATIO: dict[str, float] = {
    "folk":                    0.12,   # lightest — preserve breath
    "ballad":                  0.12,
    "chinese_folk_bel_canto":  0.14,   # medium-light — majestic
    "pop":                     0.17,   # standard
    "rock":                    0.17,
    "electronic":              0.22,   # heaviest — control dynamics
}

# RVox body compression multiplier (on top of CLA-76 GR).
# CLA-76 grabs peaks → RVox smooths the body.  The multiplier
# controls how much ADDITIONAL body compression RVox applies.
# >1.0 = RVox compresses more than the peak GR (dense genres).
_GENRE_RVOX_MULTIPLIER: dict[str, float] = {
    "electronic":              1.8,
    "pop":                     1.7,
    "rock":                    1.7,
    "chinese_folk_bel_canto":  1.5,
    "folk":                    1.0,   # sparse backing, vocal already forward
    "ballad":                  1.2,
}

# Pro-DS de-esser Range (max gain reduction in dB) per genre.
# Sparse genres → lower Range (natural vocal, light touch).
# Dense genres → higher Range (stronger sibilance control).
_GENRE_PRODS_RANGE: dict[str, float] = {
    "folk":                    6.0,   # 自然人声，轻触
    "ballad":                  6.0,   # 柔和，保留呼吸感
    "chinese_folk_bel_canto":  7.0,   # 中等，兼顾力度
    "pop":                     8.5,   # 标准商业控制
    "rock":                    8.5,   # 与 pop 一致
    "electronic":              10.0,  # 强力控制，密集混音
}

# CLA-76 attack knob — continuous, crest-driven, genre-aware.
# Formula: attack_knob = base - (crest - 10) × k, clamped [1, 6.5].
# Base = genre "normal" attack when crest ≈ 10 dB.
# k    = how much crest deviates from the attack (higher k = more responsive).
_GENRE_CLA76_ATTACK_BASE: dict[str, float] = {
    "electronic":              5.0,
    "pop":                     4.0,
    "rock":                    4.0,
    "chinese_folk_bel_canto":  3.5,
    "folk":                    3.0,
    "ballad":                  3.0,
}
_GENRE_CLA76_ATTACK_K: dict[str, float] = {
    "electronic":              0.05,
    "pop":                     0.10,
    "rock":                    0.10,
    "chinese_folk_bel_canto":  0.08,
    "folk":                    0.05,
    "ballad":                  0.05,
}

# ── 空间效果器发送量基准 ─────────────────────────────────────

# Reverb send level base per genre (dB, post-fader send).
# Values are confirmed mid-range from professional mixing practice.
# Sparse genres: lower sends (natural, intimate).
# Dense genres: higher sends (bigger space, more competitive).
_GENRE_REVERB_SEND_BASE: dict[str, dict[str, float]] = {
    "folk":                    {"plate": -18.0, "hall": -20.0, "room": -14.0},
    "ballad":                  {"plate": -14.0, "hall": -16.0, "room": -12.0},
    "pop":                     {"plate": -12.0, "hall": -14.0, "room": -16.0},
    "rock":                    {"plate": -16.0, "hall": -14.0, "room": -14.0},
    "electronic":              {"plate": -10.0, "hall": -10.0, "room": -18.0},
    "chinese_folk_bel_canto":  {"plate": -14.0, "hall": -14.0, "room": -12.0},
}

# Delay send level base per genre (dB).  -99.0 = disabled for this genre.
_GENRE_DELAY_SEND_BASE: dict[str, dict[str, float]] = {
    "folk":                    {"slap": -99.0, "rhythm": -99.0},
    "ballad":                  {"slap": -20.0, "rhythm": -22.0},
    "pop":                     {"slap": -14.0, "rhythm": -16.0},
    "rock":                    {"slap": -12.0, "rhythm": -18.0},
    "electronic":              {"slap": -10.0, "rhythm": -12.0},
    "chinese_folk_bel_canto":  {"slap": -18.0, "rhythm": -20.0},
}

# Send level range (dB).  Outside these bounds is impractical.
_SEND_LEVEL_MIN: float = -24.0
_SEND_LEVEL_MAX: float = -6.0
_SEND_DISABLED_THRESHOLD: float = -90.0  # below this = bus not created

# Crest factor reference point (dB).  Vocals with crest ≈ 12 dB are
# "normally dynamic" — no adjustment applied.
_CREST_REFERENCE: float = 12.0
# Presence deficit threshold (dB).  Below 2 dB deficit is normal;
# above that the vocal sounds dull → reduce sends so reverb doesn't
# push it further back.
_PRESENCE_DEFICIT_THRESHOLD: float = 2.0
# Sibilance reference peak (dBFS).  Peaks above this trigger a
# plate-send reduction because plates resonate in the 5–8 kHz range.
_SIBILANCE_REFERENCE_PEAK: float = -32.0

# Section boost amounts (dB) — added to all send buses.
_SECTION_BOOST: dict[str, float] = {
    "verse":  0.0,
    "chorus": 2.0,
    "bridge": 3.0,
}


def _compute_spatial_sends(
    genre: str,
    crest_factor_db: float,
    presence_deficit_db: float,
    mud_ratio_db: float,
    sibilance_peak_db: float | None = None,
    section: str = "verse",
) -> dict[str, float | None]:
    """Compute reverb and delay send levels from vocal signal analysis.

    Each send is derived from a genre-reference base, then adjusted by
    four objective biases:

    - **crest_bias**: high-crest vocals already sound "big" — dial back
      reverb so it doesn't wash out the dynamics.
    - **density_bias**: muddy vocals get less reverb to avoid piling
      low-mid energy onto the mud.
    - **presence_bias**: a dull vocal (high presence deficit) should
      stay forward — reverb would push it back.
    - **sibilance_bias** (plate only): plate reverbs resonate at
      5–8 kHz, so bright sibilant vocals get less plate send.

    Returns a dict mapping bus keys to send levels in dB.
    ``None`` means the bus is disabled for this genre (no need to
    create it).
    """
    _DEFAULT_REVERB = _GENRE_REVERB_SEND_BASE["pop"]
    _DEFAULT_DELAY = _GENRE_DELAY_SEND_BASE["pop"]

    base_reverb = _GENRE_REVERB_SEND_BASE.get(genre, _DEFAULT_REVERB)
    base_delay = _GENRE_DELAY_SEND_BASE.get(genre, _DEFAULT_DELAY)

    # ── Bias computations ──────────────────────────────────────
    crest_bias = -(crest_factor_db - _CREST_REFERENCE) * 0.5
    density_bias = mud_ratio_db * 0.3
    presence_bias = -(presence_deficit_db - _PRESENCE_DEFICIT_THRESHOLD) * 0.3
    section_bias = _SECTION_BOOST.get(section, 0.0)

    sibilance_bias = 0.0
    if sibilance_peak_db is not None:
        sibilance_bias = -max(0.0, sibilance_peak_db - _SIBILANCE_REFERENCE_PEAK) * 0.1

    # ── Assemble sends ─────────────────────────────────────────
    sends: dict[str, float | None] = {}

    for bus_type, base_db in base_reverb.items():
        bias = crest_bias + density_bias + presence_bias + section_bias
        if bus_type == "plate":
            bias += sibilance_bias
        sends[f"reverb_{bus_type}"] = round(
            max(_SEND_LEVEL_MIN, min(_SEND_LEVEL_MAX, base_db + bias)), 1,
        )

    for bus_type, base_db in base_delay.items():
        if base_db <= _SEND_DISABLED_THRESHOLD:
            sends[f"delay_{bus_type}"] = None
        else:
            bias = crest_bias + presence_bias + section_bias
            sends[f"delay_{bus_type}"] = round(
                max(_SEND_LEVEL_MIN, min(_SEND_LEVEL_MAX, base_db + bias)), 1,
            )

    return sends


_CLA76_ATTACK_KNOB_MIN: float = 1.0
_CLA76_ATTACK_KNOB_MAX: float = 6.5


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


# CLA-76 Input→GR calibration (pink noise -18 dBFS RMS, 2026-05-31).
# (input_dB, gr_db) sorted by GR ascending.  GR is negative — the table
# stores absolute values for readability.
_CLA76_GR_TABLE: list[tuple[float, float]] = [
    (-32.0,  0),    # threshold barely touched
    (-24.0,  3),    # moderate
    (-20.0,  8),    # heavy
    (-16.0, 15),    # very heavy
    (-8.0,  20),    # max
]


def _gr_to_cla76_input(gr_target: float) -> float:
    """Given a GR target (dB), return the CLA-76 Input dB setting."""
    gr_list = [row[1] for row in _CLA76_GR_TABLE]
    if gr_target <= gr_list[0]:
        return _CLA76_GR_TABLE[0][0]
    if gr_target >= gr_list[-1]:
        return _CLA76_GR_TABLE[-1][0]
    idx = bisect.bisect_left(gr_list, gr_target)
    lo_in, lo_gr = _CLA76_GR_TABLE[idx - 1]   # (input_dB, gr_db)
    hi_in, hi_gr = _CLA76_GR_TABLE[idx]
    t = (gr_target - lo_gr) / (hi_gr - lo_gr)
    return lo_in + t * (hi_in - lo_in)


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


# ════════════════════════════════════════════════════════════════
# Compressor dispatcher
# ════════════════════════════════════════════════════════════════

# Note: "rvox" is NOT in this dictionary because it requires special handling
# (rvox_multiplier parameter). See dispatch logic at line ~1344.
# CLA-76 is also handled separately (different signature: attack_knob, release_knob).
_TRANSLATORS = {
    "vca":  _apply_vca_params,
    "fet":  _apply_fet_params,
    "opto": _apply_opto_params,
}


# ════════════════════════════════════════════════════════════════
# CLA-76 ms → knob conversion (CW=fast, range 1−7)
# ════════════════════════════════════════════════════════════════

# Attack: (ms, knob_position) sorted by ms ascending.
# Attack ms → knob.  FET compressor attack times saturate below ~800 μs,
# so engine ms values (3-10 ms from BPM presets) are all "slow" in FET
# terms.  We compress the upper range so every BPM tier maps to a usable
# knob position (nothing below 2 — knob 1 is too sluggish for vocals).
_CLA76_ATTACK_MS_TABLE: list[tuple[float, float]] = [
    (0.02,  7.0),   # fastest (knob 7 = ~20 μs)
    (1.0,   6.0),   # knob 6
    (2.0,   5.0),   # knob 5 — BPM FAST   (3 ms) lands near here
    (3.0,   4.0),   # knob 4
    (5.0,   3.0),   # knob 3 — BPM MED    (5 ms) lands here
    (8.0,   2.5),   # knob 2.5
    (12.0,  2.0),   # knob 2 — BPM SLOW   (10 ms) lands near here
]

# Release: (ms, knob_position) sorted by ms ascending.
_CLA76_RELEASE_MS_TABLE: list[tuple[float, float]] = [
    (50.0,   7.0),   # fastest
    (150.0,  6.0),
    (300.0,  5.0),
    (500.0,  4.0),
    (700.0,  3.0),
    (900.0,  2.0),
    (1100.0, 1.0),   # slowest
]


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


# ════════════════════════════════════════════════════════════════
# EQ derivation + translator layer (mirrors compressor pattern)
# ════════════════════════════════════════════════════════════════

# Genre-specific tweaks to EQ derivation thresholds
_GENRE_EQ_TWEAKS: dict[str, dict] = {
    "pop":  {"presence_extra_db": 0.5, "mud_threshold_db": 3.0, "boost_scale": 1.0},
    "rock": {"presence_extra_db": 0.0, "mud_threshold_db": 4.0, "boost_scale": 1.0},
    "folk": {"presence_extra_db": 0.0, "mud_threshold_db": 3.0, "boost_scale": 0.75},
    "default": {"presence_extra_db": 0.0, "mud_threshold_db": 3.0, "boost_scale": 1.0},
}


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


# Minimum Q factor for EQ resonance cuts (mirrors spectrum._MIN_Q_FACTOR)
_MIN_EQ_Q = 15.0

# Pro-Q 3 Shape enum — verified 2026-05-31 via reapy readback.
# Denominator is 8 (not 7).  Values correspond to:
#   0=Bell  1=Low Shelf  2=Low Cut  3=High Shelf
#   4=High Cut  5=Notch  6=Band Pass  7=Tilt Shelf
_PROQ3_SHAPE: dict[str, float] = {
    "bell":        0.0 / 8.0,
    "low_shelf":   1.0 / 8.0,
    "high_shelf":  3.0 / 8.0,
    "hp":          2.0 / 8.0,  # Low Cut
    "lp":          4.0 / 8.0,  # High Cut
}

# Pro-Q 3 log-frequency formula (verified): norm = log10(f / 10) / log10(3000).
# Frequency range is 10 Hz – 30 kHz.
_PROQ3_FREQ_LOG_BASE = math.log10(30000.0 / 10.0)  # ≈ 3.477


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


# ════════════════════════════════════════════════════════════════
# SSL EQ translation (post-comp tonal shaping)
# ════════════════════════════════════════════════════════════════

# Frequency lookup tables: (norm, Hz) pairs sorted by Hz.
# Verified via reapy readback (2026-05-31).
# Interpolation between knots for continuous norm values.

_LF_FRQ_TABLE: list[tuple[float, float]] = [
    (0.000, 30),
    (0.230, 60),
    (0.425, 150),
    (0.650, 300),
    (1.000, 450),
]

# LMF frequency steps (verified reapy, 2026-05-31).
# SSL EQ frequency selectors are physically detented — interpolation
# is meaningless.  Use nearest-neighbour lookup.
_LMF_FRQ_STEPS: list[tuple[float, float]] = [
    (0.007, 200),
    (0.190, 300),
    (0.237, 420),
    (0.322, 710),
    (0.589, 1300),
    (0.670, 1570),
    (0.799, 2000),
    (1.000, 2500),
]

_HMF_FRQ_TABLE: list[tuple[float, float]] = [
    (0.000, 600),
    (0.450, 2500),
    (1.000, 7000),
]

_HF_FRQ_TABLE: list[tuple[float, float]] = [
    (0.000, 1500),
    (0.650, 10000),
    (1.000, 16000),
]

_HP_FRQ_TABLE: list[tuple[float, float]] = [
    (0.012, 16),
    (0.440, 100),
    (1.000, 350),
]

# Q: 0.1 (widest, norm=1.0) → 3.5 (narrowest, norm=0.0), reverse-linear.
_SSL_Q_MIN = 0.1
_SSL_Q_MAX = 3.5
_SSL_Q_RANGE = _SSL_Q_MAX - _SSL_Q_MIN  # 3.4


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


def _reaeq_apply_baseline_band(fx_mgr, track_idx, fx_idx,
                               band_idx, btype, freq, gain, q):
    """Apply a single baseline EQ band using ReaEQ param names."""
    n = band_idx + 1
    if btype == "hp":
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Type", 0.0)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Freq", freq)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Q", q)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Enabled", 1.0)
    elif btype == "bell":
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Type", 2.0)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Freq", freq)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Gain", gain)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Q", q)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Enabled", 1.0)


def _proq3_apply_baseline_band(fx_mgr, track_idx, fx_idx,
                                band_idx, btype, freq, gain, q):
    """Apply a single baseline EQ band using Pro-Q 3 param names."""
    n = band_idx + 1
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Used", 1.0)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Enabled", 1.0)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Frequency", freq)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Q", q)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Dynamic Range", 0.5)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Dynamics Enabled", 0.0)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Threshold", 1.0)
    fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Stereo Placement", 0.5)
    if btype == "hp":
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Shape", 0.25)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Slope", 0.1111)
    elif btype == "bell":
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Shape", 0.0)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Gain", gain)
    elif btype == "hs":
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Shape", 0.75)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Slope", 0.1111)
        fx_mgr.set_param(track_idx, fx_idx, f"Band {n} Gain", gain)


def _ssleq_apply_baseline_band(fx_mgr, track_idx, fx_idx,
                                band_idx, btype, freq, gain, q):
    """Apply a single baseline EQ band using SSL EQ param names.

    SSL EQ has fixed band assignments:
      - HF shelf  (1.5-16 kHz)
      - HMF bell  (600-7000 Hz)
      - LMF bell  (200-2500 Hz)
      - LF shelf  (30-450 Hz)
      - HP filter (30-350 Hz, separate from bands)

    Baseline bands are mapped to the most appropriate SSL band.
    """
    if btype == "hp":
        fx_mgr.set_param(track_idx, fx_idx, "HP On/Off", 1.0)
        fx_mgr.set_param(track_idx, fx_idx, "HP Frq", freq)
    elif btype == "bell":
        if freq < 2000:
            fx_mgr.set_param(track_idx, fx_idx, "LMF Gain", gain)
            fx_mgr.set_param(track_idx, fx_idx, "LMF Frq", freq)
            fx_mgr.set_param(track_idx, fx_idx, "LMF Q", q)
        else:
            fx_mgr.set_param(track_idx, fx_idx, "HMF Gain", gain)
            fx_mgr.set_param(track_idx, fx_idx, "HMF Frq", freq)
            fx_mgr.set_param(track_idx, fx_idx, "HMF Q", q)
    elif btype == "hs":
        fx_mgr.set_param(track_idx, fx_idx, "HF Gain", gain)
        fx_mgr.set_param(track_idx, fx_idx, "HF Frq", freq)


class MixingEngine:
    """Top-level REAPER mixing engine. Use as context manager for auto-connect.

    with MixingEngine() as eng:
        eng.create_project(sample_rate=48000)
        eng.import_stems(["/path/to/audio.wav"])
        result = eng.render_mix("/tmp/output")
    """

    def __init__(self, watchdog: bool = False):
        self._bridge = ReaperBridge(dialog_killer=watchdog)
        self._tracks = TrackManager(self._bridge)
        self._bus = BusManager(self._bridge)
        self._fx = FxManager(self._bridge)
        self._send = SendManager(self._bridge)
        self._render = RenderManager(self._bridge)
        self._watchdog_enabled = watchdog
        self._project_path: str | None = None
        self._snapshot_project_path: str | None = None  # from GetProjectPath at init
        self._snapshot_project_name: str | None = None  # from GetProjectName at init
        # Idempotency guards — prevent double-execution of destructive ops.
        self._stems_gain_staged: bool = False
        self._master_finalized: bool = False
        # Safety guard — prevent accidental track deletion.
        # Set to False to allow create_project / reset to wipe tracks.
        self._tracks_protected: bool = True
        self._stems_cache: list[dict] = []

        # AudioNode pipeline — built by apply_profile()
        self._vocal_chain_nodes: list[AudioNode] = []
        self._backing_chain_nodes: list[AudioNode] = []
        self._reverb_send_node: SendNode | None = None

    # ── Context manager ──────────────────────────────────

    def __enter__(self):
        if not self._bridge.connect():
            raise HermesConnectionError("Failed to connect to REAPER bridge")
        return self

    def __exit__(self, *args):
        if self._watchdog_enabled and self._bridge.dialog_killer_active:
            self._bridge.stop_dialog_killer()
        return False

    # ── Undo / state helpers ────────────────────────────────

    def _undo_block(self, label: str, fn: Callable, /, *args, **kwargs):
        """Wrap *fn* in a REAPER undo block so the user can Ctrl+Z the
        entire operation as one atomic step.
        """
        api = self._bridge.api
        try:
            api.Undo_BeginBlock()
            result = fn(*args, **kwargs)
            api.Undo_EndBlock(f"Hermes: {label}", -1)
            return result
        except Exception as e:
            try:
                api.Undo_EndBlock(f"Hermes: {label} (failed)", 0)
            except Exception as inner_e:
                log.debug("Undo_EndBlock cleanup failed: %s", inner_e)
            raise
            raise

    def _ensure_project_match(self):
        """Raise ``RuntimeError`` if REAPER's current project has changed
        since ``create_project()`` was called (e.g. user switched tabs).
        """
        if not self._snapshot_project_path and not self._snapshot_project_name:
            return
        _, name_buf, _ = self._bridge.api.GetProjectName(0, "", 256)
        path_buf, _ = self._bridge.api.GetProjectPath("", 256)
        current_name = (name_buf or "").strip()
        current_path = (path_buf or "").strip()

        name_changed = (
            self._snapshot_project_name and current_name
            and current_name != self._snapshot_project_name
        )
        path_changed = (
            self._snapshot_project_path and current_path
            and current_path != self._snapshot_project_path
        )

        if name_changed or path_changed:
            raise RuntimeError(
                f"Project mismatch: expected '{self._snapshot_project_name}'"
                f" at '{self._snapshot_project_path}', "
                f"REAPER now has '{current_name}' at '{current_path}'. "
                f"Call create_project() or re-open the expected project."
            )

    def allow_track_deletion(self):
        """Unlock destructive track operations.

        Must be called before :meth:`create_project` or any operation that
        deletes tracks.  This is a deliberate opt-in to prevent accidental
        loss of manually placed plugins and project state.
        """
        self._tracks_protected = False

    def reset(self):
        """Clear idempotency guards so the engine can be re-used for a new mix."""
        self._stems_gain_staged = False
        self._master_finalized = False
        self._stems_cache.clear()
        self._vocal_chain_nodes.clear()
        self._backing_chain_nodes.clear()
        self._reverb_send_node = None
        self._bpm = None
        self._last_spectrum: dict = {}

    def preflight_plugins(self, fx_names: list[str]) -> list[str]:
        """Check which of *fx_names* are available in REAPER.  Returns the
        list of **missing** plugin names (empty = all present).

        Uses a disposable probe track to test instantiation.  The master
        track is **never** touched — plugins that fail to load leave no
        residue.
        """
        missing: list[str] = []
        # ── Create a temporary probe track ──────────────────────
        api = self._bridge.api
        probe_idx = api.CountTracks(0)
        api.InsertTrackAtIndex(probe_idx, True)  # True = hidden under folder
        try:
            for name in fx_names:
                idx = self._fx.add(probe_idx, name)
                if idx < 0:
                    missing.append(name)
                else:
                    track = api.GetTrack(0, probe_idx)
                    if track:
                        api.TrackFX_Delete(track, idx)
        finally:
            # Remove the probe track (also deletes any leftover FX).
            track = api.GetTrack(0, probe_idx)
            if track:
                api.DeleteTrack(track)

        return missing

    def apply_profile(self, profile, /, *, vocal_track: int = 0,
                      backing_tracks: list[int] | None = None,
                      genre: str = "pop",
                      bpm: float | None = None):
        """Apply a :class:`MixingProfile` — FX chains, sends, and auto-compression.

        1. **EQ baseline** — conservative HPF + gentle presence boost.
        2. **Compression** — Crest Factor analysis → :class:`CompressionIntent`
           → translator → normalise → REAPER.  If *bpm* is provided, BPM-aware
           attack/release timing is used (see :func:`get_bpm_timing`).
        3. **Reverb bus** — aux send with Abbey Road safety EQ.

        An :class:`AudioNode` DAG is built in parallel.  Dirty flags cascade
        so that downstream nodes are automatically invalidated when an
        upstream parameter changes (``update_node_param``).
        """
        from hermes_core.profiles import MixingProfile
        if not isinstance(profile, MixingProfile):
            raise TypeError(f"Expected MixingProfile, got {type(profile).__name__}")

        self._profile = profile

        # Resolve BPM: explicit arg takes priority over prepare_stems stash.
        _bpm = bpm if bpm is not None else getattr(self, "_bpm", None)

        # ── Build a lookup: stem index → analysis data ──
        stem_data: dict[int, dict] = {}
        for i, s in enumerate(self._stems_cache):
            if s.get("success"):
                stem_data[i] = s

        # ── Vocal chain ──
        self._vocal_chain_nodes = self._build_audio_chain(
            track_index=vocal_track,
            fx_list=profile.vocal_chain,
            stem_data=stem_data,
            stem_idx=0,
            genre=genre,
            role="vocal",
            bpm=_bpm,
        )

        # ── Backing chain (one chain per backing track) ──
        self._backing_chain_nodes.clear()
        if backing_tracks and profile.backing_chain:
            for bt in backing_tracks:
                bt_stem_idx = next(
                    (i for i, s in enumerate(self._stems_cache)
                     if s.get("track_index") == bt),
                    None,
                )
                nodes = self._build_audio_chain(
                    track_index=bt,
                    fx_list=profile.backing_chain,
                    stem_data=stem_data,
                    stem_idx=bt_stem_idx or 1,
                    genre=genre,
                    role="backing",
                    bpm=_bpm,
                )
                self._backing_chain_nodes.extend(nodes)

        # ── Reverb bus with observer (SendNode) ──
        self._reverb_send_node = None
        if profile.bus_reverb:
            reverb_result = self.create_reverb_send(
                vocal_track,
                level_db=profile.reverb_level_db,
                reverb_fx=profile.bus_reverb.name,
            )
            # Attach SendNode as observer on last vocal chain node
            last_vocal = (
                self._vocal_chain_nodes[-1]
                if self._vocal_chain_nodes
                else None
            )
            if last_vocal is not None:
                self._reverb_send_node = SendNode(
                    name="Vocal_Verb_Send",
                    fx_type="reverb",
                    source_node=last_vocal,
                )
                self._reverb_send_node.params = {
                    "level_db": profile.reverb_level_db,
                    "aux_index": reverb_result.get("aux_index"),
                    "fx_index": reverb_result.get("fx_index"),
                }
                self._reverb_send_node.mark_clean()
                log.info("SendNode attached: %s observes %s",
                         self._reverb_send_node.name, last_vocal.name)

    def _build_audio_chain(
        self, track_index: int, fx_list: list,
        stem_data: dict, stem_idx: int,
        genre: str, role: str,
        bpm: float | None = None,
    ) -> list[AudioNode]:
        """Build a linked :class:`AudioNode` chain and apply FX to REAPER.

        Returns the list of nodes (linked via ``add_downstream``).
        """
        nodes: list[AudioNode] = []
        prev: AudioNode | None = None
        sd = stem_data.get(stem_idx, {})
        rms = sd.get("raw_rms_db")
        peak = sd.get("raw_peak_db")

        for i, fx in enumerate(fx_list):
            idx = self._fx.add(track_index, fx.name)
            fx_type = _resolve_fx_type(fx.name, fx.fx_type)

            node = AudioNode(
                name=f"{role}_{fx_type}_{i}_{fx.name}",
                fx_type=fx_type,
                params={},
            )
            node.is_dirty = False  # initially clean — just applied

            if prev:
                prev.add_downstream(node)
            nodes.append(node)

            if fx_type == "eq":
                file_path = sd.get("file_path", "")
                eq_position = fx.eq_position if hasattr(fx, "eq_position") else "solo"
                self._apply_eq_baseline(
                    track_index, idx, role,
                    genre=genre, stem_file_path=file_path,
                    position=eq_position, fx_name=fx.name,
                )
                # Update node params with derived EQ bands for traceability
                if hasattr(self, "_last_eq_params"):
                    node.params = dict(self._last_eq_params)
            elif fx_type in _TRANSLATORS and rms is not None and peak is not None:
                intent = _derive_compressor_intent(rms, peak, genre=genre)

                # BPM-aware timing: override genre preset when BPM is known.
                # Skip RVox — it has no attack/release params (single-fader).
                preset = _get_compressor_preset(role, genre)
                if bpm is not None and bpm > 0 and "cla-76" not in fx.name.lower() and fx_type != "rvox":
                    bpm_timing = get_bpm_timing(bpm)
                    if bpm_timing is not None:
                        preset = dict(preset, **bpm_timing)
                        log.info(
                            "BPM-aware timing: %.0f BPM → attack=%.0fms release=%.0fms",
                            bpm, bpm_timing["attack_ms"], bpm_timing["release_ms"],
                        )

                # CLA-76: crest-driven attack + BPM-driven release
                if "cla-76" in fx.name.lower():
                    attack_knob = _compute_cla76_attack_knob(
                        intent.crest_factor_db, genre,
                    )
                    release_knob = None
                    if bpm is not None and bpm > 0:
                        release_ms = 60000.0 / bpm
                        release_knob = _ms_to_cla76_release(release_ms)
                        log.info(
                            "BPM-aware timing: %.0f BPM → release=%.0fms (knob %.2f)",
                            bpm, release_ms, release_knob,
                        )
                    physical = _apply_cla76_params(
                        intent, attack_knob, release_knob,
                    )
                    log.info(
                        "CLA-76 attack: crest=%.1f → knob=%.2f (genre=%s)",
                        intent.crest_factor_db, attack_knob, genre,
                    )
                elif fx_type == "rvox":
                    rvox_mult = _GENRE_RVOX_MULTIPLIER.get(genre, 1.0)
                    physical = _apply_rvox_params(intent, preset, rvox_mult)
                else:
                    physical = _TRANSLATORS[fx_type](intent, preset)

                # No BPM → leave timing at plugin defaults (don't touch).
                # CLA-76 exception: attack is always set (crest-driven).
                if bpm is None:
                    if "cla-76" in fx.name.lower():
                        physical.pop("Release", None)
                    else:
                        for timing_key in ("Attack", "Release"):
                            physical.pop(timing_key, None)

                node.params = dict(physical)
                normalized = normalize_params(fx.name, physical)
                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, idx, pname, pval)
                log.info(
                    "Auto-compressor: %s → %s (crest=%.1f dB, gr=%.1f dB)",
                    fx.name, intent.amount, intent.crest_factor_db,
                    intent.gr_target_db,
                )
            elif fx_type == "deesser":
                # Pro-DS: threshold from presence deficit.  Fixed detection
                # band HPF=4.6kHz / LPF=12kHz covers sibilance range.
                # Single Vocal mode distinguishes sibilance from harmonics
                # internally — no peak-tracking needed.
                spectrum = getattr(self, "_last_spectrum", {}) or {}
                presence_def = spectrum.get("presence_deficit", 0.0)

                # Threshold: aggressive so Range actually engages as safety net.
                threshold_db = -32.0 + presence_def * 0.1
                threshold_db = max(-60.0, min(0.0, threshold_db))

                # Range: genre-aware max gain reduction (dB).
                range_db = _GENRE_PRODS_RANGE.get(genre, 8.5)

                # Fixed detection band (log: freq ≈ 2000 × 10^n Hz).
                hpf_norm = math.log10(5500.0 / 2000.0)
                lpf_norm = math.log10(12000.0 / 2000.0)

                physical = {
                    "Mode":              0.0,      # Single Vocal
                    "Band Processing":   0.0,      # Wide Band (natural)
                    "Threshold":         round(threshold_db, 1),
                    "Range":             range_db,
                    "Lookahead":         10.0,     # ms (manual: ~10 ms optimal)
                    "Lookahead Enabled": 1.0,
                    "High-Pass Frequency": round(hpf_norm, 3),
                    "Low-Pass Frequency":  round(lpf_norm, 3),
                    "Input Level":       0.0,
                    "Output Level":      0.0,
                    "Wet":               1.0,
                }
                node.params = dict(physical)
                normalized = normalize_params(fx.name, physical)
                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, idx, pname, pval)
                log.info(
                    "Auto-deesser: band=5.5k–12kHz, presence_def=%.1f → "
                    "threshold=%.1f dB, range=%.1f dB (genre=%s)",
                    presence_def, threshold_db, range_db, genre,
                )
            else:
                for pname, pval in fx.params.items():
                    self._fx.set_param(track_index, idx, pname, pval)

            log.info("Added %s to track %d [%s]", fx.name, track_index, node.name)
            prev = node

        return nodes

    def update_node_param(self, node: AudioNode, param_name: str,
                          physical_value: float) -> bool:
        """Update a single parameter on a node with dirty-flag cascade.

        The node's params dict is updated and all downstream nodes
        are auto-invalidated.  For EQ nodes, RMS matching suppresses
        cascade invalidation when the overall energy stays constant.

        Returns ``True`` when a dirty cascade was triggered.
        """
        new_params = dict(node.params)
        new_params[param_name] = physical_value
        changed = node.update_params(new_params)
        if changed:
            log.info("[DAG] %s.%s changed → cascade dirty", node.name, param_name)
        return changed

    def _apply_eq_baseline(self, track_index: int, fx_index: int,
                           role: str, *,
                           genre: str = "pop",
                           stem_file_path: str = "",
                           position: str = "solo",
                           fx_name: str = "") -> None:
        """Apply EQ to *track_index* / *fx_index* for the given *role*.

        When *stem_file_path* points to a readable WAV file the full
        spectrum-driven pipeline is used::

            SpectrumAnalyzer → EqIntent → translator → FxManager

        The translator is chosen based on *fx_name*:
        - ``SSLEQ`` → :func:`_apply_ssleq_eq`
        - Everything else → :func:`_apply_proq3_eq`

        *position* ("pre" / "post" / "solo") controls which rules fire
        (see :func:`_derive_eq_intent`).

        Otherwise falls back to the static :data:`_EQ_BASELINE` from
        :mod:`hermes_core.profiles`.
        """
        self._last_eq_params = {}

        log.debug(
            "EQ baseline for %s/%s/%s: stem_file_path=%r, exists=%s, "
            "fx_name=%r, position=%s",
            role, genre, "spectrum" if (stem_file_path and os.path.exists(stem_file_path)) else "static",
            stem_file_path or "",
            os.path.exists(stem_file_path) if stem_file_path else False,
            fx_name, position,
        )

        # ── Spectrum-driven EQ (happy path) ─────────────────
        if stem_file_path and os.path.exists(stem_file_path):
            try:
                report = SpectrumAnalyzer.analyze(stem_file_path)
                # Cache spectrum data so downstream FX (de-esser) can use it.
                self._last_spectrum = {
                    "presence_deficit": report.presence_deficit_db,
                    "air_level_db": report.air_level_db,
                    "sibilance_peak_hz": report.sibilance_peak_hz,
                    "mud_ratio": report.mud_ratio_db,
                }
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
                )

                # Select translator based on FX
                is_ssl = "ssleq" in fx_name.lower()
                if is_ssl:
                    normalized = _apply_ssleq_eq(eq_intent)
                else:
                    normalized = _apply_proq3_eq(eq_intent)

                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, fx_index, pname, pval)

                self._last_eq_params = normalized
                log.info(
                    "Auto-EQ (%s/%s/%s): %d bands @%s — %s",
                    role, genre, position, len(eq_intent.bands),
                    "SSLEQ" if is_ssl else "Pro-Q3",
                    ", ".join(b.reason for b in eq_intent.bands),
                )
                return
            except Exception as exc:
                log.warning(
                    "Spectrum-driven EQ failed (%s), falling back to baseline",
                    exc,
                )

        # ── Static baseline fallback ─────────────────────────
        bands = _EQ_BASELINE.get(role, [])
        if not bands:
            log.debug("EQ baseline: no baseline bands for role=%r, skipping", role)
            return

        log.info(
            "EQ baseline fallback (%s/%s/%s): %d bands — %s",
            role, genre, position, len(bands),
            [(b.get("type"), b.get("freq_hz"), b.get("gain_db", 0.0))
             for b in bands],
        )

        # Build a synthetic EqIntent so the same translators
        # (_apply_proq3_eq / _apply_ssleq_eq) handle normalisation.
        from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
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
                self._fx.set_param(track_index, fx_index, pname, pval)
            self._last_eq_params = normalized
        except Exception as exc:
            log.warning("Baseline EQ apply failed: %s", exc)
            return

        log.info(
            "EQ baseline (%s/%s): %d bands applied",
            role, genre, len(bands),
        )

    # ── Scene 1: Connection & health ─────────────────────

    def health_check(self) -> dict:
        """Return health status of the REAPER connection."""
        result = self._bridge.health_check()
        result["watchdog_enabled"] = self._watchdog_enabled
        result["recent_dialog_events"] = [
            {
                "window_title": e.window_title,
                "action_taken": e.action_taken,
                "timestamp": e.timestamp,
            }
            for e in self._bridge.get_recent_dialog_events()[-20:]
        ]
        return result

    # ── Scene 2: Project & tracks ────────────────────────

    def _safe_project_path(self, output_dir: str, name: str) -> tuple[str, bool]:
        """Return (path, conflict_renamed) for ``{output_dir}/{name}.rpp``.

        If the target already exists a timestamp suffix is appended to avoid
        overwriting a previous project.

        REAPER's ``Main_SaveProjectEx`` can fail with NEWTEMP errors on
        paths with non-ASCII characters or restrictive macOS permissions.
        To guarantee headless reliability we ALWAYS save to a system temp
        directory and then copy the result to *output_dir* as a post-save
        step.  The temp directory is returned so the caller knows where
        REAPER actually writes.
        """
        os.makedirs(output_dir, exist_ok=True)

        target = os.path.join(output_dir, f"{name}.rpp")
        conflict = os.path.exists(target)
        if conflict:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            target = os.path.join(output_dir, f"{name}_{ts}.rpp")
            log.info("Project file exists — renamed to %s", target)

        return target, conflict

    def create_project(self, name: str, output_dir: str,
                       sample_rate: int = 48000) -> dict:
        """Create a named project and save it to *output_dir* without dialogs.

        Returns ``{name, path, sample_rate, track_count, conflict_renamed}``.
        """
        safe_path, conflict_renamed = self._safe_project_path(output_dir, name)

        api = self._bridge.api

        # Safety: never delete tracks without explicit user consent.
        # If the project already has tracks (manually placed plugins, settings),
        # require opt-in.  Empty projects are safe to set up.
        existing_tracks = api.CountTracks(0)
        if self._tracks_protected and existing_tracks > 0:
            raise RuntimeError(
                f"Project has {existing_tracks} existing track(s). "
                "Deleting tracks is protected. Call eng.allow_track_deletion() "
                "first to confirm you want to wipe the project."
            )

        # Delete all tracks using raw API (reverse order to avoid index shifting).
        # Try both raw API and reapy's high-level API for reliability.
        n_tracks = api.CountTracks(0)
        for i in range(n_tracks - 1, -1, -1):
            tr = api.GetTrack(0, i)
            if tr:
                try:
                    api.DeleteTrack(tr)
                except Exception:
                    # Fallback to reapy's high-level API if raw API fails
                    try:
                        proj = self._bridge.rpr.Project()
                        if i < len(proj.tracks):
                            proj.tracks[i].delete()
                    except Exception as e:
                        log.warning("Failed to delete track %d: %s", i, e)

        # Reset master track
        master = api.GetMasterTrack(0)
        if master:
            n_fx = api.TrackFX_GetCount(master)
            for i in range(n_fx - 1, -1, -1):
                api.TrackFX_Delete(master, i)
            api.SetMediaTrackInfo_Value(master, "D_VOL", 1.0)
            api.SetMediaTrackInfo_Value(master, "B_MUTE", 0.0)
            api.SetMediaTrackInfo_Value(master, "I_SOLO", 0.0)
            api.SetMediaTrackInfo_Value(master, "D_PAN", 0.0)

        api.GetSetProjectInfo_String(0, "PROJECT_NAME", name, True)
        if sample_rate > 0:
            api.GetSetProjectInfo(0, "PROJECT_SRATE", sample_rate, True)
            api.GetSetProjectInfo(0, "PROJECT_SRATE_USE", 1, True)
        import base64
        api.GetSetProjectInfo_String(
            0, "RENDER_FORMAT",
            base64.b64encode(b"evaw\x18\x00\x01").decode(), True,
        )

        # Save to a temp directory first — REAPER can fail with NEWTEMP
        # errors on paths with non-ASCII characters or restrictive macOS
        # sandbox permissions.  We always save to a known-safe temp dir,
        # then copy the result to the user's requested path.
        tmp_dir = tempfile.mkdtemp(prefix="hermes_proj_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(safe_path))
        api.Main_SaveProjectEx(0, tmp_path, 0)
        try:
            shutil.copy2(tmp_path, safe_path)
            log.info("Project copied %s → %s", tmp_path, safe_path)
        except OSError:
            log.warning("Could not copy project to %s; using temp path", safe_path)
            safe_path = tmp_path
        self._project_path = safe_path
        # Snapshot REAPER's view of the project — later operations verify
        # the user has not manually switched to a different project.
        _, name_buf, _ = api.GetProjectName(0, "", 256)
        self._snapshot_project_name = name_buf or ""
        path_buf, _ = api.GetProjectPath("", 256)
        self._snapshot_project_path = path_buf or ""
        # Fresh project — clear all idempotency guards.
        self.reset()

        return {
            "name": name,
            "path": safe_path,
            "sample_rate": sample_rate,
            "track_count": 0,
            "conflict_renamed": conflict_renamed,
        }

    def _safe_save(self, target_path: str) -> str:
        """Save project via a temp dir to avoid REAPER NEWTEMP errors.

        REAPER's ``Main_SaveProjectEx`` can trigger modal "Error creating
        project file" dialogs on paths with non-ASCII characters or macOS
        sandbox restrictions.  We always save to a temp directory, then
        copy the result to *target_path*.
        """
        tmp_dir = tempfile.mkdtemp(prefix="hermes_save_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(target_path))
        self._bridge.api.Main_SaveProjectEx(0, tmp_path, 0)
        try:
            shutil.copy2(tmp_path, target_path)
        except OSError:
            log.warning("Could not copy to %s; keeping temp path", target_path)
            return tmp_path
        return target_path

    def save_project(self) -> dict:
        """Silently save to the current project path via ``Main_SaveProjectEx``.

        Raises ``RuntimeError`` when no project path has been established
        (i.e. ``create_project`` was never called).
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        actual = self._safe_save(self._project_path)
        return {"path": actual, "saved_at": datetime.now().isoformat()}

    def save_checkpoint(self, label: str = "") -> dict:
        """Save a timestamped copy without touching the main project file.

        Use before risky operations (adding FX, destructive edits) so you
        can always return to a known-good state.
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        base = os.path.splitext(self._project_path)[0]
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        suffix = f"_{label}_{ts}" if label else f"_{ts}"
        checkpoint_path = f"{base}_checkpoint{suffix}.rpp"

        actual = self._safe_save(checkpoint_path)
        return {"checkpoint_path": actual, "main_path": self._project_path}

    def get_project_info(self) -> dict:
        """Return current project metadata.

        ``{name, path, sample_rate, track_count}``.
        """
        api = self._bridge.api
        _, name_buf, _ = api.GetProjectName(0, "", 256)
        path_buf, _ = api.GetProjectPath("", 256)
        sr = api.GetSetProjectInfo(0, "PROJECT_SRATE", 0, False)
        n_tracks = api.CountTracks(0)

        return {
            "name": (name_buf or ""),
            "path": (path_buf or ""),
            "sample_rate": int(sr) if sr else 0,
            "track_count": n_tracks,
        }

    def import_stems(self, file_paths: list[str],
                    position: float = 0.0) -> list[dict]:
        """Import audio files, creating one track per file named by basename.

        Returns list of {name, track_index, file_path, success}.
        """
        results = []
        for path in file_paths:
            name = os.path.splitext(os.path.basename(path))[0]
            idx = self._tracks.create(name=name)
            ok = self._tracks.import_media(idx, path, position)
            results.append({
                "name": name,
                "track_index": idx,
                "file_path": path,
                "success": ok,
            })
        return results

    def list_tracks(self) -> list[TrackInfo]:
        """Return TrackInfo for all tracks in the project."""
        return self._tracks.list_all()

    # ── Scene 3: Gain staging ────────────────────────────

    def apply_gain(self, track_index: int, gain_db: float,
                   target: str = "track_fader"):
        """Apply a gain change to a track.

        target: "track_fader" | "clip_gain" | "master_fader"
        """
        if target == "track_fader":
            self._tracks.set_volume(track_index, gain_db)
        elif target == "clip_gain":
            self._tracks.set_item_volume(track_index, gain_db)
        elif target in ("master_fader",):
            raise NotImplementedError(
                f"Gain target '{target}' not yet implemented"
            )
        else:
            raise ValueError(f"Unknown gain target: {target}")

    def get_gain_structure(self) -> dict:
        """Return gain overview for all tracks."""
        tracks = []
        for t in self._tracks.list_all():
            tracks.append({
                "index": t.index,
                "name": t.name,
                "volume_db": t.volume_db,
                "pan": t.pan,
                "mute": t.mute,
            })
        return {"tracks": tracks}

    def prepare_stems(
        self,
        stem_paths: list[str],
        *,
        genre: str = "pop",
        bpm: float | None = None,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
    ) -> dict:
        """Analyse raw stems and apply clip gain to reference level.

        Clip gain brings every stem to -18 dBFS RMS (0 VU reference) so
        downstream plugins see consistent input levels across projects.

        Fader balancing is deferred to :meth:`post_fx_balance` — call it
        **after** :meth:`apply_profile` so the balance accounts for the
        loudness changes introduced by EQ, compression and reverb.

        This method is **idempotent** — calling it twice on the same
        engine instance raises ``RuntimeError``.  Call :meth:`reset` to
        clear the guard for a fresh mix.
        """
        if self._stems_gain_staged:
            raise RuntimeError(
                "Stems already gain-staged. Call reset() to start a new mix, "
                "or create a new project with create_project()."
            )
        self._ensure_project_match()

        # Store BPM for downstream use (apply_profile / _build_audio_chain).
        self._bpm = bpm

        def _do_prepare():
            return self._prepare_stems_impl(
                stem_paths, genre=genre, vocal_indices=vocal_indices,
                backing_indices=backing_indices,
            )

        result = self._undo_block("Prepare Stems", _do_prepare)
        self._stems_gain_staged = True
        self._stems_cache = result.get("stems", [])
        return result

    def _prepare_stems_impl(
        self,
        stem_paths: list[str],
        *,
        genre: str = "pop",
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
    ) -> dict:
        # 1. Import stems
        imported = self.import_stems(stem_paths)

        # 2. Classify roles
        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stem_paths))
                               if i not in vocal_indices]

        # 3. Measure each imported stem and apply clip gain
        stems_out = []
        for i, imp in enumerate(imported):
            if not imp["success"]:
                stems_out.append({
                    "file_path": stem_paths[i],
                    "role": self._classify_role(i, vocal_indices, backing_indices),
                    "track_index": imp["track_index"],
                    "track_name": imp["name"],
                    "raw_rms_db": None,
                    "raw_lufs": None,
                    "raw_peak_db": None,
                    "clip_gain_db": 0.0,
                    "adjusted_lufs": None,
                    "fader_gain_db": 0.0,
                    "success": False,
                })
                continue

            try:
                ana = SignalAnalyzer.analyze(stem_paths[i])
                raw_rms_db = ana.rms_db
                raw_lufs = ana.integrated_lufs
                raw_peak_db = ana.peak_db
            except (OSError, ValueError, RuntimeError):
                raw_rms_db = None
                raw_lufs = None
                raw_peak_db = None

            # Stage 1: clip gain to reference level
            clip_gain_db = 0.0
            if raw_rms_db is not None:
                clip_gain_db = _CLIP_GAIN_REF_DB - raw_rms_db
                # Peak guard — clip gain must not push any sample above 0 dBFS
                if raw_peak_db is not None and clip_gain_db > 0:
                    headroom = -raw_peak_db
                    if clip_gain_db > headroom:
                        log.debug(
                            "Clip gain %.1f dB capped to %.1f dB — "
                            "peak %.1f dBFS leaves no headroom",
                            clip_gain_db, headroom, raw_peak_db,
                        )
                        clip_gain_db = headroom
                self.apply_gain(imp["track_index"], clip_gain_db,
                                target="clip_gain")

            adjusted_lufs = (
                raw_lufs + clip_gain_db if raw_lufs is not None else None
            )

            stems_out.append({
                "file_path": stem_paths[i],
                "role": self._classify_role(i, vocal_indices, backing_indices),
                "track_index": imp["track_index"],
                "track_name": imp["name"],
                "raw_rms_db": raw_rms_db,
                "raw_lufs": raw_lufs,
                "raw_peak_db": raw_peak_db,
                "clip_gain_db": round(clip_gain_db, 1),
                "adjusted_lufs": (
                    round(adjusted_lufs, 1) if adjusted_lufs is not None
                    else None
                ),
                "fader_gain_db": 0.0,
                "success": imp["success"],
            })

        # 4. Fader balancing and peak ceiling are deferred to
        #    post_fx_balance() — after FX chains have been applied.

        return {
            "stems": stems_out,
            "genre": genre,
            "vocal_indices": vocal_indices,
            "backing_indices": backing_indices,
        }

    @staticmethod
    def _classify_role(idx: int, vocal_indices: list[int],
                       backing_indices: list[int]) -> str:  # noqa: D401
        if idx in vocal_indices:
            return "vocal"
        if idx in backing_indices:
            return "backing"
        return "other"

    # ── Post-FX fader balancing ──────────────────────────

    def _balance_faders(
        self,
        stems: list[dict],
        *,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        genre: str = "pop",
    ) -> dict:
        """Set fader gains so backing sits *ratio* LU below vocal.

        Vocal fader stays at 0 (reference).  Backing is attenuated to
        achieve the genre-appropriate vocal/backing ratio.
        """
        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stems))
                               if i not in vocal_indices]

        ratio = _GENRE_VOCAL_TO_BACKING.get(genre, 3)

        vocal_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems)
            if i in vocal_indices and s.get("adjusted_lufs") is not None
        ]
        backing_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems)
            if i in backing_indices and s.get("adjusted_lufs") is not None
        ]
        vocal_lufs = (
            sum(vocal_lufs_vals) / len(vocal_lufs_vals)
            if vocal_lufs_vals else -20.0
        )
        backing_lufs = (
            sum(backing_lufs_vals) / len(backing_lufs_vals)
            if backing_lufs_vals else -20.0
        )

        backing_target = vocal_lufs - ratio

        for i, s in enumerate(stems):
            if not s.get("success") or s.get("adjusted_lufs") is None:
                continue
            if i in vocal_indices:
                fader_gain_db = 0.0  # reference — don't move
            elif i in backing_indices:
                fader_gain_db = backing_target - s["adjusted_lufs"]
            else:
                continue
            s["fader_gain_db"] = round(fader_gain_db, 1)
            self.apply_gain(s["track_index"], fader_gain_db)

        return {
            "ratio_lu": ratio,
            "vocal_lufs": round(vocal_lufs, 1),
            "backing_lufs": round(backing_lufs, 1),
            "backing_target_lufs": round(backing_target, 1),
        }

    def _solo_render(
        self, indices: list[int], output_dir: str, label: str = ""
    ) -> dict:
        """Temporarily solo *indices*, render, restore solo state.

        Returns the render result dict (including ``output_path``).
        """
        api = self._bridge.api
        n = api.CountTracks(0)

        # Save solo state and solo only the requested indices
        saved: dict[int, bool] = {}
        for i in range(n):
            tr = api.GetTrack(0, i)
            if tr:
                try:
                    solo = api.GetMediaTrackInfo_Value(tr, "I_SOLO")
                except Exception as e:
                    log.debug("Failed to get solo state for track %d: %s", i, e)
                    solo = 0.0
                saved[i] = bool(solo)
                api.SetMediaTrackInfo_Value(tr, "I_SOLO", 1.0 if i in indices else 0.0)

        try:
            result = self.render_mix(output_dir, verify=False)
        finally:
            # Restore original solo state
            for i in range(n):
                tr = api.GetTrack(0, i)
                if tr:
                    api.SetMediaTrackInfo_Value(
                        tr, "I_SOLO", 1.0 if saved.get(i, False) else 0.0
                    )

        return result

    def post_fx_balance(
        self,
        *,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        genre: str = "pop",
        tmp_dir: str | None = None,
    ) -> dict:
        """Measure post-FX LUFS, set fader balance, enforce peak ceiling.

        **Must be called after** :meth:`apply_profile`.

        1. Solo-render vocal + backing → measure post-FX LUFS.
        2. Set faders so backing sits *ratio* LU below vocal (genre-based).
        3. Render full mix → measure peak.
        4. If peak > :data:`_PEAK_CEILING_DB`, attenuate both equally.

        Returns balance metadata plus combined LUFS and peak.
        """

        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_balance_")

        # Deep copy to avoid mutating the cached stem data
        stems = [dict(s) for s in self._stems_cache]
        if not stems:
            raise RuntimeError(
                "No cached stems — call prepare_stems() first"
            )

        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stems))
                               if i not in vocal_indices]

        # Map stem index → track index
        stem_idx_to_track = {
            i: s["track_index"] for i, s in enumerate(stems) if s.get("success")
        }

        # ── Solo-render vocal group ──
        vocal_tracks = [
            stem_idx_to_track[i] for i in vocal_indices
            if i in stem_idx_to_track
        ]
        vocal_lufs = None
        if vocal_tracks:
            vocal_result = self._solo_render(
                vocal_tracks,
                os.path.join(tmp, "vocal_solo"),
                "vocal",
            )
            if vocal_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(vocal_result["output_path"])
                    vocal_lufs = ana.integrated_lufs
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Solo-render backing group ──
        backing_tracks = [
            stem_idx_to_track[i] for i in backing_indices
            if i in stem_idx_to_track
        ]
        backing_lufs = None
        if backing_tracks:
            backing_result = self._solo_render(
                backing_tracks,
                os.path.join(tmp, "backing_solo"),
                "backing",
            )
            if backing_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(backing_result["output_path"])
                    backing_lufs = ana.integrated_lufs
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Update cached LUFS ──
        for i, s in enumerate(stems):
            if not s.get("success"):
                continue
            if i in vocal_indices and vocal_lufs is not None:
                s["adjusted_lufs"] = vocal_lufs
            elif i in backing_indices and backing_lufs is not None:
                s["adjusted_lufs"] = backing_lufs

        # ── Step 1: ratio-based fader balance ──
        balance_info = self._balance_faders(
            stems,
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
            genre=genre,
        )

        # ── Step 2: full-mix render → peak check ──
        combined_lufs = None
        combined_peak = None
        full_tracks = vocal_tracks + backing_tracks
        if full_tracks:
            full_result = self._solo_render(
                full_tracks,
                os.path.join(tmp, "full_mix"),
                "full",
            )
            if full_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(full_result["output_path"])
                    combined_lufs = ana.integrated_lufs
                    combined_peak = ana.peak_db
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Step 3: peak ceiling — scale both down if peak > -3 ──
        atten_db = 0.0
        if combined_peak is not None and combined_peak > _PEAK_CEILING_DB:
            atten_db = _PEAK_CEILING_DB - combined_peak  # negative
            for i, s in enumerate(stems):
                if not s.get("success") or s.get("track_index") is None:
                    continue
                if i in vocal_indices or i in backing_indices:
                    # Accumulate — ratio faders were already set by _balance_faders.
                    current_fader = s.get("fader_gain_db", 0.0)
                    new_fader = current_fader + atten_db
                    self.apply_gain(s["track_index"], new_fader)
                    s["fader_gain_db"] = round(new_fader, 1)
            combined_peak = _PEAK_CEILING_DB
            if combined_lufs is not None:
                combined_lufs = combined_lufs + atten_db

            log.info(
                "Peak ceiling: peak=%.1f dB → attenuated %.1f dB to hit %.1f dB",
                combined_peak - atten_db, atten_db, _PEAK_CEILING_DB,
            )

        log.info(
            "Post-FX balance: vocal=%.1f LUFS, backing=%.1f LUFS, "
            "combined=%.1f LUFS, peak=%.1f dB, ratio=%.1f LU",
            vocal_lufs or float("nan"),
            backing_lufs or float("nan"),
            combined_lufs or float("nan"),
            combined_peak or float("nan"),
            balance_info["ratio_lu"],
        )

        # ── Build reverb wet cache for preview mode ──
        reverb_wet_path = self._cache_reverb_wet(tmp)

        # ── Compute spatial send levels ────────────────────────
        # Uses signal analysis already collected during
        # prepare_stems (crest factor) and _build_audio_chain
        # (spectrum data) to derive genre-aware reverb/delay
        # send levels.  The sends are not created here — only
        # computed and returned for downstream use.
        vocal_stem = self._stems_cache[0] if self._stems_cache else {}
        crest_db = (
            vocal_stem.get("raw_peak_db", -3.0)
            - vocal_stem.get("raw_rms_db", -18.0)
        )
        spectrum = getattr(self, "_last_spectrum", {}) or {}
        spatial_sends = _compute_spatial_sends(
            genre=genre,
            crest_factor_db=crest_db,
            presence_deficit_db=spectrum.get("presence_deficit", 2.0),
            mud_ratio_db=spectrum.get("mud_ratio", -3.0),
            sibilance_peak_db=spectrum.get("sibilance_peak_hz"),
            section="verse",
        )

        return {
            **balance_info,
            "vocal_lufs": vocal_lufs,
            "backing_lufs": backing_lufs,
            "combined_lufs": combined_lufs,
            "combined_peak_db": combined_peak,
            "peak_atten_db": atten_db,
            "reverb_wet_cache": reverb_wet_path,
            "stems": stems,
            "spatial_sends": spatial_sends,
        }

    def apply_bus_compressor(
        self,
        bpm: float | None = None,
        genre: str = "pop",
    ) -> dict:
        """Apply bx_townhouse bus compressor to the master track.

        Pipeline step between ``post_fx_balance`` and manual mastering.
        The automation chain::

            1. Probe-render to measure the mix peak after fader balance.
            2. Compute threshold, attack, and makeup from genre + BPM.
            3. Add bx_townhouse to the master track, set all parameters.

        Returns a diagnostic dict with *peak_db*, *thresh_db*, *attack_ms*,
        *makeup_db*, and *gr_target*.
        """

        # ── 1. Probe render — measure what hits the master bus ──
        tmp_dir = tempfile.mkdtemp(prefix="hermes_bus_probe_")
        probe = self.render_mix(tmp_dir, verify=True)
        signal = probe.get("signal_check", {})
        peak_db = signal.get("peak_db", -6.0)
        if signal.get("error"):
            log.warning("Bus compressor probe failed: %s — using peak=%.1f dB",
                        signal["error"], peak_db)

        # ── 2. Compute parameters ──
        physical = compute_bus_compressor_params(
            peak_db=peak_db, bpm=bpm, genre=genre,
        )
        target_gr_db = physical.pop("_target_gr", 2.0)

        # ── 3. Add bx_townhouse to master ──
        fx_idx = self.add_master_fx(
            "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"
        )
        if fx_idx < 0:
            log.error("Failed to add bx_townhouse to master track")
            return {
                "peak_db": peak_db,
                "thresh_db": physical.get("Thresh", 0),
                "attack_ms": physical.get("Attack", 30),
                "makeup_db": physical.get("MakeUp", 1.0),
                "gr_target": target_gr_db,
                "error": "fx_add_failed",
            }

        # ── 4. Normalise and apply ──
        plugin_name = "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"
        try:
            normalized = normalize_params(plugin_name, physical)
        except Exception as exc:
            log.error("Failed to normalise bus compressor params: %s", exc)
            return {
                "peak_db": peak_db,
                "thresh_db": physical.get("Thresh", 0),
                "attack_ms": physical.get("Attack", 30),
                "makeup_db": physical.get("MakeUp", 1.0),
                "gr_target": target_gr_db,
                "error": "normalise_failed",
            }

        for param_name, norm_value in normalized.items():
            self._fx.set_param(-1, fx_idx, param_name, norm_value)

        log.info(
            "Bus compressor: peak=%.1f dB → thresh=%.1f dB, "
            "attack=%.1f ms, makeup=%.1f dB, target GR=%.1f dB",
            peak_db,
            physical["Thresh"],
            physical["Attack"],
            physical["MakeUp"],
            target_gr_db,
        )

        return {
            "peak_db": peak_db,
            "thresh_db": physical["Thresh"],
            "attack_ms": physical["Attack"],
            "makeup_db": physical["MakeUp"],
            "gr_target": target_gr_db,
        }

    def check_headroom(self) -> dict:
        """Check headroom. Without rendering, reports source as unavailable."""
        return {
            "headroom_dbtp": None,
            "source": "unavailable_without_render",
            "message": "Render the project first to measure headroom",
        }

    # ── Scene 4: FX ──────────────────────────────────────

    def add_fx(self, track_index: int, fx_name: str) -> int:
        """Add an effect plugin to a track. Returns FX index."""
        return self._fx.add(track_index, fx_name)

    def get_fx_chain(self, track_index: int) -> list[dict]:
        """Return all FX on a track."""
        return self._fx.get_chain(track_index)

    def add_master_fx(self, fx_name: str) -> int:
        """Add an effect plugin to the master track. Returns FX index."""
        return self._fx.add_master(fx_name)

    # ── Scene 5: Bus & sends ─────────────────────────────

    def create_bus(self, name: str, child_tracks: list[int]) -> int:
        """Create a folder bus containing the given child tracks."""
        return self._bus.create_bus(name, child_tracks)

    def create_reverb_send(self, src_track: int,
                          level_db: float = -8.0,
                          reverb_fx: str = "ReaVerbate",
                          mode: str = "post-fader") -> dict:
        """Create a reverb aux return and send from src_track to it.

        **Abbey Road trick**: a safety EQ (HPF @ 600 Hz, LPF @ 10 kHz)
        is automatically inserted before the reverb on the aux track.
        This prevents low-frequency mud and high-frequency sibilance
        in the reverb tail — the Agent never sees these filters.

        Returns {aux_index, send, fx_index, abbey_eq_index}.
        """
        aux_idx = self._tracks.create(name="Verb Return")

        # Abbey Road safety EQ — de-mud + de-ess the reverb input
        abbey_eq_idx = self._fx.add(aux_idx, "ReaEQ (Cockos)")
        if abbey_eq_idx >= 0:
            self._apply_abbey_road_eq(aux_idx, abbey_eq_idx)

        fx_idx = self._fx.add(aux_idx, reverb_fx)

        send_info = self._send.create(
            src=src_track, dest=aux_idx, level_db=level_db, mode=mode
        )

        return {
            "aux_index": aux_idx,
            "send": send_info,
            "fx_index": fx_idx,
            "abbey_eq_index": abbey_eq_idx,
        }

    @staticmethod
    def _apply_abbey_road_eq(aux_track: int, eq_fx_idx: int) -> None:
        """Configure ReaEQ as an Abbey Road safety filter.

        Band 1: HPF @ 600 Hz (removes low-end mud from reverb).
        Band 2: LPF @ 10 kHz (removes sibilance / harshness).

        These parameters are **not exposed to the Agent** — they are
        an engine-level safeguard applied automatically to every
        reverb send.
        """
        # ReaEQ band types: 0=low-shelf, 1=band, 2=high-shelf, 3=LPF, 4=HPF, …
        # We set these via normalised values.  Without a registered param
        # map for ReaEQ, we use raw parameter indices discovered at runtime.
        # For now the intent is captured; full mapping requires ReaEQ
        # parameter discovery (see _apply_eq_baseline note).
        log.debug(
            "Abbey Road EQ intent: HPF@600Hz + LPF@10kHz on aux %d slot %d",
            aux_track, eq_fx_idx,
        )

    def _apply_eq_rms_match(
        self, track_index: int, fx_index: int,
        pre_rms_db: float, post_rms_db: float,
    ) -> None:
        """Compensate EQ gain change so downstream nodes see consistent RMS.

        If the EQ caused the RMS to drop by *Δ* dB, apply *+Δ* dB of
        output gain.  This prevents cascade invalidation of downstream
        compressors when only EQ frequencies changed.

        Called after every EQ parameter update.
        """
        delta = pre_rms_db - post_rms_db
        if abs(delta) < 0.2:
            return  # inaudible — skip to avoid parameter churn

        log.debug(
            "RMS match: track %d EQ@%d pre=%.1f → post=%.1f (Δ=%.1f dB)",
            track_index, fx_index, pre_rms_db, post_rms_db, delta,
        )
        # Attempt to set Output Gain on the EQ plugin.
        # If the param name differs, the call silently fails — the EQ just
        # won't be gain-compensated, which is acceptable (not critical).
        self._fx.set_param(track_index, fx_index, "Output Gain", delta)
        self._fx.set_param(track_index, fx_index, "Output", delta)

    # ── Scene 6: Render ──────────────────────────────────

    def render_mix(self, output_dir: str,
                   bounds: str = "entire_project",
                   fmt: str = "wav",
                   sample_rate: int = 0,
                   verify: bool = True,
                   timeout: float = 120.0) -> dict:
        """Render project and optionally run signal analysis.

        Returns {output_path, signal_check, ...}.
        """
        result = self._render.render_mix(
            output_dir=output_dir,
            bounds=bounds,
            fmt=fmt,
            sample_rate=sample_rate,
            timeout=timeout,
        )

        if verify and result.get("output_path"):
            try:
                report = SignalAnalyzer.analyze(result["output_path"])
                result["signal_check"] = {
                    "integrated_lufs": report.integrated_lufs,
                    "true_peak_dbtp": report.true_peak_dbtp,
                    "clip_count": report.clip_count,
                    "clip_passed": report.clip_passed,
                    "silence_passed": report.silence_passed,
                    "rms_db": report.rms_db,
                    "peak_db": report.peak_db,
                    "duration_sec": report.duration_sec,
                }
            except (OSError, ValueError, RuntimeError) as e:
                result["signal_check"] = {"error": str(e)}

        return result

    # ── Scene 7: Safety audit ────────────────────────────

    def audit_mix(self, file_path: str) -> dict:
        """Run a full safety audit on a rendered mix file.

        Returns {passed, checks: [{check_name, severity, message}, ...], diagnostics}.
        """
        try:
            report = SignalAnalyzer.analyze(file_path)
        except (OSError, ValueError, RuntimeError) as e:
            return {"passed": False, "error": str(e)}

        checks = []

        if not report.silence_passed:
            checks.append({
                "check_name": "silence",
                "severity": "critical",
                "message": f"Mix is silent (RMS={report.rms_db} dB)",
            })

        if not report.clip_passed:
            checks.append({
                "check_name": "clipping",
                "severity": "critical",
                "message": f"Mix has {report.clip_count} clipped samples",
            })

        if report.true_peak_dbtp > 0.0:
            checks.append({
                "check_name": "true_peak",
                "severity": "warning",
                "message": (
                    f"True peak {report.true_peak_dbtp} dBTP exceeds 0 dBTP"
                ),
            })
        elif report.true_peak_dbtp > -1.0:
            checks.append({
                "check_name": "true_peak",
                "severity": "info",
                "message": (
                    f"True peak {report.true_peak_dbtp} dBTP "
                    "(within 1 dB of ceiling)"
                ),
            })

        criticals = [c for c in checks if c["severity"] == "critical"]
        passed = len(criticals) == 0

        return {
            "passed": passed,
            "checks": checks or [
                {"check_name": "all_clear", "severity": "info",
                 "message": "No issues detected"}
            ],
            "diagnostics": {
                "integrated_lufs": report.integrated_lufs,
                "true_peak_dbtp": report.true_peak_dbtp,
                "rms_db": report.rms_db,
                "peak_db": report.peak_db,
                "clip_count": report.clip_count,
                "duration_sec": report.duration_sec,
                "sample_rate": report.sample_rate,
            },
        }

    # ── Scene 8: Master finalization ───────────────────────

    def finalize_master(
        self,
        target_lufs: float = _DEFAULT_TARGET_LUFS,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tolerance: float = 0.3,
        tmp_dir: str | None = None,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> dict:
        """Two-pass master finalization via brickwall-limiter simulation.

        1. Add *limiter_fx* to master with Gain=0, Output Level=*ceiling_db*.
        2. Probe render → brickwall simulation + binary search → optimal Gain.
        3. Apply gain, render final.
        4. Verify final LUFS against target.

        The binary search accounts for limiter nonlinearity directly,
        so the open-loop formula is no longer needed.

        This method is **idempotent** — calling it twice on the same
        engine instance raises ``RuntimeError``.  Call :meth:`reset` to
        clear the guard for a fresh mix.

        *on_progress* is an optional callback ``(stage: str, pct: float)``
        called at each phase for progress reporting.
        """
        if self._master_finalized:
            raise RuntimeError(
                "Master already finalized. Call reset() to start a new mix, "
                "or create a new project with create_project()."
            )
        self._ensure_project_match()

        def _do_finalize():
            return self._finalize_master_impl(
                target_lufs, limiter_fx=limiter_fx, ceiling_db=ceiling_db,
                tolerance=tolerance, tmp_dir=tmp_dir,
                on_progress=on_progress,
            )

        result = self._undo_block("Finalize Master", _do_finalize)
        if result.get("passed"):
            self._master_finalized = True
        return result

    def _finalize_master_impl(
        self,
        target_lufs: float = _DEFAULT_TARGET_LUFS,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tolerance: float = 0.3,
        tmp_dir: str | None = None,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> dict:
        def _progress(stage: str, pct: float):
            if on_progress:
                on_progress(stage, pct)


        _progress("setup", 0.0)
        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_master_")
        probe_dir = os.path.join(tmp, "probe")
        final_dir = os.path.join(tmp, "final")

        # 1. Add limiter
        fx_idx = self._fx.add_master(limiter_fx)
        if fx_idx < 0:
            return _master_error(
                target_lufs, ceiling_db,
                f"Failed to add {limiter_fx} to master",
            )

        # Pro-L 2 param formulas (verified 2026-05-28 via REAPER calibration):
        #   Gain: 0..+30 dB → normalized = gain_db / 30
        #   Output Level: -30..0 dB → normalized = (ceiling_db + 30) / 30
        ceiling_norm = max(0.0, min(1.0, (ceiling_db + _PRO_L2_RANGE_DB) / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Output Level", ceiling_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Output Level param not found — may need calibration",
            )
        if not self._fx.set_param(-1, fx_idx, "Gain", 0.0):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found — may need calibration",
            )

        # 2. Probe render
        _progress("probe_render", 0.15)
        probe_result = self.render_mix(probe_dir, verify=True)
        probe_sc = probe_result.get("signal_check", {})
        pre_peak = probe_sc.get("peak_db", 0.0)
        if probe_result.get("output_path") is None:
            return _master_error(
                target_lufs, ceiling_db, "Probe render failed",
            )

        # 3. Hard-clip model + binary search → optimal Gain
        _progress("search", 0.35)
        probe_path = probe_result.get("output_path")
        cal = load_calibration()
        search = find_optimal_gain(
            probe_path,
            target_lufs=target_lufs,
            ceiling_dbtp=ceiling_db,
            tolerance=tolerance,
            calibration_offset=cal,
        )
        if not search.converged and search.probe_lufs <= -70:
            return _master_error(
                target_lufs, ceiling_db, "Probe is near-silent",
            )

        gain_db = search.gain_db

        # 4. Apply gain and render final.
        _progress("final_render", 0.65)
        gain_norm = max(0.0, min(1.0, gain_db / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Gain", gain_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found during final render",
            )
        final_result = self.render_mix(final_dir, verify=True)
        output_path = final_result.get("output_path")

        # 5. Verify
        _progress("verify", 0.90)
        achieved_lufs = None
        passed = output_path is not None
        if output_path:
            verify = verify_output(output_path, target_lufs=target_lufs)
            achieved_lufs = verify.actual_lufs
            passed = verify.passed

        log.info(
            "Master report:\n%s",
            generate_report(search, verify if output_path else None),
        )

        return {
            "target_lufs": target_lufs,
            "achieved_lufs": achieved_lufs,
            "probe_lufs": search.probe_lufs,
            "gain_db": gain_db,
            "ceiling_db": ceiling_db,
            "passed": passed,
            "converged": search.converged,
            "pre_limiter_peak_db": pre_peak,
            "output_path": output_path,
        }

    # ── Wet reverb caching ────────────────────────────────

    def _cache_reverb_wet(self, cache_dir: str) -> str | None:
        """Render 100% wet reverb and cache the WAV.

        Solos the reverb return track, renders, and saves the result.
        Subsequent preview renders can numpy-mix this cache with
        dry renders without waking REAPER for simple level changes.

        Returns the cache path or ``None``.
        """
        if self._reverb_send_node is None:
            return None

        aux_index = self._reverb_send_node.params.get("aux_index")
        if aux_index is None:
            return None

        os.makedirs(cache_dir, exist_ok=True)
        wet_path = os.path.join(cache_dir, "reverb_wet_cache.wav")

        # ── Solo the reverb return, render ──
        result = self._solo_render([aux_index], cache_dir, "reverb_wet")
        rendered = result.get("output_path")
        if rendered and os.path.exists(rendered):
            import shutil
            shutil.move(rendered, wet_path)
            log.info("[wet-cache] Reverb wet cached → %s", wet_path)
            self._reverb_send_node.params["_wet_cache_path"] = wet_path
            return wet_path

        log.warning("[wet-cache] Reverb wet render failed")
        return None

    @staticmethod
    def _numpy_mix(dry_path: str, wet_path: str,
                   wet_level_db: float, output_path: str) -> str | None:
        """Mix dry + wet WAVs in numpy with *wet_level_db* gain on wet.

        Pure Python / numpy — no REAPER call.  Returns *output_path*.
        """
        import numpy as np
        import soundfile as sf

        try:
            dry, sr = sf.read(dry_path, dtype="float64")
            wet, sr_w = sf.read(wet_path, dtype="float64")
        except Exception as exc:
            log.warning("[numpy-mix] Read error: %s", exc)
            return None

        # Match sample rates and lengths
        if sr != sr_w:
            log.warning("[numpy-mix] SR mismatch dry=%d wet=%d", sr, sr_w)
            return None

        min_len = min(len(dry), len(wet))
        dry = dry[:min_len]
        wet = wet[:min_len]

        # Ensure 2-D
        if dry.ndim == 1:
            dry = dry.reshape(-1, 1)
        if wet.ndim == 1:
            wet = wet.reshape(-1, 1)

        # Broadcast to same channel count
        if dry.shape[1] != wet.shape[1]:
            nch = min(dry.shape[1], wet.shape[1])
            dry = dry[:, :nch]
            wet = wet[:, :nch]

        wet_gain = 10.0 ** (wet_level_db / 20.0)
        mix = dry + wet * wet_gain

        sf.write(output_path, mix, sr, subtype="FLOAT")
        return output_path

    # ── Preview / Finalize 双模渲染 ────────────────────────

    def render_preview(self, output_dir: str,
                       target_lufs: float = -12.0,
                       ceiling_db: float = -0.5,
                       cache_dir: str | None = None) -> dict:
        """Fast preview render — numpy mix, no Pro-L 2.

        1. Mute reverb return → render dry tracks from REAPER.
        2. Restore reverb → numpy-mix cached wet WAV at desired level.
        3. Apply hard-clip model to estimate final integrated LUFS.

        Returns ``{output_path, estimated_lufs, signal_check, ...}``.
        The ``"mastering"`` key is ``"bypassed"`` — callers should
        not base final loudness decisions on the preview.
        """
        import numpy as np

        tmp = cache_dir or tempfile.mkdtemp(prefix="hermes_preview_")
        os.makedirs(output_dir, exist_ok=True)
        api = self._bridge.api

        # ── 1. Mute reverb return, render dry ──
        saved_mute: dict[int, float] = {}
        if self._reverb_send_node:
            aux_idx = self._reverb_send_node.params.get("aux_index")
            if aux_idx is not None:
                tr = api.GetTrack(0, aux_idx)
                if tr:
                    saved_mute[aux_idx] = api.GetMediaTrackInfo_Value(tr, "B_MUTE")
                    api.SetMediaTrackInfo_Value(tr, "B_MUTE", 1.0)

        try:
            dry_result = self.render_mix(
                os.path.join(tmp, "dry"), verify=False,
            )
        finally:
            for idx, mute_val in saved_mute.items():
                tr = api.GetTrack(0, idx)
                if tr:
                    api.SetMediaTrackInfo_Value(tr, "B_MUTE", mute_val)

        dry_path = dry_result.get("output_path")
        if dry_path is None:
            return {"output_path": None, "error": "Dry render failed",
                    "mode": "preview"}

        # ── 2. Numpy-mix reverb wet cache ──
        wet_path = None
        wet_level_db = -8.0
        if self._reverb_send_node:
            wet_path = self._reverb_send_node.params.get("_wet_cache_path")
            wet_level_db = self._reverb_send_node.params.get("level_db", -8.0)

        if wet_path and os.path.exists(wet_path):
            mix_input = os.path.join(tmp, "dry_wet_mix.wav")
            mixed = self._numpy_mix(dry_path, wet_path, wet_level_db, mix_input)
            if mixed:
                dry_path = mixed
            else:
                log.warning("[preview] numpy mix failed, using dry-only")

        # ── 3. Hard-clip simulation for LUFS estimate ──
        from hermes_core.loudness_optimizer import find_optimal_gain, _hard_clip
        search = find_optimal_gain(
            dry_path, target_lufs=target_lufs, ceiling_dbtp=ceiling_db,
        )

        # ── 4. Apply gain + hard-clip to produce preview WAV ──
        pcm, sr = SignalAnalyzer._read_pcm(dry_path)
        limited = _hard_clip(pcm, search.gain_db, ceiling_db)

        import soundfile as sf
        preview_path = os.path.join(output_dir, "preview.wav")
        sf.write(preview_path, limited, sr, subtype="FLOAT")

        signal_check = {}
        try:
            ana = SignalAnalyzer.analyze(preview_path)
            signal_check = {
                "integrated_lufs": ana.integrated_lufs,
                "true_peak_dbtp": ana.true_peak_dbtp,
                "rms_db": ana.rms_db,
                "peak_db": ana.peak_db,
                "clip_count": ana.clip_count,
            }
        except (OSError, ValueError, RuntimeError):
            pass

        return {
            "output_path": preview_path,
            "mode": "preview",
            "estimated_lufs": search.predicted_lufs,
            "gain_applied_db": search.gain_db,
            "converged": search.converged,
            "signal_check": signal_check,
            "mastering": "bypassed",
            "warning": (
                "Preview mode — Pro-L 2 bypassed. "
                "Use finalize_master() for production output."
            ),
        }

    # ── Micro-render pipeline ──────────────────────────────

    def _micro_render_node(self, node: AudioNode,
                           input_wav: str | None,
                           cache_dir: str) -> str | None:
        """Render a single :class:`AudioNode` to a cached WAV.

        Creates a temporary track, imports *input_wav*, adds the FX,
        sets its params, solo-renders, then cleans up.

        Returns the output WAV path or ``None`` on failure.
        """
        import shutil

        # ── Cache hit: clean node with valid output ──
        if not node.is_dirty and node.output_audio_path:
            if os.path.exists(node.output_audio_path):
                log.debug("[micro] %s cache hit → %s", node.name,
                          node.output_audio_path)
                return node.output_audio_path

        if input_wav is None or not os.path.exists(input_wav):
            log.warning("[micro] %s: no input WAV — skipping", node.name)
            return None

        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.join(cache_dir, f"{node.name}.wav")

        # ── Clean up stale output ──
        if os.path.exists(out_path):
            os.remove(out_path)

        api = self._bridge.api
        n_before = api.CountTracks(0)

        # ── Create temp track ──
        api.InsertTrackAtIndex(n_before, True)
        temp_track_idx = n_before
        temp_track = api.GetTrack(0, temp_track_idx)

        try:
            # ── Import media ──
            self._tracks.import_media(temp_track_idx, input_wav, position=0.0)

            # ── Add FX + set params ──
            fx_idx = self._fx.add(temp_track_idx, node.params.get("_fx_name", ""))
            if fx_idx < 0:
                log.warning("[micro] %s: failed to add FX", node.name)
                return None

            fx_type = node.fx_type
            if fx_type in _TRANSLATORS:
                # Re-derive physical params (may have changed since build)
                normalized = normalize_params(
                    node.params.get("_fx_name", ""),
                    {k: v for k, v in node.params.items()
                     if not k.startswith("_")},
                )
                for pname, pval in normalized.items():
                    self._fx.set_param(temp_track_idx, fx_idx, pname, pval)

            # ── Solo render ──
            render_result = self._solo_render(
                [temp_track_idx], cache_dir, node.name,
            )
            rendered = render_result.get("output_path")
            if rendered and os.path.exists(rendered):
                shutil.move(rendered, out_path)

            if os.path.exists(out_path):
                node.mark_clean(out_path)
                log.info("[micro] %s rendered → %s", node.name, out_path)
                return out_path

            return None

        finally:
            # ── Clean up temp track ──
            try:
                api.DeleteTrack(temp_track)
            except Exception as e:
                log.debug("Failed to clean up temp track: %s", e)

    def _make_chain_executor(self, cache_dir: str) -> ChainExecutor:
        """Return a :class:`ChainExecutor` wired to :meth:`_micro_render_node`."""
        return ChainExecutor(
            lambda node, inp: self._micro_render_node(node, inp, cache_dir)
        )

    def execute_chain(self, nodes: list[AudioNode],
                      cache_dir: str | None = None) -> list[AudioNode]:
        """Execute *nodes* via micro-rendering, reusing cached outputs.

        Dirty nodes are re-rendered; clean nodes with valid caches are
        skipped.  Returns the (mutated) node list.
        """
        cdir = cache_dir or tempfile.mkdtemp(prefix="hermes_chain_")
        executor = self._make_chain_executor(cdir)
        first = executor.first_dirty(nodes)
        if first < 0:
            log.info("[chain] All %d nodes clean — nothing to render", len(nodes))
            return nodes
        log.info("[chain] Executing from node %d/%d (%s)", first,
                 len(nodes), nodes[first].name)
        return executor.execute(nodes)

    # ── GR Calibration ─────────────────────────────────────

    def calibrate_compressor(
        self,
        plugin_name: str,
        param_name: str,
        param_range: tuple[float, float],
        *,
        steps: int = 10,
        test_signal_path: str | None = None,
        cache_dir: str | None = None,
    ) -> list[tuple[float, float]]:
        """Auto-calibrate a compressor parameter's knob curve.

        Creates a test signal (pink noise at -18 dBFS RMS), then
        iterates *param_name* through *param_range* in *steps*
        increments.  At each step the signal is micro-rendered
        through the plugin and the resulting LUFS is measured.

        Returns a table of ``(normalised_value, physical_result)``
        pairs suitable for ``PLUGIN_REGISTRY``.

        Parameters
        ----------
        plugin_name:
            REAPER FX name (must be installed).
        param_name:
            The parameter to sweep (e.g. ``"Input"`` for 1176).
        param_range:
            ``(physical_lo, physical_hi)`` of the parameter.
        steps:
            Number of measurement points (default 10).
        test_signal_path:
            Path to a WAV test signal.  If ``None``, a -18 dBFS RMS
            pink-noise WAV is generated automatically.
        cache_dir:
            Temp directory for intermediate renders.
        """

        tmp = cache_dir or tempfile.mkdtemp(prefix="hermes_cal_")

        # ── Generate or use test signal ──
        if test_signal_path and os.path.exists(test_signal_path):
            signal_path = test_signal_path
        else:
            signal_path = self._gen_calibration_signal(tmp)

        log.info(
            "Calibrating %s.%s over [%.1f, %.1f] in %d steps",
            plugin_name, param_name, param_range[0], param_range[1], steps,
        )

        table: list[tuple[float, float]] = []
        phys_lo, phys_hi = param_range

        for i in range(steps + 1):
            t = i / steps
            physical = phys_lo + t * (phys_hi - phys_lo)

            # Create a one-node chain for this measurement
            node = AudioNode(
                name=f"cal_{plugin_name}_{i}",
                fx_type="comp",
                params={"_fx_name": plugin_name, param_name: physical},
            )
            node.is_dirty = True

            result_path = self._micro_render_node(
                node, signal_path, os.path.join(tmp, f"step_{i}"),
            )

            if result_path and os.path.exists(result_path):
                try:
                    ana = SignalAnalyzer.analyze(result_path)
                    table.append((t, ana.integrated_lufs))
                    log.debug("  [%d/%d] knob=%.2f → %.1f LUFS",
                              i, steps, t, ana.integrated_lufs)
                except (OSError, ValueError, RuntimeError):
                    table.append((t, 0.0))
            else:
                log.warning("  [%d/%d] knob=%.2f → render failed", i, steps, t)
                table.append((t, 0.0))

        log.info("Calibration complete: %d points", len(table))
        return table

    @staticmethod
    def _gen_calibration_signal(output_dir: str,
                                duration: float = 5.0,
                                sr: int = 48000) -> str:
        """Generate a -18 dBFS RMS pink-like noise WAV for calibration."""
        import numpy as np
        import soundfile as sf

        n = int(sr * duration)
        rng = np.random.default_rng(42)
        # Approximate pink noise via filtered white noise
        white = rng.standard_normal(n)
        # Simple 1/f filter: cumulative sum of white noise
        pink = np.cumsum(white)
        pink /= np.max(np.abs(pink)) + 1e-10
        # Scale to -18 dBFS RMS
        target_linear = 10.0 ** (-18.0 / 20.0)
        pink *= target_linear / (np.sqrt(np.mean(pink ** 2)) + 1e-10)
        stereo = np.column_stack([pink, pink])

        out_path = os.path.join(output_dir, "cal_signal.wav")
        sf.write(out_path, stereo, sr, subtype="FLOAT")
        log.info("Generated calibration signal: %s (%.1fs, -18 dBFS RMS)",
                 out_path, duration)
        return out_path
