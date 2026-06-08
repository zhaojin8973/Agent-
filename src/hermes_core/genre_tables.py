"""
流派查找表与常量 — 从 engine.py 提取的模块级数据。

包含所有流派相关的参数映射、校准表、EQ频段表等。
这些是纯数据，不包含任何业务逻辑。
"""

from __future__ import annotations

import math

from hermes_core.plugin_registry import (
    SPATIAL_PLUGIN_MAP,
    SPATIAL_BUS_NAMES,
    _REVERB_BUS_TYPES,
    _DELAY_BUS_TYPES,
)

# 向后兼容别名 — 外部模块（engine.py 等）通过 genre_tables 引用这些常量
_SPATIAL_PLUGIN = SPATIAL_PLUGIN_MAP
_SPATIAL_BUS_NAMES = SPATIAL_BUS_NAMES

# ════════════════════════════════════════════════════════════════
# 人声/伴奏平衡
# ════════════════════════════════════════════════════════════════

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

# ════════════════════════════════════════════════════════════════
# 响度目标
# ════════════════════════════════════════════════════════════════

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

# Pro-L 2 calibrated VST parameter ranges (verified 2026-05-28 via REAPER GUI).
# Gain: normalized 0.0 = 0 dB, 1.0 = +30 dB (boost only).
# Output Level: normalized 0.0 = -30 dB, 1.0 = 0 dB.
# Both share a 30 dB span.
_PRO_L2_RANGE_DB: float = 30.0

# ════════════════════════════════════════════════════════════════
# 压缩推导
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

# ════════════════════════════════════════════════════════════════
# 空间效果器发送量基准
# ════════════════════════════════════════════════════════════════

# Reverb send level base per genre (dB, post-fader send).
# Values are confirmed mid-range from professional mixing practice.
# Western folk: intimate, dry.  Chinese folk bel canto: bright,
# grand, long — wetter than Western genres but not muddy.
_GENRE_REVERB_SEND_BASE: dict[str, dict[str, float]] = {
    # 民谣 (Western folk): 自然、亲近、干声为主
    "folk":                    {"plate": -18.0, "hall": -20.0, "room": -14.0},
    "ballad":                  {"plate": -14.0, "hall": -16.0, "room": -12.0},
    "pop":                     {"plate": -12.0, "hall": -14.0, "room": -16.0},
    "rock":                    {"plate": -16.0, "hall": -14.0, "room": -14.0},
    "electronic":              {"plate": -10.0, "hall": -10.0, "room": -18.0},
    # 中国民歌/民族美声：透亮水灵 + 大气绵长，混响偏大但不浑
    "chinese_folk_bel_canto":  {"plate": -10.0, "hall": -10.0, "room": -14.0},
}

# Delay send level base per genre (dB).  -99.0 = disabled for this genre.
_GENRE_DELAY_SEND_BASE: dict[str, dict[str, float]] = {
    "folk":                    {"slap": -99.0, "throw": -99.0, "pingpong": -99.0},
    "ballad":                  {"slap": -21.0, "throw": -24.0, "pingpong": -99.0},
    "pop":                     {"slap": -15.0, "throw": -18.0, "pingpong": -20.0},
    "rock":                    {"slap": -15.0, "throw": -18.0, "pingpong": -18.0},
    "electronic":              {"slap": -11.0, "throw": -14.0, "pingpong": -16.0},
    "chinese_folk_bel_canto":  {"slap": -15.0, "throw": -18.0, "pingpong": -20.0},
}

