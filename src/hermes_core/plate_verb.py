"""
Plate Reverb (LX480 v4 / ValhallaPlate) — BPM 驱动的板式混响参数推导。

三路混响 BPM 联动：
  Room  — 目标 0.5-1.0s, 1/256 PDL
  Plate — 目标 1.0-2.0s, 1/128 PDL
  Hall  — 目标 2.0-4.0s, 1/64 PDL

时值公式：
  rtm_s  ≈ 目标秒数（BPM 自适应拍数：beat = round(target_s × BPM / 60)）
  pdl_ms = 60,000 / bpm / 音符细分
  无BPM → 默认120

校准：2026-06-11 REAPER TrackFX_GetFormattedParamValue 自动扫描
"""

import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# RTM 校准曲线 — seconds → norm（分段线性插值）
# ════════════════════════════════════════════════════════════════

_RTM_CAL: list[tuple[float, float]] = [
    (0.12, 0.000), (0.50, 0.450), (1.00, 0.545),
    (1.60, 0.687), (1.80, 0.718), (1.90, 0.731),
    (2.00, 0.744), (2.50, 0.791), (2.80, 0.813),
    (3.50, 0.876), (4.30, 0.900), (5.30, 0.922),
    (32.6, 1.000),
]


def _rtm_s_to_norm(s: float) -> float:
    if s <= _RTM_CAL[0][0]: return _RTM_CAL[0][1]
    for i in range(len(_RTM_CAL) - 1):
        s0, n0 = _RTM_CAL[i]; s1, n1 = _RTM_CAL[i + 1]
        if s0 <= s <= s1:
            return round(n0 + (s - s0) / (s1 - s0) * (n1 - n0), 4)
    return _RTM_CAL[-1][1]


# PDL 校准曲线 — ms → norm
_PDL_CAL: list[tuple[float, float]] = [
    (10, 0.027), (15, 0.040), (25, 0.065),
    (30, 0.075), (35, 0.086), (40, 0.098),
    (60, 0.140), (100, 0.213), (120, 0.250),
]


def _pdl_ms_to_norm(ms: float) -> float:
    if ms <= _PDL_CAL[0][0]: return _PDL_CAL[0][1]
    for i in range(len(_PDL_CAL) - 1):
        m0, n0 = _PDL_CAL[i]; m1, n1 = _PDL_CAL[i + 1]
        if m0 <= ms <= m1:
            return round(n0 + (ms - m0) / (m1 - m0) * (n1 - n0), 4)
    return _PDL_CAL[-1][1]


# ════════════════════════════════════════════════════════════════
# RTM — BPM 锚点平滑插值
# 思路：慢歌混响长、快歌混响短，锚点之间线性过渡
# ════════════════════════════════════════════════════════════════

_RTM_ANCHORS: list[tuple[float, float]] = [
    (40,  2.5),   # 极慢速 → 大气绵长
    (80,  2.0),   # 慢速 → 自然衰减
    (120, 1.5),   # 中速 → 标准板式
    (160, 1.0),   # 快速 → 紧凑
    (200, 0.8),   # 极快速 → 短促
]


from hermes_core.genre_tables import _bpm_to_rtm_s  # noqa: F811 (replaces local def)


_DEFAULT_BPM = 120.0

# 流派 RTM 倍率：在 BPM 锚点曲线基础上按风格微调
_GENRE_RTM_MULT: dict[str, float] = {
    "folk":                    0.85,
    "ballad":                  1.00,
    "chinese_folk_bel_canto":  1.00,
    "pop":                     0.90,
    "rock":                    0.80,
    "rap":                     0.70,
    "electronic":              0.95,
}
_DEFAULT_RTM_MULT = 1.0

# PDL: 网页计算器 "Small Room(1/2 Note)" = 1/128 音符
# 公式: 60,000 / bpm / 32（32 = quarter→1/128 divider）
_PDL_SUB = 32                  # 1/128 note
_PDL_NOTE = "1/128"

# ════════════════════════════════════════════════════════════════
# 非时值参数 — 自动扫描校准
# ════════════════════════════════════════════════════════════════

