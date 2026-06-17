"""
Plugin parameter normalisation layer.

Translates physical values (dB, ms, ratio) into 0.0–1.0 normalised
values that REAPER's API requires.  Each known plugin registers its
parameter ranges and curve types here — translators never deal with
0.0–1.0 directly.

Curve types
-----------
- ``"linear"`` — ``(value - lo) / (hi - lo)`` clamped to [0, 1].
- ``"table"``  — a list of ``(norm, physical)`` knots sorted ascending
  by physical value.  Reverse-lookup uses binary search + linear
  interpolation between the two nearest knots.

There is **no** generic ``"log"`` curve — every manufacturer uses a
different non-linear mapping (x², x³, piecewise-linear, …), so all
non-linear parameters are specified via explicit calibration tables.
"""

import bisect
import logging
from hermes_core.exceptions import UnregisteredPluginError, UnregisteredParamError

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# Internal helpers
# ════════════════════════════════════════════════════════════════


def _normalize_linear(value: float, lo: float, hi: float) -> float:
    """Map *value* from ``[lo, hi]`` to ``[0, 1]``, clamped."""
    if hi == lo:
        return 0.5
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def _normalize_from_table(value: float, table: list[tuple[float, float]]) -> float:
    """Reverse-lookup: given a physical *value*, find the corresponding
    normalised knob position via linear interpolation between the two
    nearest knots.

    The *table* must be a list of ``(normalised, physical)`` pairs
    sorted ascending by physical value.
    """
    if not table:
        raise ValueError("table must have at least one knot")
    if len(table) == 1:
        return table[0][0]

    phys = [row[1] for row in table]

    # Clamp to table bounds
    if value <= phys[0]:
        return table[0][0]
    if value >= phys[-1]:
        return table[-1][0]

    idx = bisect.bisect_left(phys, value)
    if idx == 0:
        return table[0][0]

    lo_n, lo_p = table[idx - 1]
    hi_n, hi_p = table[idx]

    if hi_p == lo_p:
        return lo_n

    t = (value - lo_p) / (hi_p - lo_p)
    return lo_n + t * (hi_n - lo_n)


# ════════════════════════════════════════════════════════════════
# Calibration tables (non-linear knob curves)
# ════════════════════════════════════════════════════════════════

# 1176 Input knob: normalised position → Equivalent Threshold (dBFS).
# Calibrated with a -18 dBFS RMS pink-noise source.  The "Equivalent
# Threshold" is the input level at which the compressor begins to show
# ~1 dB of gain reduction.  Because our clip-gain stage always feeds
# signals at -18 dBFS RMS, this table is signal-independent.
#
# Rows are sorted ascending by physical value (threshold dBFS) as
# required by _normalize_from_table.
_FET_1176_INPUT_TABLE: list[tuple[float, float]] = [
    (1.0,  -50.0),     # knob=1.00 → threshold ~ -50 dBFS (very sensitive)
    (0.90, -38.0),     # knob=0.90 → threshold ~ -38 dBFS
    (0.70, -26.0),     # knob=0.70 → threshold ~ -26 dBFS
    (0.50, -18.0),     # knob=0.50 → threshold ~ -18 dBFS (= clip-gain ref)
    (0.30, -10.0),     # knob=0.30 → threshold ~ -10 dBFS
    (0.15,  -2.0),     # knob=0.15 → threshold ~  -2 dBFS
    (0.0,   10.0),     # knob=0.00 → threshold ~ +10 dBFS (never compresses)
]

# 1176 Attack: normalised → time (ms).  REAPER's 1176-style plugins
# typically map this as a continuous knob 0…1 where lower = faster.
# Sorted ascending by physical value (ms).
_FET_1176_ATTACK_TABLE: list[tuple[float, float]] = [
    (0.0,    0.02),          # fastest
    (0.2,    0.2),
    (0.4,    0.8),
    (0.6,    2.0),
    (0.8,    5.0),
    (1.0,    8.0),           # slowest ("off" on some 1176 revisions)
]

# 1176 Release: normalised → time (ms).  Faster at low values.
_FET_1176_RELEASE_TABLE: list[tuple[float, float]] = [
    (0.0,  50.0),
    (0.25, 200.0),
    (0.5,  500.0),
    (0.75, 800.0),
    (1.0,  1200.0),
]

# Optical compressor (LA-2A style): Peak Reduction knob → target GR (dB).
# Like the 1176 Input, this is calibrated at -18 dBFS RMS input.
_OPTO_PEAK_REDUCTION_TABLE: list[tuple[float, float]] = [
    (0.0,  0.0),
    (0.25, 2.0),
    (0.5,  5.0),
    (0.7,  8.0),
    (0.85, 12.0),
    (1.0,  20.0),
]

# Generic Attack table (Pro-C 2 style).  Normalised → time (ms).
_GENERIC_ATTACK_TABLE: list[tuple[float, float]] = [
    (0.0,  0.01),
    (0.25, 1.0),
    (0.5,  5.0),
    (0.75, 30.0),
    (1.0,  100.0),
]

# Generic Release table (Pro-C 2 style).  Normalised → time (ms).
_GENERIC_RELEASE_TABLE: list[tuple[float, float]] = [
    (0.0,  10.0),
    (0.25, 50.0),
    (0.5,  150.0),
    (0.75, 500.0),
    (1.0,  1000.0),
]

# bx_townhouse Buss Compressor — stepped Attack (norm → ms).
# Verified via REAPER GUI readback (2026-06-01).
_BX_ATTACK_TABLE: list[tuple[float, float]] = [
    (0.0,  0.1),
    (0.2,  0.3),
    (0.4,  1.0),
    (0.6,  3.0),
    (0.8,  10.0),
    (1.0,  30.0),
]

# bx_townhouse stepped Release (norm → seconds).
# 1.0 = auto release (represented as 999.0 — effectively infinite).
_BX_RELEASE_TABLE: list[tuple[float, float]] = [
    (0.0,  0.1),
    (0.2,  0.3),
    (0.4,  0.6),
    (0.7,  1.2),
    (1.0,  999.0),  # auto
]

# ════════════════════════════════════════════════════════════════
# Bus compressor automation (bx_townhouse)
# ════════════════════════════════════════════════════════════════

