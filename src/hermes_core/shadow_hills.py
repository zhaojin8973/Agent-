"""
Shadow Hills Mastering Compressor (Plugin Alliance) — 光电压缩 + 变压器音染。

双级压缩结构：光学级（平滑人声控制）+ 离散级（完全旁路）。人声链仅使用光学级，
搭配流派驱动的变压器染色（Steel/Iron/Nickel 三档）。

Vocal A 链角色（第 8/9 位）：Inflator 密度增强之后 → 最终电平平滑 → Maag Air EQ 之前。
"""

import logging

log = logging.getLogger(__name__)

# ── 光学阈值 — post-RVox crest + 流派偏移 ──
# 光学压缩器天然 RMS 响应（T4B 光电衰减器，类似 LA-2A）。
# crest 高 → 阈值低（panel 低）→ 少压，保留动态（民美/folk 的诉求）。
# crest 低 → 阈值高（panel 高）→ 可多压（rock/electronic 求一致性）。
# 流派偏移：民美/folk 负值（更少压），rock/electronic 正值（更多压）。
_THRESH_BASE = 0.45
_CREST_COEFFICIENT = -0.008  # 负值：crest 高→thresh 低→少压
_THRESH_MIN = 0.22
_THRESH_MAX = 0.42

# 流派阈值偏移 — 正值=更多压缩，负值=保留动态
_GENRE_THRESH_OFFSET: dict[str, float] = {
    "folk":                   -0.05,
    "chinese_folk_bel_canto": -0.05,
    "ballad":                 -0.02,
    "pop":                     0.00,
    "rock":                    0.05,
    "rap":                     0.05,
    "electronic":              0.08,
}
_DEFAULT_OFFSET = 0.0

# ── 增益联动 — 压越多（thresh 高）→ 补越多 ──
# 校准：thresh=panel 7(0.26) → gain=panel 12(0.47) 才能补回 1.5dB GR
_GAIN_BASE = 0.08
_GAIN_COEFFICIENT = 1.5
_GAIN_MIN = 0.30
_GAIN_MAX = 0.65

# ── Transformer 音染 — 流派差异化 ──
# 0.0=Steel(激进饱和)  0.5=Iron(温暖中频)  1.0=Nickel(干净闪亮)
_TRANSFORMER_BY_GENRE: dict[str, float] = {
    "folk":                    1.0,   # Nickel — 干净闪亮
    "ballad":                  1.0,   # Nickel
    "chinese_folk_bel_canto":  1.0,   # Nickel
    "pop":                     0.5,   # Iron — 温暖中频
    "rock":                    0.0,   # Steel — 激进饱和
    "rap":                     0.0,   # Steel
    "electronic":              0.0,   # Steel
}
_DEFAULT_TRANSFORMER = 0.5  # Iron


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """Shadow Hills 归一化（0-1 clamp，所有参数原本就是 0-1 比例）。"""
    result: dict[str, float] = {}
    for key, val in physical.items():
        result[key] = max(0.0, min(1.0, val))
    return result


def build_params(ctx, *, post_rms_db: float = -18.0, post_crest_db: float = 0.0) -> dict:
    """从 FXBuildContext + post-RVox 信号状态推导 Shadow Hills 归一化参数。

    两层结构：
      纠错层 — post-RVox crest + 流派偏移驱动光学阈值（crest 高→少压保留动态）。
               增益联动：压越多（thresh 高）→ makeup 越多。
      美化层 — 流派驱动 Transformer 音染选择。

    Parameters
    ----------
    ctx : FXBuildContext
        必须含 `.genre`。
    post_rms_db : float
        RVox 之后 mid-chain 重分析的实际 RMS (dB)，预留供日志。
    post_crest_db : float
        RVox 之后 mid-chain 重分析的实际波峰因数 (dB)。

    Returns
    -------
    dict
        归一化 0-1 参数。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"

    # ── 纠错层：crest + 流派偏移 → 光学阈值 → 增益联动 ──
    offset = _GENRE_THRESH_OFFSET.get(genre, _DEFAULT_OFFSET)
    optical_thresh = round(
        max(_THRESH_MIN, min(_THRESH_MAX,
            _THRESH_BASE + post_crest_db * _CREST_COEFFICIENT + offset)),
        3,
    )
    optical_gain = round(
        max(_GAIN_MIN, min(_GAIN_MAX,
            _GAIN_BASE + optical_thresh * _GAIN_COEFFICIENT)),
        3,
    )

    # ── 美化层：流派 → Transformer 音染 ──
    transformer = _TRANSFORMER_BY_GENRE.get(genre, _DEFAULT_TRANSFORMER)
    tx_label = {0.0: "Steel", 0.5: "Iron", 1.0: "Nickel"}.get(transformer, "?")

    log.info(
        "Auto-Shadow Hills: optical thresh=%.3f gain=%.3f "
        "| discrete=bypass | transformer=%s "
        "(RMS=%.1fdB crest=%.1fdB genre=%s offset=%+.2f)",
        optical_thresh, optical_gain, tx_label,
        post_rms_db, post_crest_db, genre, offset,
    )

    return {
        "Hardwire Bypass":       1.0,
        "Optical Bypass 1":      1.0,
        "Optical Threshold 1":   optical_thresh,
        "Optical Gain 1":        optical_gain,
        "Discrete Bypass 1":     0.0,
        "Discrete Ratio 1":      0.4,
        "Discrete Attack 1":     1.0,
        "Discrete Recover 1":    0.0,
        "Discrete Gain 1":       0.0,
        "Sidechain Filter 1":    0.0,
        "Transformer 1":         transformer,
    }
