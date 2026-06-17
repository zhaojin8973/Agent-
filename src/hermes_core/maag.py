"""
Maag EQ4 (Plugin Alliance) — Air Band 最终抛光。

Boost-only EQ。人声链仅使用 Air Band（空气感点缀），其余频段 0dB。
Body/presence 纠错已由上游 Q3 + EQ232D 处理，此处不重复。

Vocal A 链角色（第 9/9 位）：Shadow Hills 光学平滑之后 → 最终空气感点缀。
"""

import logging
from hermes_core.vocal_ref import relativize, FEMALE_REF, MALE_REF

log = logging.getLogger(__name__)

# ── Air Band 频率 — 流派选择 ──
# 0.50=10kHz, 0.75=20kHz（VST3 6 档: Off/2.5k/5k/10k/20k/40k）
_AIR_BAND_BY_GENRE: dict[str, float] = {
    "folk":                    0.50,   # 10kHz — 自然保守
    "ballad":                  0.50,   # 10kHz
    "chinese_folk_bel_canto":  0.50,   # 10kHz
    "pop":                     0.75,   # 20kHz — "magic" vocal
    "rock":                    0.75,   # 20kHz
    "rap":                     0.75,   # 20kHz
    "electronic":              0.75,   # 20kHz
}
_DEFAULT_AIR_BAND = 0.75

# ── Air Gain — air 偏差补偿 ──
# 0.0→0dB, 1.0→10dB 线性
_AIR_GAIN_COEFF = 0.02    # 每 1dB 偏差 → 0.02 norm (0.2dB boost)
_AIR_GAIN_MIN = 0.10      # 1.0 dB（最小微量抛光）
_AIR_GAIN_MAX = 0.50      # 5.0 dB


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """Maag EQ4 归一化（0-1 clamp）。"""
    result: dict[str, float] = {}
    for key, val in physical.items():
        result[key] = max(0.0, min(1.0, val))
    return result


def build_params(ctx, *, gender: str = "", spectrum: dict | None = None) -> dict:
    """从 FXBuildContext + post-RVox 频谱推导 Maag EQ4 归一化参数。

    两层结构：
      纠错层 — post-RVox air 偏差（relativize 转相对值，vs 参考模板）→ Air Gain。
      美化层 — 流派选择 Air Band 频率。
    160Hz/2.5kHz 保持 0dB — body/presence 已由上游 Q3 + EQ232D 处理。

    Parameters
    ----------
    ctx : FXBuildContext
        必须含 `.genre`。
    gender : str
        "female" / "male"，决定参考模板。
    spectrum : dict | None
        post-RVox 的 band_energy_db（绝对 A-weighted dB）。为 None 时默认 1dB。

    Returns
    -------
    dict
        归一化 0-1 参数。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"

    # ── 美化层：Air Band 频率 ──
    air_band = _AIR_BAND_BY_GENRE.get(genre, _DEFAULT_AIR_BAND)

    # ── 纠错层：air 偏差 → Air Gain ──
    if spectrum and spectrum.get("band_energy_db"):
        bands = spectrum["band_energy_db"]
        is_male = gender == "male"
        ref = MALE_REF if is_male else FEMALE_REF

        # relativize: 绝对 A-weighted dB → 相对 mid 值
        rel = relativize(bands)
        air_center = ref["air"][0]
        air_rel = rel.get("air", air_center)
        air_dev = air_center - air_rel  # 正=暗需要boost, 负=亮少boost
        raw_air = _AIR_GAIN_MIN + air_dev * _AIR_GAIN_COEFF
        air_gain = round(max(_AIR_GAIN_MIN, min(_AIR_GAIN_MAX, raw_air)), 3)

        log.info(
            "Auto-Maag: air=%s +%.1fdB (air_rel=%.1fdB dev=%+.1fdB ref=%.0f genre=%s gender=%s)",
            {0.50: "10k", 0.75: "20k"}.get(air_band, "?"),
            air_gain * 10, air_rel, air_dev, air_center, genre, gender,
        )
    else:
        air_gain = _AIR_GAIN_MIN
        log.info(
            "Auto-Maag: air=%s +%.1fdB (no spectrum) (genre=%s)",
            {0.50: "10k", 0.75: "20k"}.get(air_band, "?"),
            air_gain * 10, genre,
        )

    return {
        "Sub":         0.5,   # 0dB
        "40 Hz":       0.5,   # 0dB
        "160 Hz":      0.5,   # 0dB — body 由上游 Q3/EQ232D 处理
        "650 Hz":      0.5,   # 0dB
        "2.5 kHz":     0.5,   # 0dB — presence 由上游 Q3/EQ232D 处理
        "Air Gain":    air_gain,
        "Air Band":    air_band,
        "Level Trim":  1.0,
        "In/Out":      1.0,
    }
