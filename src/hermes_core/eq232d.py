"""
Bettermaker EQ232D 自动化参数模块
===================================
Plugin Alliance Bettermaker EQ232D — 干净固态 Pultec 风格母带级 EQ。

硬件原型：Bettermaker EQ232P MKII
Vocal A 链角色：Pultec 染色 + 低频推拉 + 空气感提升

参数范围（REAPER VST3，所有参数 0.0-1.0 归一化）
---------------------------------------------------
物理映射（校准验证）:
- LO/HI BOOST/ATTEN, HI BW: 线性 0→14
- LO CPS: 0=20Hz, 0.333=30Hz, 0.667=60Hz, 1.0=100Hz
- KCS BST: 0=3k, 0.167=4k, 0.333=5k, 0.5=6k, 0.667=10k, 0.833=12k, 1.0=16k
- KCS ATT: 0=5k, 0.5=10k, 1.0=20k
- LVL OUT: unity ≈0.492, 显示范围 ≈-6.4~+6.6 dB
- CHANNEL: 0=STEREO, 0.5=M/S, 1.0=DUAL MONO
- 开关类: 0=OFF, 1=ON

设计原则（v3 — 两层结构）
-------------------------
纠错层 (hermes_core.vocal_ref):
  实际频谱 vs 男女声参考模板 → 偏差超出容差 → 比例补偿
美化层 (本模块):
  纠错之上叠加流派驱动的额外加成（混音需要"美化"不是只"修"）
"""

from hermes_core.vocal_ref import get_ref, relativize, deviation, is_outside

import logging

log = logging.getLogger(__name__)

# LO CPS 档位
_CPS_60HZ = 0.667
_CPS_100HZ = 1.0

# KCS BST 档位（HI BOOST 频率，7 档）
_KCS_BST_10K = 0.667
_KCS_BST_12K = 0.833
_KCS_BST_16K = 1.000

# KCS ATT 档位（HI ATTEN 频率，3 档）
_KCS_ATT_20K = 1.000


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """EQ232D 物理参数 → 归一化字典 (0-1)。"""
    return {k: max(0.0, min(1.0, v)) for k, v in physical.items()}


# ════════════════════════════════════════════════════════════════
# 流派表 — 基准系数（会被 spectrum 偏差动态修正）
# ════════════════════════════════════════════════════════════════

_LOW_BOOST_BY_GENRE: dict[str, float] = {
    "folk":                    0.179,
    "ballad":                  0.214,
    "chinese_folk_bel_canto":  0.214,
    "pop":                     0.250,
    "rock":                    0.286,
    "rap":                     0.250,
    "electronic":              0.321,
}

_LOW_ATTEN_BY_GENRE: dict[str, float] = {
    "folk":                    0.129,
    "ballad":                  0.150,
    "chinese_folk_bel_canto":  0.150,
    "pop":                     0.179,
    "rock":                    0.200,
    "rap":                     0.200,
    "electronic":              0.214,
}

_HI_BW_BY_GENRE: dict[str, float] = {
    "folk":                    0.72,
    "ballad":                  0.72,
    "chinese_folk_bel_canto":  0.72,
    "pop":                     0.72,
    "rock":                    0.55,
    "rap":                     0.55,
    "electronic":              0.55,
}

_DEFAULT_GENRE = "pop"

# ── 美化层 — 流派驱动的额外加成（叠加在纠错之上）──
_HI_BOOST_ENHANCE: dict[str, float] = {
    "folk": 0.05, "ballad": 0.06, "chinese_folk_bel_canto": 0.08,
    "pop": 0.10, "rock": 0.08, "rap": 0.10, "electronic": 0.12,
}
_LO_BOOST_ENHANCE: dict[str, float] = {
    "folk": 0.03, "ballad": 0.04, "chinese_folk_bel_canto": 0.05,
    "pop": 0.06, "rock": 0.08, "rap": 0.06, "electronic": 0.08,
}
_LO_ATTEN_ENHANCE: dict[str, float] = {
    "folk": 0.02, "ballad": 0.02, "chinese_folk_bel_canto": 0.03,
    "pop": 0.03, "rock": 0.04, "rap": 0.04, "electronic": 0.04,
}