# MicroShift AUX send level base per genre (dB, verse 基准, chorus +3dB)
# 设计文档 §3.1
_GENRE_MICROSHIFT_SEND: dict[str, float] = {
    "folk":                    -16.0,
    "ballad":                  -14.0,
    "pop":                     -12.0,
    "rock":                    -12.0,
    "electronic":              -10.0,
    "chinese_folk_bel_canto":  -14.0,
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

# ════════════════════════════════════════════════════════════════
# 返回轨 EQ 参数（按流派 × 总线）
# ════════════════════════════════════════════════════════════════

# HPF removes low-end mud from reverb/delay returns.
# LPF tames sibilance / harshness in the tail.
# Delay returns are filtered more aggressively (narrower band).
_GENRE_RETURN_EQ: dict[str, dict[str, dict[str, float]]] = {
    "folk": {
        "plate": {"hpf": 200, "lpf": 10000},
        "hall":  {"hpf": 400, "lpf": 8000},
        "room":  {"hpf": 150, "lpf": 12000},
        "delay": {"hpf": 500, "lpf": 5000},
    },
    "ballad": {
        "plate": {"hpf": 180, "lpf": 10000},
        "hall":  {"hpf": 350, "lpf": 8000},
        "room":  {"hpf": 120, "lpf": 12000},
        "delay": {"hpf": 400, "lpf": 6000},
    },
    "pop": {
        "plate": {"hpf": 200, "lpf": 10000},
        "hall":  {"hpf": 400, "lpf": 8000},
        "room":  {"hpf": 180, "lpf": 12000},
        "delay": {"hpf": 500, "lpf": 6000},
    },
    "rock": {
        "plate": {"hpf": 250, "lpf": 9000},
        "hall":  {"hpf": 450, "lpf": 7000},
        "room":  {"hpf": 200, "lpf": 11000},
        "delay": {"hpf": 600, "lpf": 5000},
    },
    "electronic": {
        "plate": {"hpf": 300, "lpf": 8000},
        "hall":  {"hpf": 500, "lpf": 6000},
        "room":  {"hpf": 250, "lpf": 10000},
        "delay": {"hpf": 600, "lpf": 4000},
    },
    "chinese_folk_bel_canto": {
        "plate": {"hpf": 180, "lpf": 10000},
        "hall":  {"hpf": 350, "lpf": 8000},
        "room":  {"hpf": 150, "lpf": 12000},
        "delay": {"hpf": 400, "lpf": 6000},
    },
}

# ════════════════════════════════════════════════════════════════
# 流派空间插件参数表
# ════════════════════════════════════════════════════════════════

# 每个 bus 使用其首选插件的参数名和归一化值 (0.0–1.0)。
# 如果回退插件被加载，通过 _SPATIAL_PARAM_FALLBACK_MAP 做名称转换。
# 设计值来源: docs/spatial-effects-design.md §1。
# 验证: 用 REAPER 运行 tools/discover_spatial_params.py 确认参数名。

_GENRE_SPATIAL_PARAMS: dict[str, dict[str, dict[str, float]]] = {
    # ── 民谣 (Western folk) ──
    "folk": {
        # plate → Little Plate: Decay 1.8s, Pre-Delay N/A (无此参数), HPF 200Hz
        "plate": {"Decay": 0.28, "Low Cut": 0.15, "Mix": 1.0},
        # hall → LX480: Decay 1.5s, Pre-Delay 60ms
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.25,
            "E1: Pre Delay (PDL)": 0.30,
            "E1: Mix (MIX)": 1.0,
        },
        # room → ValhallaRoom: Decay 0.8s, Pre-Delay 10ms
        "room":  {"decay": 0.18, "predelay": 0.05, "mix": 1.0},
    },

    # ── 情歌 (Ballad) ──
    "ballad": {
        "plate": {"Decay": 0.35, "Low Cut": 0.12, "Mix": 1.0},
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.35,
            "E1: Pre Delay (PDL)": 0.40,
            "E1: Mix (MIX)": 1.0,
        },
        "room":  {"decay": 0.22, "predelay": 0.10, "mix": 1.0},
        # slap → EchoBoy: 100ms, Feedback 10%
        "slap":  {
            "Echo1Time": 0.05, "Feedback": 0.10, "Mix": 1.0,
            "Saturation": 0.15,
        },
    },

    # ── 流行 (Pop) ──
    "pop": {
        # plate → Little Plate: Decay 2.0s, Pre-Delay N/A (回退到ValhallaPlate设PreDelay)
        "plate": {"Decay": 0.32, "Low Cut": 0.12, "Mix": 1.0, "Mod Enable": 0.0},
        # hall → LX480: Decay 2.2s, Pre-Delay 40ms
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.32,
            "E1: Pre Delay (PDL)": 0.25,
            "E1: Mix (MIX)": 1.0,
            "E1: Size (SIZ)": 0.45,
        },
        # room → ValhallaRoom: Decay 0.6s, Pre-Delay 10ms
        "room":  {"decay": 0.14, "predelay": 0.05, "mix": 1.0, "lateSize": 0.35},
        # slap → EchoBoy: 100ms, Feedback 10%
        "slap":  {
            "Echo1Time": 0.05, "Feedback": 0.10, "Mix": 1.0,
            "Saturation": 0.15, "LowCut": 0.15,
        },
        # rhythm → EchoBoy: 1/4 Note, Feedback 20%（音符值在 _apply_spatial_params 中处理）
        "rhythm": {
            "RhythmNote": 0.30, "Feedback": 0.20, "Mix": 1.0,
            "Saturation": 0.12, "LowCut": 0.15,
        },
    },

    # ── 摇滚 (Rock) ──
    "rock": {
        "plate": {"Decay": 0.25, "Low Cut": 0.15, "Mix": 1.0, "Mod Enable": 0.0},
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.28,
            "E1: Pre Delay (PDL)": 0.28,
            "E1: Mix (MIX)": 1.0,
            "E1: Size (SIZ)": 0.40,
        },
        "room":  {"decay": 0.14, "predelay": 0.05, "mix": 1.0},
        "slap":  {
            "Echo1Time": 0.04, "Feedback": 0.15, "Mix": 1.0,
            "Saturation": 0.20, "LowCut": 0.18,
        },
        "rhythm": {
            "RhythmNote": 0.20, "Feedback": 0.20, "Mix": 1.0,
            "Saturation": 0.15, "LowCut": 0.18,
        },
    },

    # ── 电子 (Electronic) ──
    "electronic": {
        "plate": {"Decay": 0.38, "Low Cut": 0.22, "Mix": 1.0},
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.45,
            "E1: Pre Delay (PDL)": 0.20,
            "E1: Mix (MIX)": 1.0,
        },
        "room":  {"decay": 0.10, "predelay": 0.03, "mix": 1.0},
        "slap":  {
            "Echo1Time": 0.06, "Feedback": 0.25, "Mix": 1.0,
            "Saturation": 0.10, "LowCut": 0.08,
        },
        "rhythm": {
            "RhythmNote": 0.25, "Feedback": 0.30, "Mix": 1.0,
        },
    },

    # ── 中国民歌/民族美声 ──
    "chinese_folk_bel_canto": {
        "plate": {"Decay": 0.32, "Low Cut": 0.10, "Mix": 1.0, "Mod Enable": 0.0},
        "hall":  {
            "E1: Reverb Time Mid (RTM)": 0.38,
            "E1: Pre Delay (PDL)": 0.22,
            "E1: Mix (MIX)": 1.0,
            "E1: Size (SIZ)": 0.50,
        },
        "room":  {"decay": 0.20, "predelay": 0.05, "mix": 1.0},
        "slap":  {
            "Echo1Time": 0.05, "Feedback": 0.10, "Mix": 1.0,
            "Saturation": 0.10, "LowCut": 0.12,
        },
        "rhythm": {
            "RhythmNote": 0.30, "Feedback": 0.20, "Mix": 1.0,
        },
    },
}