# Empirical GR measurements at each attack step with offset=+1
# (thresh = peak + 1 dB, ratio = 2).  Measured 2026-06-01.
# Each entry: (attack_ms, gr_at_offset_plus1_db).
_BUS_ATTACK_GR_TABLE: list[tuple[float, float]] = [
    (0.1,   6.5),
    (0.3,   5.5),
    (1.0,   5.0),
    (3.0,   4.0),
    (10.0,  3.0),
    (30.0,  1.8),
]

# Genre → target bus compressor GR (dB).
_GENRE_BUS_GR_TARGET: dict[str, float] = {
    "electronic":              3.5,   # density and punch
    "pop":                     3.0,   # commercial loudness
    "rock":                    3.0,   # tight and punchy
    "chinese_folk_bel_canto":  2.5,   # majestic with weight
    "folk":                    2.0,   # light glue
    "ballad":                  2.0,   # gentle glue
}

# The bx_townhouse plugin name in PLUGIN_REGISTRY.
_BUS_COMPRESSOR_NAME: str = "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"

# Available attack steps (physical ms) for bx_townhouse.
_BX_ATTACK_STEPS: list[float] = [0.1, 0.3, 1.0, 3.0, 10.0, 30.0]


def _select_bus_attack(bpm: float | None = None,
                        genre: str = "pop") -> float:
    """Return bus compressor attack time — always 30 ms.

    The bx_townhouse SSL-style bus compressor shines at 30 ms attack
    for glue, regardless of BPM or genre.  Timing variation belongs
    in the per-track compressors, not the master bus glue.
    """
    return 30.0


def _bus_thresh_offset(attack_ms: float, target_gr_db: float) -> float:
    """Compute threshold offset for a given attack time and target GR.

    Uses the empirical :data:`_BUS_ATTACK_GR_TABLE` to interpolate the
    GR observed at ``offset = +1 dB`` for *attack_ms*, then scales
    proportionally to *target_gr_db*.

    ``thresh = peak + offset``.
    """
    # Linear interpolation in the GR-vs-attack table.
    phys = [row[0] for row in _BUS_ATTACK_GR_TABLE]
    grs = [row[1] for row in _BUS_ATTACK_GR_TABLE]

    idx = bisect.bisect_left(phys, attack_ms)
    if idx == 0:
        gr_at_1 = grs[0]
    elif idx >= len(phys):
        gr_at_1 = grs[-1]
    else:
        lo_ms, lo_gr = phys[idx - 1], grs[idx - 1]
        hi_ms, hi_gr = phys[idx], grs[idx]
        t = (attack_ms - lo_ms) / (hi_ms - lo_ms)
        gr_at_1 = lo_gr + t * (hi_gr - lo_gr)

    # Proportional scaling: offset = 1.0 × (GR_at_offset_1 / target_gr).
    # Faster attack → bigger GR_at_1 → bigger offset needed to hit target.
    if target_gr_db <= 0:
        return 999.0  # no compression → effectively no threshold
    offset = 1.0 * (gr_at_1 / target_gr_db)
    return round(offset, 2)


def _snap_bx_attack(desired_ms: float) -> float:
    """Snap *desired_ms* to the nearest available bx_townhouse attack step."""
    return min(_BX_ATTACK_STEPS, key=lambda s: abs(s - desired_ms))


def compute_bus_compressor_params(
    peak_db: float,
    bpm: float | None = None,
    genre: str = "pop",
) -> dict[str, float]:
    """Compute bx_townhouse physical parameters for bus compression.

    The automation chain::

        Target GR = h(genre)
        Attack    = g(bpm, genre)
        Thresh    = peak + f(attack, target_gr)
        MakeUp    = target_gr × 0.5

    Returns a dict of physical values ready for :func:`normalize_params`.
    """
    target_gr_db = _GENRE_BUS_GR_TARGET.get(genre, 2.0)
    desired_attack_ms = _select_bus_attack(bpm, genre)
    attack_ms = _snap_bx_attack(desired_attack_ms)
    offset = _bus_thresh_offset(attack_ms, target_gr_db)
    thresh_db = round(peak_db + offset, 1)
    makeup_db = round(target_gr_db * 0.5, 1)

    return {
        "Comp In":  1.0,
        "Thresh":   thresh_db,
        "Ratio":    2.0,
        "Attack":   attack_ms,
        "Release":  999.0,  # auto
        "MakeUp":   makeup_db,
        "Mix":      1.0,
        "Wet":      1.0,
        "_target_gr": target_gr_db,  # metadata — pop before normalize_params()
    }


# ════════════════════════════════════════════════════════════════
# Plugin registry
# ════════════════════════════════════════════════════════════════

