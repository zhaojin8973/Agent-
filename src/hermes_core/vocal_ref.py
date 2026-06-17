"""
人声频谱参考模板 — 基于 LTASS 研究的纠错层。

两层设计：
  - 纠错层：实际频谱偏离参考模板 → EQ 补偿
  - 美化层：即使正常，按流派叠加额外加成（各插件自行实现）
"""

# (center_dB, tolerance_dB) — 相对 mid (500-2000Hz) 的 dB 差
FEMALE_REF: dict[str, tuple[float, float]] = {
    "low":       (-10.0, 8.0),
    "low_mid":   (-5.0,  6.0),
    "presence":  (-20.0, 8.0),
    "air":       (-32.0, 10.0),
}

MALE_REF: dict[str, tuple[float, float]] = {
    "low":       (-8.0,  8.0),
    "low_mid":   (-3.0,  6.0),
    "presence":  (-25.0, 8.0),
    "air":       (-37.0, 10.0),
}


def get_ref(gender: str) -> dict[str, tuple[float, float]]:
    """性别 → 参考模板。"""
    if gender == "male":
        return MALE_REF
    return FEMALE_REF


def relativize(band_energy: dict[str, float]) -> dict[str, float]:
    """绝对 band_energy → 相对 mid 的值（mid=0 基准）。"""
    mid_db = band_energy.get("mid", -60.0)
    return {k: v - mid_db for k, v in band_energy.items()}


def deviation(actual_rel: float, ref_center: float) -> float:
    """实际相对值 - 参考中心值。正=高于正常，负=低于正常。"""
    return actual_rel - ref_center


def is_outside(dev: float, tolerance: float) -> bool:
    """偏差是否超出容差窗口。"""
    return abs(dev) > tolerance