# ── 空间插件参数名回退映射 ──────────────────────────────────────
# 当回退插件被加载时，将首选插件的参数名映射到回退插件的对应参数名。
# 键: (首选插件注册名, 首选参数名) → 回退参数名
# 值通过 _resolve_spatial_plugin_key 匹配后查找。

_SPATIAL_PARAM_FALLBACK_MAP: dict[str, dict[str, str]] = {
    # Little Plate → ValhallaPlate 回退 (plate bus)
    "ValhallaPlate": {
        "Decay":      "Decay",
        "Low Cut":    "LowEQFreq",
        "Mix":        "Mix",
        "Mod Enable": "ModDepth",
    },
    # LX480 → ValhallaVintageVerb 回退 (hall bus)
    "ValhallaVintageVerb": {
        "E1: Reverb Time Mid (RTM)":  "Decay",
        "E1: Pre Delay (PDL)":        "PreDelay",
        "E1: Mix (MIX)":              "Mix",
        "E1: Size (SIZ)":             "Size",
        "E1: High Frequency Cutoff (HFC)": "HighCut",
        "E1: Low Frequency Cutoff (LFC)":  "LowCut",
    },
    # ValhallaRoom → Pro-R 2 回退 (room bus)
    "Pro-R": {
        "decay":    "Decay Rate",
        "predelay": "Predelay",
        "mix":      "Mix",
        "lateSize": "Distance",
        "HiCut":    "Brightness",
        "LoCut":    "Brightness",
    },
    # EchoBoy → ValhallaDelay 回退 (slap/rhythm bus)
    "ValhallaDelay": {
        "Echo1Time":   "DelayL_Ms",
        "Feedback":    "Feedback",
        "Mix":         "Mix",
        "Saturation":  "DriveIn",
        "LowCut":      "LowCut",
        "HighCut":     "HighCut",
        "RhythmNote":  "DelayL_Ms",
    },
}