PLUGIN_REGISTRY: dict[str, dict] = {
    # ── VCA / digital compressors ──────────────────────
    "FabFilter Pro-C 2 (FabFilter)": {
        "type": "vca",
        "params": {
            "Threshold":   {"range": (-60.0, 0.0),    "curve": "linear"},
            "Ratio":       {"range": (1.0, 20.0),      "curve": "linear"},
            "Attack":      {"table": _GENERIC_ATTACK_TABLE},
            "Release":     {"table": _GENERIC_RELEASE_TABLE},
            "Knee":        {"range": (0.0, 24.0),      "curve": "linear"},
            "Range":       {"range": (0.0, 48.0),      "curve": "linear"},
            "Makeup Gain": {"range": (-20.0, 20.0),    "curve": "linear"},
        },
    },

    # ── bx_townhouse Buss Compressor (Plugin Alliance) ──
    # SSL-style VCA bus compressor.  Calibrated 2026-06-01.
    "VST3: bx_townhouse Buss Compressor (Plugin Alliance)": {
        "type": "vca",
        "params": {
            "Comp In":  {"range": (0.0, 1.0),   "curve": "linear"},
            "Thresh":   {"range": (-20.0, 10.0), "curve": "linear"},
            "Ratio":    {"range": (1.0, 10.0),   "curve": "linear"},
            "Attack":   {"table": _BX_ATTACK_TABLE},
            "Release":  {"table": _BX_RELEASE_TABLE},
            "MakeUp":   {"range": (0.0, 15.0),   "curve": "linear"},
            "Mix":      {"range": (0.0, 1.0),    "curve": "linear"},
            "Wet":      {"range": (0.0, 1.0),    "curve": "linear"},
        },
    },

    # ── FET compressors ────────────────────────────────
    "Universal Audio 1176LN (Universal Audio)": {
        "type": "fet",
        "params": {
            "Input":    {"table": _FET_1176_INPUT_TABLE},
            "Output":   {"range": (-24.0, 24.0), "curve": "linear"},
            "Attack":   {"table": _FET_1176_ATTACK_TABLE},
            "Release":  {"table": _FET_1176_RELEASE_TABLE},
        },
    },
    "Universal Audio 1176AE (Universal Audio)": {
        "type": "fet",
        "params": {
            "Input":    {"table": _FET_1176_INPUT_TABLE},
            "Output":   {"range": (-24.0, 24.0), "curve": "linear"},
            "Attack":   {"table": _FET_1176_ATTACK_TABLE},
            "Release":  {"table": _FET_1176_RELEASE_TABLE},
        },
    },

    # ── Waves CLA-76 (FET, VST3 swept 2026-06-08) ──────
    # Input/Output: piecewise-linear -55.2~0 dB (拐点 -36)
    # Attack/Release: 1~7 线性, CW=快
    "VST3: CLA-76 Mono (Waves)": {
        "type": "fet",
        "params": {
            "Input":    {"range": (-55.2, 0.0), "curve": "linear"},
            "Output":   {"range": (-55.2, 0.0), "curve": "linear"},
            "Attack":   {"range": (1.0, 7.0),    "curve": "linear"},
            "Release":  {"range": (1.0, 7.0),    "curve": "linear"},
        },
    },

    # ── Optical compressors ────────────────────────────
    "Universal Audio LA-2A (Universal Audio)": {
        "type": "opto",
        "params": {
            "Peak Reduction": {"table": _OPTO_PEAK_REDUCTION_TABLE},
            "Gain":           {"range": (-12.0, 20.0), "curve": "linear"},
        },
    },
    "Softube CL-1B (Softube)": {
        "type": "opto",
        "params": {
            "Threshold": {"range": (-40.0, 0.0), "curve": "linear"},
            "Ratio":     {"range": (2.0, 10.0),  "curve": "linear"},
            "Attack":    {"table": _GENERIC_ATTACK_TABLE},
            "Release":   {"table": _GENERIC_RELEASE_TABLE},
            "Gain":      {"range": (-20.0, 20.0), "curve": "linear"},
        },
    },

    # Shadow Hills Mastering Compressor — 光电压缩 + 离散压缩双级。
    # 人声链仅使用光学级（离散级 bypass），Iron 变压器染色。
    # 所有参数 0.0–1.0（VST3 内置归一化）。
    "VST3: Shadow Hills Mastering Compressor (Plugin Alliance)": {
        "type": "opto",
        "params": {
            "Hardwire Bypass":      {"range": (0.0, 1.0), "curve": "linear"},
            "Optical Bypass 1":     {"range": (0.0, 1.0), "curve": "linear"},
            "Optical Threshold 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "Optical Gain 1":       {"range": (0.0, 1.0), "curve": "linear"},
            "Discrete Bypass 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "Discrete Ratio 1":     {"range": (0.0, 1.0), "curve": "linear"},
            "Discrete Attack 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "Discrete Recover 1":   {"range": (0.0, 1.0), "curve": "linear"},
            "Discrete Gain 1":      {"range": (0.0, 1.0), "curve": "linear"},
            "Sidechain Filter 1":   {"range": (0.0, 1.0), "curve": "linear"},
            "Transformer 1":        {"range": (0.0, 1.0), "curve": "linear"},
            "Sidechain HP Freq":    {"range": (0.0, 1.0), "curve": "linear"},
            "Mix":                  {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Vocal-specific compressors ─────────────────────
    "VST3: RVox Mono (Waves)": {
        "type": "rvox",
        "params": {
            "Compression": {"range": (-36.0, 0.0), "curve": "linear"},
            "Gate":        {"range": (-120.0, 0.0), "curve": "linear"},
            "Gain":        {"range": (-36.0, 0.0), "curve": "linear"},
        },
    },
    "Waves RCompressor (Waves)": {
        "type": "vca",
        "params": {
            "Threshold": {"range": (-60.0, 0.0), "curve": "linear"},
            "Ratio":     {"range": (1.0, 20.0), "curve": "linear"},
            "Attack":    {"table": _GENERIC_ATTACK_TABLE},
            "Release":   {"table": _GENERIC_RELEASE_TABLE},
            "Gain":      {"range": (-20.0, 20.0), "curve": "linear"},
        },
    },

    # ── Bus / SSL-style compressor ─────────────────────
    "Waves SSL G-Master Buss Compressor (Waves)": {
        "type": "vca",
        "params": {
            "Threshold":  {"range": (-20.0, 10.0), "curve": "linear"},
            "Ratio":      {"range": (2.0, 10.0),   "curve": "linear"},
            "Attack":     {"table": [(0.0,0.1),(0.33,1.0),(0.67,10.0),(1.0,30.0)]},
            "Release":    {"table": [(0.0,0.1),(0.33,0.3),(0.67,0.6),(1.0,1.2)]},
            "Makeup":     {"range": (-5.0, 15.0), "curve": "linear"},
        },
    },

    # ── FabFilter Pro-L 2 (already normalised in engine, listed for completeness) ──
    "VST: FabFilter Pro-L 2 (FabFilter)": {
        "type": "limiter",
        "params": {
            "Gain":          {"range": (0.0, 30.0),  "curve": "linear"},
            "Output Level":  {"range": (-30.0, 0.0), "curve": "linear"},
        },
    },

    # ── 母带插件 ──────────────────────────────────────────
    # bx_2098 EQ (Plugin Alliance) — 四段母带 EQ（LF/LMF/HMF/HF）+ M/S + THD
    # 每通道：LF Shelf + LMF Bell + HMF Bell + HF Shelf
    "VST3: bx_2098 EQ (Plugin Alliance)": {
        "type": "eq_mastering",
        "params": {
            # ── Channel 1 ──
            "HPF Frequency 1":  {"range": (10.0, 400.0),    "curve": "linear"},
            "HPF In 1":         {"range": (0.0, 1.0),       "curve": "linear"},
            "LF In 1":          {"range": (0.0, 1.0),       "curve": "linear"},
            "LF Frequency 1":   {"range": (10.0, 400.0),    "curve": "linear"},
            "LF Gain 1":        {"range": (-5.0, 5.0),      "curve": "linear"},
            "LF Glow 1":        {"range": (0.0, 1.0),       "curve": "linear"},
            "LMF In 1":         {"range": (0.0, 1.0),       "curve": "linear"},
            "LMF Frequency 1":  {"range": (100.0, 2000.0),  "curve": "linear"},
            "LMF Gain 1":       {"range": (-5.0, 5.0),      "curve": "linear"},
            "LMF Q 1":          {"range": (0.1, 5.0),       "curve": "linear"},
            "LMF Notch 1":      {"range": (0.0, 1.0),       "curve": "linear"},
            "HMF In 1":         {"range": (0.0, 1.0),       "curve": "linear"},
            "HMF Frequency 1":  {"range": (800.0, 12000.0), "curve": "linear"},
            "HMF Gain 1":       {"range": (-5.0, 5.0),      "curve": "linear"},
            "HMF Q 1":          {"range": (0.1, 5.0),       "curve": "linear"},
            "HMF Notch 1":      {"range": (0.0, 1.0),       "curve": "linear"},
            "HF In 1":          {"range": (0.0, 1.0),       "curve": "linear"},
            "HF Frequency 1":   {"range": (2500.0, 40000.0),"curve": "linear"},
            "HF Gain 1":        {"range": (-5.0, 5.0),      "curve": "linear"},
            "HF Sheen 1":       {"range": (0.0, 1.0),       "curve": "linear"},
            # ── Channel 2（与 Ch1 同步）──
            "HPF Frequency 2":  {"range": (10.0, 400.0),    "curve": "linear"},
            "HPF In 2":         {"range": (0.0, 1.0),       "curve": "linear"},
            "LF In 2":          {"range": (0.0, 1.0),       "curve": "linear"},
            "LF Frequency 2":   {"range": (10.0, 400.0),    "curve": "linear"},
            "LF Gain 2":        {"range": (-5.0, 5.0),      "curve": "linear"},
            "LF Glow 2":        {"range": (0.0, 1.0),       "curve": "linear"},
            "LMF In 2":         {"range": (0.0, 1.0),       "curve": "linear"},
            "LMF Frequency 2":  {"range": (100.0, 2000.0),  "curve": "linear"},
            "LMF Gain 2":       {"range": (-5.0, 5.0),      "curve": "linear"},
            "LMF Q 2":          {"range": (0.1, 5.0),       "curve": "linear"},
            "LMF Notch 2":      {"range": (0.0, 1.0),       "curve": "linear"},
            "HMF In 2":         {"range": (0.0, 1.0),       "curve": "linear"},
            "HMF Frequency 2":  {"range": (800.0, 12000.0), "curve": "linear"},
            "HMF Gain 2":       {"range": (-5.0, 5.0),      "curve": "linear"},
            "HMF Q 2":          {"range": (0.1, 5.0),       "curve": "linear"},
            "HMF Notch 2":      {"range": (0.0, 1.0),       "curve": "linear"},
            "HF In 2":          {"range": (0.0, 1.0),       "curve": "linear"},
            "HF Frequency 2":   {"range": (2500.0, 40000.0),"curve": "linear"},
            "HF Gain 2":        {"range": (-5.0, 5.0),      "curve": "linear"},
            "HF Sheen 2":       {"range": (0.0, 1.0),       "curve": "linear"},
            # ── 全局 ──
            "Mid/Side":         {"range": (0.0, 1.0),       "curve": "linear"},
            "Mono Maker In":    {"range": (0.0, 1.0),       "curve": "linear"},
            "Mono Maker":       {"range": (20.0, 20000.0),  "curve": "linear"},
            "THD In":           {"range": (0.0, 1.0),       "curve": "linear"},
            "THD":              {"range": (0.0, 1.0),       "curve": "linear"},
        },
    },

    # The God Particle (Cradle) — Jaycen Joshua 母带链
    # 设计理念："穿过去就好"，Input 驱动整体谐波/压缩/宽度
    "VST3: The God Particle (Cradle)": {
        "type": "god_particle",
        "params": {
            "Input Gain":          {"range": (0.0, 1.0), "curve": "linear"},
            "Output Gain":         {"range": (0.0, 1.0), "curve": "linear"},
            "EQ Low Gain":         {"range": (0.0, 1.0), "curve": "linear"},
            "EQ Mid Gain":         {"range": (0.0, 1.0), "curve": "linear"},
            "EQ High Gain":        {"range": (0.0, 1.0), "curve": "linear"},
            "EQ Bypass":           {"range": (0.0, 1.0), "curve": "linear"},
            "Character":           {"range": (0.0, 1.0), "curve": "linear"},
            "Character Bypass":    {"range": (0.0, 1.0), "curve": "linear"},
            "Limiter Input Gain":  {"range": (0.0, 1.0), "curve": "linear"},
            "Limiter Bypass":      {"range": (0.0, 1.0), "curve": "linear"},
            "Bypass":              {"range": (0.0, 1.0), "curve": "linear"},
            "Wet":                 {"range": (0.0, 1.0), "curve": "linear"},
            "Delta":               {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── EQs ────────────────────────────────────────────
    # Pro-Q 3 verified via reapy readback (2026-05-31).
    # All per-band params are pre-normalised (0–1) by _apply_proq3_eq().
    # Global params (Output Level) are also pre-normalised.
    #
    # Verified curves:
    #   Frequency:  log10(f/10) / log10(3000)          10 Hz – 30 kHz
    #   Gain:       (dB+30) / 60                       -30 – +30 dB
    #   Q:          log10(Q/0.025) / log10(1600)       0.025 – 40
    #   Shape:      enum / 8                           0=Bell..7=Tilt
    #   Slope:      enum / 9                           0=6..9=96 dB/oct
    #   Dynamic Range: (dB+30) / 60                    -30 – +30 dB
    #   Threshold:  (dB+60) / 60                       -60 – 0 dB
    #   Output Level: (dB+36) / 72                     -36 – +36 dB
    "VST: FabFilter Pro-Q 3 (FabFilter)": {
        "type": "eq",
        "params": {
            **{
                f"Band {n} Used":      {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            **{
                f"Band {n} Enabled":   {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            **{
                f"Band {n} Frequency": {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            **{
                f"Band {n} Gain":      {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            **{
                f"Band {n} Q":         {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            **{
                f"Band {n} Shape":     {"range": (0.0, 1.0), "curve": "linear"}
                for n in range(1, 9)
            },
            # Global
            "Output Level":            {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    # ── SSL EQ (post-comp tonal shaping) ─────────────────
    # Verified via reapy readback (2026-05-31).
    # All params 0–1 pass-through — pre-normalised by _apply_ssleq_eq().
    #
    # Gain curves:
    #   LF/HF Gain:  (dB + 17) / 34    ±17 dB
    #   LMF/HMF Gain: (dB + 20) / 40   ±20 dB
    #   Output Gain:  (dB + 12) / 24   ±12 dB
    # Frequency: stepped controls — lookup tables
    # Q: 0.1 (widest=1.0) – 3.5 (narrowest=0.0), reverse-linear
    # Switches: 0/1 (HP On, LMF Div3, HMF Mul3, Analog, EQ IN)
    "VST3: SSLEQ Mono (Waves)": {
        "type": "eq",
        "params": {
            "Bypass":       {"range": (0.0, 1.0), "curve": "linear"},
            "HP On/Off":    {"range": (0.0, 1.0), "curve": "linear"},
            "LF Gain":      {"range": (-17.0, 17.0), "curve": "linear"},
            "LMF Gain":     {"range": (-20.0, 20.0), "curve": "linear"},
            "LMF Div3":     {"range": (0.0, 1.0), "curve": "linear"},
            "HMF Mul3":     {"range": (0.0, 1.0), "curve": "linear"},
            "HMF Gain":     {"range": (-20.0, 20.0), "curve": "linear"},
            "HF Gain":      {"range": (-17.0, 17.0), "curve": "linear"},
            "Gain":         {"range": (-12.0, 12.0), "curve": "linear"},
            "HP Frq":       {"range": (0.0, 1.0), "curve": "linear"},
            "LF Frq":       {"range": (0.0, 1.0), "curve": "linear"},
            "LMF Q":        {"range": (0.0, 1.0), "curve": "linear"},
            "LMF Frq":      {"range": (0.0, 1.0), "curve": "linear"},
            "HMF Q":        {"range": (0.0, 1.0), "curve": "linear"},
            "HMF Frq":      {"range": (0.0, 1.0), "curve": "linear"},
            "HF Frq":       {"range": (0.0, 1.0), "curve": "linear"},
            "Analog":       {"range": (0.0, 1.0), "curve": "linear"},
            "EQ IN":        {"range": (0.0, 1.0), "curve": "linear"},
            "Phase Rev":    {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    # ReaEQ: 4 bands for basic EQ tasks.  Band Types:
    # 0=High Pass, 1=Low Shelf, 2=Bell, 3=High Shelf, 4=Low Pass.
    # Bettermaker EQ232D — Pultec 风格双通道均衡器。
    # 参数全范围 0.0–1.0（VST3 内置归一化），curve=linear 做 pass-through。
    "VST3: Bettermaker EQ232D (Plugin Alliance)": {
        "type": "eq",
        "params": {
            # ── Channel 1 ──
            "ENGAGE 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "HPF IN 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "HPF FREQ 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "EQ1 IN 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "EQ1 GAIN 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "EQ1 Q 1":     {"range": (0.0, 1.0), "curve": "linear"},
            "EQ1 FREQ 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "EQ2 IN 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "EQ2 GAIN 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "EQ2 Q 1":     {"range": (0.0, 1.0), "curve": "linear"},
            "EQ2 FREQ 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "PEQ IN 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "LO BOOST 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "LO ATTEN 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "LO CPS 1":    {"range": (0.0, 1.0), "curve": "linear"},
            "HI BOOST 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "HI ATTEN 1":  {"range": (0.0, 1.0), "curve": "linear"},
            "HI BW 1":     {"range": (0.0, 1.0), "curve": "linear"},
            "KCS BST 1":   {"range": (0.0, 1.0), "curve": "linear"},
            "KCS ATT 1":   {"range": (0.0, 1.0), "curve": "linear"},
            "LVL OUT 1":   {"range": (0.0, 1.0), "curve": "linear"},
            # ── Channel 2 ──
            "ENGAGE 2":    {"range": (0.0, 1.0), "curve": "linear"},
            # ── 全局 ──
            "CHANNEL":     {"range": (0.0, 1.0), "curve": "linear"},
            "MS MATRIX":   {"range": (0.0, 1.0), "curve": "linear"},
            # 其余 VST3 参数（SNAPSHOT, Program, Bypass, Wet, Delta 等）
            # 无需注册——由 REAPER 默认处理
        },
    },
    "ReaEQ (Cockos)": {
        "type": "eq",
        "params": {
            **{
                f"Band {n} Freq":    {"range": (20.0, 20000.0), "curve": "linear"}
                for n in range(1, 5)
            },
            **{
                f"Band {n} Gain":    {"range": (-24.0, 24.0),   "curve": "linear"}
                for n in range(1, 5)
            },
            **{
                f"Band {n} Q":       {"range": (0.01, 10.0),    "curve": "linear"}
                for n in range(1, 5)
            },
            **{
                f"Band {n} Type":    {"range": (0.0, 5.0),      "curve": "linear"}
                for n in range(1, 5)
            },
            **{
                f"Band {n} Enabled": {"range": (0.0, 1.0),      "curve": "linear"}
                for n in range(1, 5)
            },
        },
    },

    # ── FabFilter Pro-DS (de-esser) ──────────────────
    # Param ranges from FabFilter Pro-DS manual / REAPER readback.
    "VST: FabFilter Pro-DS (FabFilter)": {
        "type": "deesser",
        "params": {
            "Mode":                   {"range": (0.0, 1.0),    "curve": "linear"},
            "Band Processing":        {"range": (0.0, 1.0),    "curve": "linear"},
            "Threshold":              {"range": (-60.0, 0.0),  "curve": "linear"},  # verified via GUI readback
            "Range":                  {"range": (0.0, 24.0),   "curve": "linear"},  # verified via GUI readback
            "Lookahead":              {"range": (0.0, 15.0),   "curve": "linear"},  # verified in GUI
            "Lookahead Enabled":      {"range": (0.0, 1.0),    "curve": "linear"},
            "High-Pass Frequency":    {"range": (0.0, 1.0), "curve": "linear"},
            "Low-Pass Frequency":     {"range": (0.0, 1.0), "curve": "linear"},
            "Input Level":            {"range": (-30.0, 30.0), "curve": "linear"},
            "Output Level":           {"range": (-30.0, 30.0), "curve": "linear"},
            "Wet":                    {"range": (0.0, 1.0),    "curve": "linear"},
        },
    },

    # ── Decapitator (Soundtoys) — 谐波饱和 ────────────────
    # Style: A=0.0, E=0.25, N=0.5, T=0.75, P=1.0
    # 所有参数 0.0–1.0（VST 内置归一化）。
    "VST: Decapitator (Soundtoys)": {
        "type": "saturation",
        "params": {
            "Style":        {"range": (0.0, 1.0), "curve": "linear"},
            "Drive":        {"range": (0.0, 1.0), "curve": "linear"},
            "Punish":       {"range": (0.0, 1.0), "curve": "linear"},
            "LowCut":       {"range": (0.0, 1.0), "curve": "linear"},
            "Tone":         {"range": (0.0, 1.0), "curve": "linear"},
            "HighCut":      {"range": (0.0, 1.0), "curve": "linear"},
            "Mix":          {"range": (0.0, 1.0), "curve": "linear"},
            "AutoGain":     {"range": (0.0, 1.0), "curve": "linear"},
            "LowThump":     {"range": (0.0, 1.0), "curve": "linear"},
            "HighSlope":    {"range": (0.0, 1.0), "curve": "linear"},
            "OutputTrim":   {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Oxford Inflator (Sonnox) — 谐波密度 ────────────────
    "VST3: Oxford Inflator (Sonnox)": {
        "type": "harmonic",
        "params": {
            "Input Gain":   {"range": (0.0, 1.0), "curve": "linear"},
            "Effect":       {"range": (0.0, 1.0), "curve": "linear"},
            "Curve":        {"range": (0.0, 1.0), "curve": "linear"},
            "Output Gain":  {"range": (0.0, 1.0), "curve": "linear"},
            "In":           {"range": (0.0, 1.0), "curve": "linear"},
            "Band Split":   {"range": (0.0, 1.0), "curve": "linear"},
            "Clip 0dB":     {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Maag EQ4 (Plugin Alliance) — Air Band 空气感 ─────
    "VST3: Maag EQ4 (Plugin Alliance)": {
        "type": "air_eq",
        "params": {
            "Sub":          {"range": (0.0, 1.0), "curve": "linear"},
            "40 Hz":        {"range": (0.0, 1.0), "curve": "linear"},
            "160 Hz":       {"range": (0.0, 1.0), "curve": "linear"},
            "650 Hz":       {"range": (0.0, 1.0), "curve": "linear"},
            "2.5 kHz":      {"range": (0.0, 1.0), "curve": "linear"},
            "Air Gain":     {"range": (0.0, 1.0), "curve": "linear"},
            "Air Band":     {"range": (0.0, 1.0), "curve": "linear"},
            "Level Trim":   {"range": (0.0, 1.0), "curve": "linear"},
            "In/Out":       {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Reverbs ────────────────────────────────────────
    "ValhallaVintageVerb (Valhalla DSP)": {
        "type": "reverb",
        "params": {
            "Mix":   {"range": (0.0, 100.0), "curve": "linear"},
            "Decay": {"range": (0.05, 70.0), "curve": "linear"},
        },
    },
    "VST3: ValhallaVintageVerb (Valhalla DSP, LLC)": {
        "type": "reverb",
        "params": {
            "Mix":            {"range": (0.0, 1.0), "curve": "linear"},
            "PreDelay":       {"range": (0.0, 1.0), "curve": "linear"},
            "Decay":          {"range": (0.0, 1.0), "curve": "linear"},
            "Size":           {"range": (0.0, 1.0), "curve": "linear"},
            "Attack":         {"range": (0.0, 1.0), "curve": "linear"},
            "BassMult":       {"range": (0.0, 1.0), "curve": "linear"},
            "BassXover":      {"range": (0.0, 1.0), "curve": "linear"},
            "HighShelf":      {"range": (0.0, 1.0), "curve": "linear"},
            "HighFreq":       {"range": (0.0, 1.0), "curve": "linear"},
            "EarlyDiffusion": {"range": (0.0, 1.0), "curve": "linear"},
            "LateDiffusion":  {"range": (0.0, 1.0), "curve": "linear"},
            "ModRate":        {"range": (0.0, 1.0), "curve": "linear"},
            "ModDepth":       {"range": (0.0, 1.0), "curve": "linear"},
            "HighCut":        {"range": (0.0, 1.0), "curve": "linear"},
            "LowCut":         {"range": (0.0, 1.0), "curve": "linear"},
            "ColorMode":      {"range": (0.0, 1.0), "curve": "linear"},
            "ReverbMode":     {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "ReaVerbate (Cockos)": {
        "type": "reverb",
        "params": {},
    },

    # ── MicroShift (Soundtoys) — 立体声展宽 ─────────────
    "VST3: MicroShift (Soundtoys)": {
        "type": "doubler",
        "params": {
            "Mix":       {"range": (0.0, 1.0), "curve": "linear"},
            "InputGain": {"range": (0.0, 1.0), "curve": "linear"},
            "Detune":    {"range": (0.0, 1.0), "curve": "linear"},
            "Delay":     {"range": (0.0, 1.0), "curve": "linear"},
            "Focus":     {"range": (0.0, 1.0), "curve": "linear"},
            "Style":     {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Spatial: Reverbs ────────────────────────────────
    "VST: Little Plate (Soundtoys)": {
        "type": "reverb",
        "params": {
            "Mix":        {"range": (0.0, 1.0), "curve": "linear"},
            "Decay":      {"range": (0.0, 1.0), "curve": "linear"},
            "Low Cut":    {"range": (0.0, 1.0), "curve": "linear"},
            "Mod Enable": {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST: ValhallaPlate (Valhalla DSP, LLC)": {
        "type": "reverb",
        "params": {
            "Mix":        {"range": (0.0, 1.0), "curve": "linear"},
            "PreDelay":   {"range": (0.0, 1.0), "curve": "linear"},
            "Decay":      {"range": (0.0, 1.0), "curve": "linear"},
            "Size":       {"range": (0.0, 1.0), "curve": "linear"},
            "Width":      {"range": (0.0, 1.0), "curve": "linear"},
            "ModRate":    {"range": (0.0, 1.0), "curve": "linear"},
            "ModDepth":   {"range": (0.0, 1.0), "curve": "linear"},
            "LowEQFreq":  {"range": (0.0, 1.0), "curve": "linear"},
            "LowEQGain":  {"range": (0.0, 1.0), "curve": "linear"},
            "HighEQFreq": {"range": (0.0, 1.0), "curve": "linear"},
            "HighEQGain": {"range": (0.0, 1.0), "curve": "linear"},
            "Type":       {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST: ValhallaRoom (Valhalla DSP, LLC)": {
        "type": "reverb",
        "params": {
            "mix":            {"range": (0.0, 1.0), "curve": "linear"},
            "predelay":       {"range": (0.0, 1.0), "curve": "linear"},
            "decay":          {"range": (0.0, 1.0), "curve": "linear"},
            "HiCut":          {"range": (0.0, 1.0), "curve": "linear"},
            "LoCut":          {"range": (0.0, 1.0), "curve": "linear"},
            "earlyLateMix":   {"range": (0.0, 1.0), "curve": "linear"},
            "lateSize":       {"range": (0.0, 1.0), "curve": "linear"},
            "earlySize":      {"range": (0.0, 1.0), "curve": "linear"},
            "diffusion":      {"range": (0.0, 1.0), "curve": "linear"},
            "RTBassMultiply": {"range": (0.0, 1.0), "curve": "linear"},
            "RTHighMultiply": {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST3: LX480 v4 (Relab Development)": {
        "type": "reverb",
        "params": {
            # E1 引擎参数 + 全局参数
            "E1: Algorithm":                    {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Size (SIZ)":                   {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Reverb Time Mid (RTM)":        {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Shape (SHP)":                  {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Spread (SPR)":                 {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Pre Delay (PDL)":              {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Width (WID)":                  {"range": (0.0, 1.0), "curve": "linear"},
            "E1: High Frequency Cutoff (HFC)":  {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Low Frequency Cutoff (LFC)":   {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Diffusion (DIF)":              {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Bass Multiply (BAS)":          {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Decay Optimization (DCO)":     {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Mix (MIX)":                    {"range": (0.0, 1.0), "curve": "linear"},
            "E1: Reverb Mode (MOD)":            {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST: FabFilter Pro-R 2 (FabFilter)": {
        "type": "reverb",
        "params": {
            "Space":        {"range": (0.0, 1.0), "curve": "linear"},
            "Decay Rate":   {"range": (0.0, 1.0), "curve": "linear"},
            "Distance":     {"range": (0.0, 1.0), "curve": "linear"},
            "Brightness":   {"range": (0.0, 1.0), "curve": "linear"},
            "Style":        {"range": (0.0, 1.0), "curve": "linear"},
            "Character":    {"range": (0.0, 1.0), "curve": "linear"},
            "Thickness":    {"range": (0.0, 1.0), "curve": "linear"},
            "Stereo Width": {"range": (0.0, 1.0), "curve": "linear"},
            "Ducking":      {"range": (0.0, 1.0), "curve": "linear"},
            "Mix":          {"range": (0.0, 1.0), "curve": "linear"},
            "Predelay":     {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST3: REV6000 (Relab Development)": {
        "type": "reverb",
        "params": {
            "Dry Level":     {"range": (0.0, 1.0), "curve": "linear"},
            "Early Level":   {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Level":  {"range": (0.0, 1.0), "curve": "linear"},
            "Decay":         {"range": (0.0, 1.0), "curve": "linear"},
            "Pre Delay":     {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Size":   {"range": (0.0, 1.0), "curve": "linear"},
            "Lo Cut":        {"range": (0.0, 1.0), "curve": "linear"},
            "Hi Cut":        {"range": (0.0, 1.0), "curve": "linear"},
            "Modulation Rate":  {"range": (0.0, 1.0), "curve": "linear"},
            "Modulation Depth": {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Type":   {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST3: Supernova (Nuro Audio)": {
        "type": "reverb",
        "params": {
            "Global Mix":        {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Decay Time": {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Depth":      {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Pre-Delay":  {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Low Color":  {"range": (0.0, 1.0), "curve": "linear"},
            "Reverb Hi Color":   {"range": (0.0, 1.0), "curve": "linear"},
            "Delay Rate":        {"range": (0.0, 1.0), "curve": "linear"},
            "Delay Feedback":    {"range": (0.0, 1.0), "curve": "linear"},
            "Duck Amount":       {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST3: UAD EMT 140 (Universal Audio)": {
        "type": "reverb",
        "params": {
            "Plate":   {"range": (0.0, 1.0), "curve": "linear"},   # 板类型 (A/B/C)
            "DampA":   {"range": (0.0, 1.0), "curve": "linear"},   # 阻尼 A
            "DampB":   {"range": (0.0, 1.0), "curve": "linear"},   # 阻尼 B
            "DampC":   {"range": (0.0, 1.0), "curve": "linear"},   # 阻尼 C
            "EQ":      {"range": (0.0, 1.0), "curve": "linear"},   # EQ 开关
            "LFreq":   {"range": (0.0, 1.0), "curve": "linear"},   # 低频频率
            "LGain":   {"range": (0.0, 1.0), "curve": "linear"},   # 低频增益
            "HFreq":   {"range": (0.0, 1.0), "curve": "linear"},   # 高频频率
            "HGain":   {"range": (0.0, 1.0), "curve": "linear"},   # 高频增益
            "PreDly":  {"range": (0.0, 1.0), "curve": "linear"},   # 预延迟
            "ModRate": {"range": (0.0, 1.0), "curve": "linear"},   # 调制速率
            "ModDepth":{"range": (0.0, 1.0), "curve": "linear"},   # 调制深度
            "Width":   {"range": (0.0, 1.0), "curve": "linear"},   # 立体声宽度
            "Mix":     {"range": (0.0, 1.0), "curve": "linear"},   # 干湿比
            "LowCut":  {"range": (0.0, 1.0), "curve": "linear"},   # 低切
        },
    },

    # ── Seventh Heaven Professional (LiquidSonics) ──────
    # Bricasti M7 仿真，Room Verb 首选插件。
    # 参数扫描: 2026-06-12 REAPER TrackFX_GetParamName (71 params)
    "VST3: Seventh Heaven Professional (LiquidSonics)": {
        "type": "reverb",
        "params": {
            "Master Gain":              {"range": (0.0, 1.0), "curve": "linear"},
            "Dry/Wet Mix":              {"range": (0.0, 1.0), "curve": "linear"},
            "Decay Time":               {"range": (0.0, 1.0), "curve": "linear"},
            "Early / Late Level":       {"range": (0.0, 1.0), "curve": "linear"},
            "VLF Reverb Level":         {"range": (0.0, 1.0), "curve": "linear"},
            "Early Rolloff":            {"range": (0.0, 1.0), "curve": "linear"},
            "Late Rolloff":             {"range": (0.0, 1.0), "curve": "linear"},
            "Pre-delay":                {"range": (0.0, 1.0), "curve": "linear"},
            "Pre-delay Sync":           {"range": (0.0, 1.0), "curve": "linear"},
            "Pre-delay Tempo":          {"range": (0.0, 1.0), "curve": "linear"},
            "Reflection Pattern":       {"range": (0.0, 1.0), "curve": "linear"},
            "Low Decay Mul Freq":       {"range": (0.0, 1.0), "curve": "linear"},
            "High Decay Mul Freq":      {"range": (0.0, 1.0), "curve": "linear"},
            "Low Decay Multiplier":     {"range": (0.0, 1.0), "curve": "linear"},
            "High Decay Multiplier":    {"range": (0.0, 1.0), "curve": "linear"},
            "Low Pass Freq":            {"range": (0.0, 1.0), "curve": "linear"},
            "High Pass Freq":           {"range": (0.0, 1.0), "curve": "linear"},
            "Low Pass Enable":          {"range": (0.0, 1.0), "curve": "linear"},
            "High Pass Enable":         {"range": (0.0, 1.0), "curve": "linear"},
            "Bypass":                   {"range": (0.0, 1.0), "curve": "linear"},
            "Wet":                      {"range": (0.0, 1.0), "curve": "linear"},
            "Delta":                    {"range": (0.0, 1.0), "curve": "linear"},
        },
    },

    # ── Spatial: Delays ────────────────────────────────
    "VST3: EchoBoy (Soundtoys)": {
        "type": "delay",
        "params": {
            "InputGain":      {"range": (0.0, 1.0), "curve": "linear"},
            "OutputGain":     {"range": (0.0, 1.0), "curve": "linear"},
            "Mix":            {"range": (0.0, 1.0), "curve": "linear"},
            "Mode":           {"range": (0.0, 1.0), "curve": "linear"},
            "Echo1Mode":      {"range": (0.0, 1.0), "curve": "linear"},
            "Echo1Note":      {"range": (0.0, 1.0), "curve": "linear"},
            "Echo1Time":      {"range": (0.0, 1.0), "curve": "linear"},
            "Echo2Mode":      {"range": (0.0, 1.0), "curve": "linear"},
            "Echo2Note":      {"range": (0.0, 1.0), "curve": "linear"},
            "Echo2Time":      {"range": (0.0, 1.0), "curve": "linear"},
            "Feedback":       {"range": (0.0, 1.0), "curve": "linear"},
            "PrimeNumbers":   {"range": (0.0, 1.0), "curve": "linear"},
            "Groove":         {"range": (0.0, 1.0), "curve": "linear"},
            "Feel":           {"range": (0.0, 1.0), "curve": "linear"},
            "Saturation":     {"range": (0.0, 1.0), "curve": "linear"},
            "LowCut":         {"range": (0.0, 1.0), "curve": "linear"},
            "HighCut":        {"range": (0.0, 1.0), "curve": "linear"},
            "RhythmMode":     {"range": (0.0, 1.0), "curve": "linear"},
            "RhythmNote":     {"range": (0.0, 1.0), "curve": "linear"},
            "RhythmTime":     {"range": (0.0, 1.0), "curve": "linear"},
            "RhythmRepeats":  {"range": (0.0, 1.0), "curve": "linear"},
            "RhythmDecay":    {"range": (0.0, 1.0), "curve": "linear"},
            "Style":          {"range": (0.0, 1.0), "curve": "linear"},
            "Tempo":          {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
    "VST3: ValhallaDelay (Valhalla DSP, LLC)": {
        "type": "delay",
        "params": {
            "Mix":          {"range": (0.0, 1.0), "curve": "linear"},
            "DelayStyle":   {"range": (0.0, 1.0), "curve": "linear"},
            "DelayL_Ms":    {"range": (0.0, 1.0), "curve": "linear"},
            "Feedback":     {"range": (0.0, 1.0), "curve": "linear"},
            "Width":        {"range": (0.0, 1.0), "curve": "linear"},
            "DriveIn":      {"range": (0.0, 1.0), "curve": "linear"},
            "Age":          {"range": (0.0, 1.0), "curve": "linear"},
            "Diffusion":    {"range": (0.0, 1.0), "curve": "linear"},
            "LowCut":       {"range": (0.0, 1.0), "curve": "linear"},
            "HighCut":      {"range": (0.0, 1.0), "curve": "linear"},
            "ModRate":      {"range": (0.0, 1.0), "curve": "linear"},
            "ModDepth":     {"range": (0.0, 1.0), "curve": "linear"},
            "Ducking":      {"range": (0.0, 1.0), "curve": "linear"},
            "Era":          {"range": (0.0, 1.0), "curve": "linear"},
        },
    },
}


# ════════════════════════════════════════════════════════════════
# Public API
# ════════════════════════════════════════════════════════════════


def normalize_param(plugin_name: str, param_name: str,
                    physical_value: float) -> float:
    """Convert a physical parameter value to 0.0–1.0 for REAPER.

    Looks up *plugin_name* in :data:`PLUGIN_REGISTRY`, then selects
    either linear or table-based normalisation based on the parameter's
    ``"curve"`` entry.

    Raises :exc:`UnregisteredPluginError` when *plugin_name* is not
    in the registry and :exc:`UnregisteredParamError` when *param_name*
    is not known for the plugin.
    """
    plugin = PLUGIN_REGISTRY.get(plugin_name)
    if plugin is None:
        raise UnregisteredPluginError(
            f"'{plugin_name}' is not in PLUGIN_REGISTRY. "
            f"Add it with parameter ranges before using this plugin."
        )

    params = plugin.get("params", {})
    spec = params.get(param_name)
    if spec is None:
        raise UnregisteredParamError(
            f"'{param_name}' is not registered for '{plugin_name}'. "
            f"Registered params: {sorted(params.keys())}"
        )

    if "table" in spec:
        return _normalize_from_table(physical_value, spec["table"])

    # Default: linear
    lo, hi = spec.get("range", (0.0, 1.0))
    return _normalize_linear(physical_value, lo, hi)


def normalize_params(plugin_name: str,
                     physical_params: dict[str, float]) -> dict[str, float]:
    """Batch-normalise — return ``{param_name: 0.0–1.0, …}``.

    Each key in *physical_params* is normalised via :func:`normalize_param`.
    Unknown plugins or parameter names raise exceptions (fail-fast).
    """
    return {
        name: normalize_param(plugin_name, name, value)
        for name, value in physical_params.items()
    }