_GENRE_SIZ: dict[str, float] = {
    "folk": 0.3400, "ballad": 0.6950, "chinese_folk_bel_canto": 0.5750,
    "pop": 0.5150, "rock": 0.4000, "rap": 0.3100, "electronic": 0.7500,
}
_GENRE_WID: dict[str, float] = {
    "folk": 0.4716, "ballad": 0.4370, "chinese_folk_bel_canto": 0.4500,
    "pop": 0.4370, "rock": 0.4370, "rap": 0.4500, "electronic": 0.3750,
}
_GENRE_HFC: dict[str, float] = {
    "folk": 0.7124, "ballad": 0.7124, "chinese_folk_bel_canto": 0.7600,
    "pop": 0.7600, "rock": 0.7600, "rap": 0.7800, "electronic": 0.8000,
}
_GENRE_LFC: dict[str, float] = {
    "folk": 0.0764, "ballad": 0.0500, "chinese_folk_bel_canto": 0.1000,
    "pop": 0.0764, "rock": 0.1000, "rap": 0.1210, "electronic": 0.1404,
}
_GENRE_DIF: dict[str, float] = {
    "folk": 0.3450, "ballad": 0.4450, "chinese_folk_bel_canto": 0.3950,
    "pop": 0.3950, "rock": 0.3450, "rap": 0.3000, "electronic": 0.5450,
}
_GENRE_SHAPE: dict[str, float] = {
    "folk": 0.0300, "ballad": 0.0608, "chinese_folk_bel_canto": 0.0452,
    "pop": 0.0400, "rock": 0.0300, "rap": 0.0150, "electronic": 0.0950,
}
_GENRE_BAS: dict[str, float] = {
    "folk": 0.5700, "ballad": 0.5970, "chinese_folk_bel_canto": 0.5700,
    "pop": 0.5400, "rock": 0.5400, "rap": 0.5054, "electronic": 0.4700,
}

_DEFAULTS: dict[str, float] = {
    "SIZ": 0.5150, "WID": 0.4370, "HFC": 0.7600, "LFC": 0.0764,
    "DIF": 0.3950, "SHAPE": 0.0400, "BAS": 0.5400,
}

_ALG_PLATE, _DCO_RVB7, _MIX_WET, _SPREAD_DEF = 0.5, 7/19, 1.0, 0.30


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    return {k: max(0.0, min(1.0, v)) for k, v in physical.items()}


def build_params(ctx, *, bpm: float | None = None) -> dict:
    genre = getattr(ctx, "genre", "pop") or "pop"
    eff_bpm = bpm if (bpm and bpm > 0) else _DEFAULT_BPM

    # ── RTM: BPM 锚点插值 × 流派倍率 → 秒 → norm ──
    base_s = _bpm_to_rtm_s(eff_bpm, _RTM_ANCHORS)
    mult = _GENRE_RTM_MULT.get(genre, _DEFAULT_RTM_MULT)
    rtm_s = round(base_s * mult, 2)
    rtm = _rtm_s_to_norm(rtm_s)

    # ── PDL: 1/128 音符（网页 Small Room）──
    pdl_ms = round(60_000.0 / eff_bpm / _PDL_SUB, 1)
    pdl = _pdl_ms_to_norm(pdl_ms)

    # ── 非时值参数 ──
    siz   = _GENRE_SIZ.get(genre, _DEFAULTS["SIZ"])
    wid   = _GENRE_WID.get(genre, _DEFAULTS["WID"])
    hfc   = _GENRE_HFC.get(genre, _DEFAULTS["HFC"])
    lfc   = _GENRE_LFC.get(genre, _DEFAULTS["LFC"])
    dif   = _GENRE_DIF.get(genre, _DEFAULTS["DIF"])
    shape = _GENRE_SHAPE.get(genre, _DEFAULTS["SHAPE"])
    bas   = _GENRE_BAS.get(genre, _DEFAULTS["BAS"])

    log.info(
        f"Auto-Plate: RTM=%.4f(%.1fs) PDL=%.4f(%.0fms/{_PDL_NOTE}) "
        f"SIZ=%.4f WID=%.4f SHAPE=%.4f HFC=%.4f LFC=%.4f "
        f"DIF=%.4f BAS=%.4f (genre=%s bpm=%s)",
        rtm, rtm_s, pdl, pdl_ms,
        siz, wid, shape, hfc, lfc, dif, bas, genre,
        f"{eff_bpm:.0f}" if bpm else "N/A",
    )

    return {
        "E1: Algorithm":                    _ALG_PLATE,
        "E1: Size (SIZ)":                  siz,
        "E1: Reverb Time Mid (RTM)":       rtm,
        "E1: Shape (SHP)":                 shape,
        "E1: Spread (SPR)":                _SPREAD_DEF,
        "E1: Pre Delay (PDL)":             pdl,
        "E1: Width (WID)":                 wid,
        "E1: High Frequency Cutoff (HFC)": hfc,
        "E1: Low Frequency Cutoff (LFC)":  lfc,
        "E1: Diffusion (DIF)":             dif,
        "E1: Bass Multiply (BAS)":         bas,
        "E1: Decay Optimization (DCO)":    _DCO_RVB7,
        "E1: Mix (MIX)":                   _MIX_WET,
    }