# ════════════════════════════════════════════════════════════════
# EQ 推导
# ════════════════════════════════════════════════════════════════

# Genre-specific tweaks to EQ derivation thresholds
_GENRE_EQ_TWEAKS: dict[str, dict] = {
    "pop":  {"presence_extra_db": 0.5, "mud_threshold_db": 3.0, "boost_scale": 1.0},
    "rock": {"presence_extra_db": 0.0, "mud_threshold_db": 4.0, "boost_scale": 1.0},
    "folk": {"presence_extra_db": 0.0, "mud_threshold_db": 3.0, "boost_scale": 0.75},
    "default": {"presence_extra_db": 0.0, "mud_threshold_db": 3.0, "boost_scale": 1.0},
}

# Minimum Q factor for EQ resonance cuts (mirrors spectrum._MIN_Q_FACTOR)
_MIN_EQ_Q = 15.0

# ════════════════════════════════════════════════════════════════
# Pro-Q 3 参数
# ════════════════════════════════════════════════════════════════

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

# ════════════════════════════════════════════════════════════════
# SSL EQ 转换
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

# ════════════════════════════════════════════════════════════════
# 9 段人声链 — 新插件流派参数
# ════════════════════════════════════════════════════════════════

# UAD 1176 Ratio — 流派差异化
_GENRE_1176_RATIO: dict[str, int] = {
    "folk":                    4,
    "ballad":                  4,
    "pop":                     8,
    "rock":                    8,
    "electronic":              12,
    "chinese_folk_bel_canto":  4,
}

# Oxford Inflator Effect — 流派差异化（人声保守）
_GENRE_INFLATOR_EFFECT: dict[str, float] = {
    "folk":                    0.20,
    "ballad":                  0.25,
    "pop":                     0.30,
    "rock":                    0.35,
    "electronic":              0.40,
    "chinese_folk_bel_canto":  0.25,
}

# UAD CL 1B Ratio — 流派差异化
_GENRE_CL1B_RATIO: dict[str, float] = {
    "folk":                    2.0,
    "ballad":                  2.0,
    "pop":                     3.0,
    "rock":                    3.0,
    "electronic":              4.0,
    "chinese_folk_bel_canto":  2.0,
}

