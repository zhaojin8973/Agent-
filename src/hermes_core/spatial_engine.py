"""
空间效果发送量计算引擎。

基于人声信号分析（波峰因子、存在感缺失、浑浊度、齿音峰值）
计算流派感知的混响和延迟发送量。
"""

from hermes_core.genre_tables import (
    _GENRE_REVERB_SEND_BASE,
    _GENRE_DELAY_SEND_BASE,
    _SEND_LEVEL_MIN,
    _SEND_LEVEL_MAX,
    _SEND_DISABLED_THRESHOLD,
    _CREST_REFERENCE,
    _PRESENCE_DEFICIT_THRESHOLD,
    _SIBILANCE_REFERENCE_PEAK,
    _SECTION_BOOST,
)


def _compute_spatial_sends(
    genre: str,
    crest_factor_db: float,
    presence_deficit_db: float,
    mud_ratio_db: float,
    sibilance_peak_db: float | None = None,
    section: str = "verse",
) -> dict[str, float | None]:
    """根据人声信号分析计算混响和延迟发送量。

    每条发送量由流派参考基准值推导，再通过四个客观偏差调整：

    - **crest_bias**：高波峰因子的 vocals 已经听起来"大" —
      减少混响以避免冲淡动态。
    - **density_bias**：浑浊的 vocals 获得更少混响，避免在浑浊上
      堆积低频能量。
    - **presence_bias**：沉闷的人声（高存在感缺失）应保持前置 —
      混响会将其推远。
    - **sibilance_bias**（仅 plate）：plate 混响在 5–8 kHz 共振，
      所以明亮齿音的人声获得更少 plate 发送。

    Returns a dict mapping bus keys to send levels in dB.
    ``None`` means the bus is disabled for this genre (no need to
    create it).
    """
    _DEFAULT_REVERB = _GENRE_REVERB_SEND_BASE["pop"]
    _DEFAULT_DELAY = _GENRE_DELAY_SEND_BASE["pop"]

    base_reverb = _GENRE_REVERB_SEND_BASE.get(genre, _DEFAULT_REVERB)
    base_delay = _GENRE_DELAY_SEND_BASE.get(genre, _DEFAULT_DELAY)

    # ── 偏差计算 ──────────────────────────────────────────
    crest_bias = -(crest_factor_db - _CREST_REFERENCE) * 0.5
    density_bias = mud_ratio_db * 0.3
    presence_bias = -(presence_deficit_db - _PRESENCE_DEFICIT_THRESHOLD) * 0.3
    section_bias = _SECTION_BOOST.get(section, 0.0)

    sibilance_bias = 0.0
    if sibilance_peak_db is not None:
        sibilance_bias = -max(0.0, sibilance_peak_db - _SIBILANCE_REFERENCE_PEAK) * 0.1

    # ── 组装发送量 ─────────────────────────────────────────
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
