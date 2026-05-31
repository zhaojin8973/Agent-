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

    # ── Waves CLA-76 (FET, verified sweep 2026-05-31) ─────
    # All ranges verified via reapy readback against CLA-76 Mono GUI.
    # Attack/Release accept knob positions 1-7 (CW=fast).
    "VST3: CLA-76 Mono (Waves)": {
        "type": "fet",
        "params": {
            "Input":    {"range": (-48.0, 0.0), "curve": "linear"},
            "Output":   {"range": (-48.0, 0.0), "curve": "linear"},
            "Attack":   {"range": (1.0, 7.0),  "curve": "linear"},
            "Release":  {"range": (1.0, 7.0),  "curve": "linear"},
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

    # ── Reverbs ────────────────────────────────────────
    "ValhallaVintageVerb (Valhalla DSP)": {
        "type": "reverb",
        "params": {
            "Mix":   {"range": (0.0, 100.0), "curve": "linear"},
            "Decay": {"range": (0.05, 70.0), "curve": "linear"},
        },
    },
    "ReaVerbate (Cockos)": {
        "type": "reverb",
        "params": {},
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