# Maag EQ4 Air Band 频率 — 流派差异化
_GENRE_MAAG_AIR_FREQ: dict[str, float] = {
    "folk":                    10000.0,
    "ballad":                  10000.0,
    "pop":                     20000.0,
    "rock":                    20000.0,
    "electronic":              20000.0,
    "chinese_folk_bel_canto":  10000.0,
}

# bx_2098 Sheen — 流派差异化
_GENRE_BX2098_SHEEN: dict[str, bool] = {
    "folk":                    False,
    "ballad":                  False,
    "pop":                     True,
    "rock":                    True,
    "electronic":              True,
    "chinese_folk_bel_canto":  False,
}

# Pro-L 2 Style — 流派差异化
_GENRE_PROL2_STYLE: dict[str, str] = {
    "folk":                    "Transparent",
    "ballad":                  "Transparent",
    "pop":                     "Allround",
    "rock":                    "Allround",
    "electronic":              "Aggressive",
    "chinese_folk_bel_canto":  "Transparent",
}

# ════════════════════════════════════════════════════════════════
# 混响器流派配对表
# ════════════════════════════════════════════════════════════════

# 流派 → {bus → 首选插件名子串}
_GENRE_REVERB_PREFERENCE: dict[str, dict[str, str]] = {
    "folk": {
        "room":  "Seventh Heaven",
        "plate": "Relab LX480",
        "hall":  "Cinematic Rooms",
    },
    "ballad": {
        "room":  "Seventh Heaven",
        "plate": "Relab LX480",
        "hall":  "Cinematic Rooms",
    },
    "pop": {
        "room":  "UAD EMT 140",
        "plate": "Relab LX480",
        "hall":  "Seventh Heaven",
    },
    "rock": {
        "room":  "UAD EMT 140",
        "plate": "Tai Chi",
        "hall":  "Relab LX480",
    },
    "electronic": {
        "room":  "Relab LX480",
        "plate": "Supernova",
        "hall":  "Blackhole",
    },
    "chinese_folk_bel_canto": {
        "room":  "Seventh Heaven",
        "plate": "Relab LX480",
        "hall":  "Cinematic Rooms",
    },
}

# Electronic 流派专属 Blackhole 设置
_BLACKHOLE_GENRES: frozenset = frozenset({"electronic"})


# ════════════════════════════════════════════════════════════════
# 人声特征画像 — 业界混音标准数据
# ════════════════════════════════════════════════════════════════

from dataclasses import dataclass


