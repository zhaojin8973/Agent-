"""
Oxford Inflator (Sonnox) — 心理声学响度增强器。

Inflator 不是饱和器、不是压缩器。它通过正弦波整形曲线改变采样点概率分布，
增加感知响度和谐波密度，同时保持完整动态范围。

Vocal A/B 链角色（第 7/9 位）：RVox 身体压缩之后 → 密度恢复 → 光电压缩之前。
"""

import logging

log = logging.getLogger(__name__)

# ── 流派 Curve 映射（物理值 → 归一化） ──
# Curve: -50 最微妙 .. 0 平衡 .. +50 最激进
# 归一化: (physical + 50) / 100
_CURVE_BY_GENRE: dict[str, float] = {
    "folk":                    0.20,   # -30 — 最保守，保持动态
    "ballad":                  0.20,   # -30
    "chinese_folk_bel_canto":  0.20,   # -30
    "pop":                     0.30,   # -20 — Jason Goldstein 推荐
    "rock":                    0.40,   # -10 — 需要更多存在感
    "rap":                     0.40,   # -10
    "electronic":              0.50,   #   0 — 最大化冲击力
}
_DEFAULT_CURVE = 0.30  # -20

# ── 流派 Effect 美化层偏移 ──
_EFFECT_ENHANCE: dict[str, float] = {
    "folk":                     0.00,
    "ballad":                   0.03,
    "chinese_folk_bel_canto":   0.00,
    "pop":                      0.05,
    "rock":                     0.08,
    "rap":                      0.08,
    "electronic":               0.10,
}
_DEFAULT_ENHANCE = 0.03

# ── Clip 0dB — 流派差异化 ──
# ON (1.0) = 干净响度增强；OFF (0.0) = 阀管过载特性
_CLIP_BY_GENRE: dict[str, float] = {
    "folk":                    1.0,
    "ballad":                  1.0,
    "chinese_folk_bel_canto":  1.0,
    "pop":                     1.0,
    "rock":                    0.0,   # valve overdrive
    "rap":                     0.0,
    "electronic":              0.0,
}
_DEFAULT_CLIP = 1.0

# ── Effect 范围约束 ──
_EFFECT_MIN = 0.15
_EFFECT_MAX = 0.55
_EFFECT_CREST_COEFFICIENT = 0.022  # post-RVox crest → Effect base 转换系数

# ── Gain 归零点（REAPER 校准） ──
# Input Gain 物理范围: -6.0 ~ +12.0 dB → 0 dB = 6/18 = 0.333
# Output Gain 物理范围: -12.0 ~ 0.0 dB → 0 dB = 12/12 = 1.0
_INPUT_UNITY = 0.333
_OUTPUT_UNITY = 1.0


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """Inflator 归一化（0-1 clamp，参数原本就是 0-1 比例）。"""
    result: dict[str, float] = {}
    for key, val in physical.items():
        result[key] = max(0.0, min(1.0, val))
    return result


def build_params(ctx, *, post_crest_db: float = 0.0) -> dict:
    """从 FXBuildContext + post-RVox 信号状态推导 Inflator 归一化参数。

    两层结构：
      纠错层 — post_crest_db（mid-chain 重分析的实际波峰因数）驱动 Effect。
               post-RVox crest 越高 → 信号越动态 → 需要更多密度增强。
      美化层 — 流派驱动 Effect 偏移 + Curve + Clip 0dB 特性选择。

    Parameters
    ----------
    ctx : FXBuildContext
        必须含 `.genre`。
    post_crest_db : float
        RVox 之后 mid-chain 重分析的实际波峰因数 (peak - RMS, dB)。
        20 dB = 非常动态，0 dB = 非常密实。

    Returns
    -------
    dict
        归一化 0-1 参数: Effect, Curve, Clip 0dB, Input/Output Gain, Band Split, In。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"

    # ── 纠错层：post-RVox crest → Effect 基础值 ──
    base_effect = post_crest_db * _EFFECT_CREST_COEFFICIENT
    base_effect = max(_EFFECT_MIN, min(_EFFECT_MAX, base_effect))

    # ── 美化层：流派偏移 ──
    enhance = _EFFECT_ENHANCE.get(genre, _DEFAULT_ENHANCE)
    effect = base_effect + enhance
    effect = round(max(_EFFECT_MIN, min(_EFFECT_MAX, effect)), 3)

    # ── Curve ──
    curve = _CURVE_BY_GENRE.get(genre, _DEFAULT_CURVE)

    # ── Clip 0dB ──
    clip_0db = _CLIP_BY_GENRE.get(genre, _DEFAULT_CLIP)

    log.info(
        "Auto-Inflator: effect=%.0f%% curve=%.2f clip=%s "
        "(crest=%.1fdB base=%.3f enhance=%.3f genre=%s)",
        effect * 100, curve, "ON" if clip_0db > 0.5 else "OFF",
        post_crest_db, base_effect, enhance, genre,
    )

    return {
        "Input Gain":  _INPUT_UNITY,
        "Effect":      effect,
        "Curve":       curve,
        "Output Gain": _OUTPUT_UNITY,
        "In":          1.0,
        "Band Split":  0.0,
        "Clip 0dB":    clip_0db,
    }