# ════════════════════════════════════════════════════════════════
# 推导公式 — 纠错层 (vocal_ref) + 性别修正
# ════════════════════════════════════════════════════════════════

def lo_cps_val(gender: str = "", genre: str = "pop") -> float:
    """性别 + 流派 → LO CPS。"""
    if gender == "male" and genre in ("rock", "electronic", "folk"):
        return _CPS_60HZ
    return _CPS_100HZ


def low_boost(genre: str = "pop", *,
              gender: str = "",
              spectrum: dict | None = None) -> float:
    """偏差驱动 → LO BOOST（纠错）。"""
    base = _LOW_BOOST_BY_GENRE.get(genre, _LOW_BOOST_BY_GENRE[_DEFAULT_GENRE])

    if spectrum and spectrum.get("band_energy_db"):
        ref = get_ref(gender)
        rel = relativize(spectrum["band_energy_db"])
        low_rel = rel.get("low", -10.0)
        low_center, low_tol = ref["low"]
        dev = deviation(low_rel, low_center)

        if dev < -low_tol:
            factor = min(1.5, max(0.6, 1.0 + abs(dev + low_tol) / 25.0))
            base *= factor
        elif dev > low_tol:
            base *= 0.4
        else:
            base *= 0.75

    if gender == "female":
        base *= 0.7

    return round(max(0.0, min(1.0, base)), 3)


def low_atten(deficit: float, genre: str = "pop", *,
              gender: str = "",
              spectrum: dict | None = None) -> float:
    """偏差驱动 → LO ATTEN（纠错）。"""
    base = _LOW_ATTEN_BY_GENRE.get(genre, _LOW_ATTEN_BY_GENRE[_DEFAULT_GENRE])

    if spectrum and spectrum.get("band_energy_db"):
        ref = get_ref(gender)
        rel = relativize(spectrum["band_energy_db"])
        lm_rel = rel.get("low_mid", -5.0)
        lm_center, lm_tol = ref["low_mid"]
        dev = deviation(lm_rel, lm_center)

        if dev > lm_tol:
            base *= 1.3
        elif dev < -lm_tol:
            base *= 0.5
        else:
            base *= 0.85

    if gender == "female":
        base *= 1.15

    if spectrum is None:
        if deficit > 3.0:
            base *= 1.2
        elif deficit <= 0:
            base *= 0.4

    return round(max(0.0, min(1.0, base)), 3)


def hi_boost(deficit: float, genre: str = "pop", *,
             spectrum: dict | None = None,
             gender: str = "") -> float:
    """偏差驱动 → HI BOOST（纠错）。"""
    band_energy = spectrum.get("band_energy_db", {}) if spectrum else {}
    if band_energy and "mid" in band_energy:
        ref = get_ref(gender)
        rel = relativize(band_energy)
        air_rel = rel.get("air", -32.0)
        air_center, air_tol = ref["air"]
        dev = deviation(air_rel, air_center)

        if dev < -air_tol:
            need_db = min(abs(dev) - air_tol, 20.0)
            return round(max(0.10, min(0.75, need_db * 0.025)), 3)
        elif dev > air_tol:
            return 0.08
        else:
            return 0.12

    _k = {"folk": 0.030, "ballad": 0.032, "chinese_folk_bel_canto": 0.035,
          "pop": 0.040, "rock": 0.045, "rap": 0.060, "electronic": 0.050}
    return round(max(0.0, min(1.0, deficit * _k.get(genre, 0.040))), 3)


def hi_atten(hi_boost_val: float) -> float:
    """Pultec 高频技巧：仅在 boost > 0.30 时启用。"""
    if hi_boost_val <= 0.30:
        return 0.0
    return round(hi_boost_val * 0.12, 3)


def hi_bw(genre: str = "pop") -> float:
    return _HI_BW_BY_GENRE.get(genre, _HI_BW_BY_GENRE[_DEFAULT_GENRE])


def kcs_bst_val(genre: str = "pop", *, gender: str = "") -> float:
    if genre in ("rock", "rap"):
        return _KCS_BST_10K
    if genre == "electronic" or (genre == "pop" and gender == "female"):
        return _KCS_BST_16K
    return _KCS_BST_12K