@dataclass(frozen=True)
class VocalProfile:
    """人声特征画像 — 不可变，所有参数可追溯到业界标准。

    来源:
        - Unison Audio Vocal EQ Chart (2024)
        - ProducerHive Vocal Frequency Ranges
        - iZotope Vocal EQ Guide
        - MusicGuyMixing Male/Female EQ Guides
        - bchillmix Vocal EQ Cheatsheet
        - 343labs Vocal EQ Guide
    """

    # ── 元数据 ──
    gender: str = "female"
    technique: str = "pop"

    # ── 基频 (Hz) ──
    # 来源: ProducerHive — 典型成年男声 85-180, 女声 165-255
    fundamental_lo: float = 165.0
    fundamental_hi: float = 255.0

    # ── 低切 HPF (Hz) ──
    # 来源: bchillmix — 男声 60-80, 女声 100-120; iZotope — <100Hz 以下为低频隆隆声
    hpf_default_hz: float = 120.0
    hpf_min_hz: float = 100.0
    hpf_max_hz: float = 180.0

    # ── 泥巴/浑浊区 (Hz) ──
    # 来源: iZotope — 200-500Hz; Unison — 男声 200-400, 女声 300-600
    mud_scan_lo: float = 300.0
    mud_scan_hi: float = 600.0
    mud_threshold_db: float = 3.0

    # ── 盒子声/鼻音区 (Hz) ──
    # 来源: MusicGuyMixing — 500Hz 盒子声, 800-1.5k 鼻音(男), 1k-2k 鼻音(女)
    boxy_lo: float = 400.0
    boxy_hi: float = 900.0
    nasal_lo: float = 1000.0
    nasal_hi: float = 2000.0

    # ── 存在感 (Hz) ──
    # 来源: Unison — 男声 3k-5k, 女声 4k-6k; 343labs — 男声 boost 5k, 女声 cut 5k
    presence_scan_lo: float = 4000.0
    presence_scan_hi: float = 6000.0
    presence_boost_max_db: float = 3.0

    # ── 齿音区 (Hz) ──
    # 来源: iZotope — 5k-8k(男), 6k-9k(女); ProducerHive — 5-8kHz
    sibilance_lo: float = 6000.0
    sibilance_hi: float = 9000.0

    # ── 空气感 (Hz) ──
    # 来源: bchillmix — 10k-15k(男), 12k-18k(女)
    air_scan_lo: float = 4000.0
    air_scan_hi: float = 15000.0
    air_boost_max_db: float = 1.5

    # ── 共振检测 ──
    # 来源: 业界共识 — Q>15 为房间模式; 女声泛音更密，阈值略降
    resonance_q_threshold: float = 8.0
    resonance_cut_max_db: float = 6.0

    # ── 增益策略 ──
    # 来源: 混合经验 — 民美动态大需保守, 流行可稍大胆, 摇滚最激进
    boost_scale: float = 1.0

    # ── 泥巴区先提升(thin→warm)的场景 ──
    thin_boost_max_db: float = 2.0


# ════════════════════════════════════════════════════════════════
# VocalProfile 预设表: gender × technique
# ════════════════════════════════════════════════════════════════