def kcs_att_val() -> float:
    return _KCS_ATT_20K


# ════════════════════════════════════════════════════════════════
# Builder
# ════════════════════════════════════════════════════════════════

def build_params(ctx, *, gender: str = "", spectrum: dict | None = None) -> dict:
    """从 FXBuildContext + mid-chain 频谱推导 EQ232D 参数。

    两层结构：纠错层 (vocal_ref) + 美化层 (enhance tables)。
    Channel 1 + Channel 2：镜像配置。
    """
    deficit = getattr(ctx, "presence_deficit", 0.0) or 0.0
    genre = (getattr(ctx, "genre", None) or _DEFAULT_GENRE)

    lo_cps       = lo_cps_val(gender=gender, genre=genre)
    lo_boost_val = low_boost(genre, gender=gender, spectrum=spectrum)
    lo_atten_val = low_atten(deficit, genre, gender=gender, spectrum=spectrum)
    hi_boost_val = hi_boost(deficit, genre, spectrum=spectrum, gender=gender)
    hi_bw_val    = hi_bw(genre)
    kcs_bst      = kcs_bst_val(genre, gender=gender)
    kcs_att      = kcs_att_val()

    # ── 美化层 ──
    hi_boost_val = round(hi_boost_val + _HI_BOOST_ENHANCE.get(genre, 0.08), 3)
    lo_boost_val = round(lo_boost_val + _LO_BOOST_ENHANCE.get(genre, 0.05), 3)
    lo_atten_val = round(lo_atten_val + _LO_ATTEN_ENHANCE.get(genre, 0.03), 3)
    hi_atten_val = hi_atten(hi_boost_val)

    # ── 增益补偿 ──
    lvl_out = round(0.492 - (hi_boost_val * 0.08) - (lo_boost_val * 0.02), 3)
    lvl_out_db = round((lvl_out - 0.492) * 13.0, 1)

    # ── 日志 ──
    lo_hz = {0.667: "60Hz", 1.0: "100Hz"}.get(lo_cps, f"{lo_cps:.3f}")
    kcs_bst_khz = {0.667: 10, 0.833: 12, 1.0: 16}.get(kcs_bst, "?")
    log.info(
        "Auto-EQ232D: @%s boost=%.3f(%.1f) atten=%.3f(%.1f) | "
        "Hi@%skHz boost=%.3f(%.1f) atten=%.3f@20kHz bw=%.2f | "
        "LVL_OUT=%.1fdB | Stereo Ch2=mirror (genre=%s gender=%s deficit=%.0f)",
        lo_hz, lo_boost_val, lo_boost_val * 14,
        lo_atten_val, lo_atten_val * 14,
        kcs_bst_khz, hi_boost_val, hi_boost_val * 14,
        hi_atten_val, hi_bw_val, lvl_out_db,
        genre, gender or "?", deficit,
    )

    return {
        "CHANNEL": 0.0, "MS MATRIX": 0.0,
        # Ch2（先写，镜像 Ch1）
        "PEQ IN 2": 1.0, "HPF IN 2": 0.0, "EQ1 IN 2": 0.0, "EQ2 IN 2": 0.0,
        "LO CPS 2": lo_cps, "LO BOOST 2": lo_boost_val, "LO ATTEN 2": lo_atten_val,
        "HI BOOST 2": hi_boost_val, "HI ATTEN 2": hi_atten_val, "HI BW 2": hi_bw_val,
        "KCS BST 2": kcs_bst, "KCS ATT 2": kcs_att, "LVL OUT 2": lvl_out,
        # Ch1（后写，不被耦合覆写）
        "ENGAGE 1": 1.0, "HPF IN 1": 0.0, "EQ1 IN 1": 0.0, "EQ2 IN 1": 0.0,
        "PEQ IN 1": 1.0,
        "LO CPS 1": lo_cps, "LO BOOST 1": lo_boost_val, "LO ATTEN 1": lo_atten_val,
        "HI BOOST 1": hi_boost_val, "HI ATTEN 1": hi_atten_val, "HI BW 1": hi_bw_val,
        "KCS BST 1": kcs_bst, "KCS ATT 1": kcs_att, "LVL OUT 1": lvl_out,
    }