_VOCAL_PROFILES: dict[str, dict[str, dict]] = {
    "male": {
        "pop": {
            "fundamental_lo": 85.0,  "fundamental_hi": 180.0,
            "hpf_default_hz": 80.0,  "hpf_min_hz": 60.0,  "hpf_max_hz": 100.0,
            "mud_scan_lo": 200.0,     "mud_scan_hi": 400.0,
            "mud_threshold_db": 3.0,
            "boxy_lo": 300.0,         "boxy_hi": 700.0,
            "nasal_lo": 800.0,        "nasal_hi": 1500.0,
            "presence_scan_lo": 3000.0, "presence_scan_hi": 5000.0,
            "presence_boost_max_db": 3.0,
            "sibilance_lo": 5000.0,   "sibilance_hi": 8000.0,
            "air_scan_lo": 3000.0,    "air_scan_hi": 12000.0,
            "air_boost_max_db": 1.5,
            "resonance_q_threshold": 8.0, "resonance_cut_max_db": 6.0,
            "boost_scale": 1.0,       "thin_boost_max_db": 2.0,
        },
        "rock": {
            "fundamental_lo": 85.0,  "fundamental_hi": 180.0,
            "hpf_default_hz": 120.0, "hpf_min_hz": 100.0, "hpf_max_hz": 150.0,
            "mud_scan_lo": 200.0,     "mud_scan_hi": 400.0,
            "mud_threshold_db": 4.0,
            "boxy_lo": 300.0,         "boxy_hi": 700.0,
            "nasal_lo": 800.0,        "nasal_hi": 1500.0,
            "presence_scan_lo": 3000.0, "presence_scan_hi": 5000.0,
            "presence_boost_max_db": 3.5,
            "sibilance_lo": 5000.0,   "sibilance_hi": 8000.0,
            "air_scan_lo": 3000.0,    "air_scan_hi": 12000.0,
            "air_boost_max_db": 1.5,
            "resonance_q_threshold": 8.0, "resonance_cut_max_db": 6.0,
            "boost_scale": 1.2,       "thin_boost_max_db": 2.0,
        },
        "folk": {
            "fundamental_lo": 85.0,  "fundamental_hi": 180.0,
            "hpf_default_hz": 80.0,  "hpf_min_hz": 60.0,  "hpf_max_hz": 120.0,
            "mud_scan_lo": 200.0,     "mud_scan_hi": 400.0,
            "mud_threshold_db": 3.0,
            "boxy_lo": 300.0,         "boxy_hi": 700.0,
            "nasal_lo": 800.0,        "nasal_hi": 1500.0,
            "presence_scan_lo": 3000.0, "presence_scan_hi": 5000.0,
            "presence_boost_max_db": 2.5,
            "sibilance_lo": 5000.0,   "sibilance_hi": 8000.0,
            "air_scan_lo": 3000.0,    "air_scan_hi": 12000.0,
            "air_boost_max_db": 1.0,
            "resonance_q_threshold": 8.0, "resonance_cut_max_db": 5.0,
            "boost_scale": 0.8,       "thin_boost_max_db": 1.5,
        },
        "bel_canto": {
            "fundamental_lo": 85.0,  "fundamental_hi": 180.0,
            "hpf_default_hz": 60.0,  "hpf_min_hz": 40.0,  "hpf_max_hz": 100.0,
            "mud_scan_lo": 200.0,     "mud_scan_hi": 400.0,
            "mud_threshold_db": 2.5,
            "boxy_lo": 300.0,         "boxy_hi": 700.0,
            "nasal_lo": 800.0,        "nasal_hi": 1500.0,
            "presence_scan_lo": 2500.0, "presence_scan_hi": 4500.0,
            "presence_boost_max_db": 2.0,
            "sibilance_lo": 5000.0,   "sibilance_hi": 8000.0,
            "air_scan_lo": 3000.0,    "air_scan_hi": 12000.0,
            "air_boost_max_db": 1.0,
            "resonance_q_threshold": 6.0, "resonance_cut_max_db": 4.0,
            "boost_scale": 0.6,       "thin_boost_max_db": 1.5,
        },
    },
    "female": {
        "pop": {
            "fundamental_lo": 165.0, "fundamental_hi": 255.0,
            "hpf_default_hz": 120.0, "hpf_min_hz": 100.0, "hpf_max_hz": 150.0,
            "mud_scan_lo": 300.0,     "mud_scan_hi": 500.0,
            "mud_threshold_db": 3.0,
            "boxy_lo": 400.0,         "boxy_hi": 900.0,
            "nasal_lo": 1000.0,       "nasal_hi": 2000.0,
            "presence_scan_lo": 4000.0, "presence_scan_hi": 6000.0,
            "presence_boost_max_db": 3.0,
            "sibilance_lo": 6000.0,   "sibilance_hi": 9000.0,
            "air_scan_lo": 4000.0,    "air_scan_hi": 15000.0,
            "air_boost_max_db": 1.5,
            "resonance_q_threshold": 7.0, "resonance_cut_max_db": 5.0,
            "boost_scale": 1.0,       "thin_boost_max_db": 2.0,
        },
        "rock": {
            "fundamental_lo": 165.0, "fundamental_hi": 255.0,
            "hpf_default_hz": 130.0, "hpf_min_hz": 120.0, "hpf_max_hz": 180.0,
            "mud_scan_lo": 300.0,     "mud_scan_hi": 500.0,
            "mud_threshold_db": 4.0,
            "boxy_lo": 400.0,         "boxy_hi": 900.0,
            "nasal_lo": 1000.0,       "nasal_hi": 2000.0,
            "presence_scan_lo": 4000.0, "presence_scan_hi": 6000.0,
            "presence_boost_max_db": 3.5,
            "sibilance_lo": 6000.0,   "sibilance_hi": 9000.0,
            "air_scan_lo": 4000.0,    "air_scan_hi": 15000.0,
            "air_boost_max_db": 1.5,
            "resonance_q_threshold": 7.0, "resonance_cut_max_db": 5.0,
            "boost_scale": 1.2,       "thin_boost_max_db": 2.0,
        },
        "folk": {
            "fundamental_lo": 165.0, "fundamental_hi": 255.0,
            "hpf_default_hz": 120.0, "hpf_min_hz": 100.0, "hpf_max_hz": 150.0,
            "mud_scan_lo": 300.0,     "mud_scan_hi": 500.0,
            "mud_threshold_db": 3.0,
            "boxy_lo": 400.0,         "boxy_hi": 900.0,
            "nasal_lo": 1000.0,       "nasal_hi": 2000.0,
            "presence_scan_lo": 4000.0, "presence_scan_hi": 6000.0,
            "presence_boost_max_db": 2.5,
            "sibilance_lo": 6000.0,   "sibilance_hi": 9000.0,
            "air_scan_lo": 4000.0,    "air_scan_hi": 15000.0,
            "air_boost_max_db": 1.0,
            "resonance_q_threshold": 7.0, "resonance_cut_max_db": 4.0,
            "boost_scale": 0.8,       "thin_boost_max_db": 1.5,
        },
        "bel_canto": {
            "fundamental_lo": 165.0, "fundamental_hi": 255.0,
            "hpf_default_hz": 100.0, "hpf_min_hz": 80.0,  "hpf_max_hz": 180.0,
            "mud_scan_lo": 300.0,     "mud_scan_hi": 600.0,
            "mud_threshold_db": 2.5,
            "boxy_lo": 400.0,         "boxy_hi": 900.0,
            "nasal_lo": 1000.0,       "nasal_hi": 2000.0,
            "presence_scan_lo": 3500.0, "presence_scan_hi": 5500.0,
            "presence_boost_max_db": 2.0,
            "sibilance_lo": 6000.0,   "sibilance_hi": 9000.0,
            "air_scan_lo": 4000.0,    "air_scan_hi": 15000.0,
            "air_boost_max_db": 1.0,
            "resonance_q_threshold": 6.0, "resonance_cut_max_db": 4.0,
            "boost_scale": 0.6,       "thin_boost_max_db": 1.5,
        },
        # 中国民美：民族唱法的明亮靠前 + 美声的气息支撑
        # → 透亮、有穿透力、不闷（与西方美声的保守策略相反）
        "chinese_folk_bel_canto": {
            "fundamental_lo": 165.0, "fundamental_hi": 255.0,
            "hpf_default_hz": 100.0, "hpf_min_hz": 80.0,  "hpf_max_hz": 150.0,
            "mud_scan_lo": 300.0,     "mud_scan_hi": 600.0,
            "mud_threshold_db": 2.5,
            "boxy_lo": 400.0,         "boxy_hi": 900.0,
            "nasal_lo": 1000.0,       "nasal_hi": 2000.0,
            "presence_scan_lo": 3500.0, "presence_scan_hi": 5500.0,
            "presence_boost_max_db": 3.0,
            "sibilance_lo": 6000.0,   "sibilance_hi": 9000.0,
            "air_scan_lo": 4000.0,    "air_scan_hi": 15000.0,
            "air_boost_max_db": 1.5,
            "resonance_q_threshold": 6.0, "resonance_cut_max_db": 4.0,
            "boost_scale": 1.1,       "thin_boost_max_db": 2.0,
        },
    },
}


def get_vocal_profile(gender: str = "", technique: str = "") -> VocalProfile:
    """根据性别和演唱方式返回 :class:`VocalProfile`。

    用 ``gender × technique`` 查 :data:`_VOCAL_PROFILES`，
    未匹配时回退到默认值（female/pop）。
    """
    gender_key = gender if gender in _VOCAL_PROFILES else "female"
    tech_map = _VOCAL_PROFILES[gender_key]
    tech_key = technique if technique in tech_map else "pop"
    return VocalProfile(gender=gender_key, technique=tech_key, **tech_map[tech_key])

# 混响回退 — 首选不可用时的通用回退
_FALLBACK_REVERB: str = "ValhallaVintageVerb"
