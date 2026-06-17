"""
MicroShift (Soundtoys) — 立体声展宽/微音高偏移。

模拟 Eventide H3000 / AMS DMX 15-80s 的经典 widening 效果。通过左右声道
微音高偏移 + 调制延迟 + 硬左右声像，创造宽立体声场。

Vocal A 链角色：人声 AUX send → MicroShift → 混响/延迟 bus 共享空间。
Mix=100%（AUX 全湿），主唱和展宽信号通过 send 电平控制干湿比。
"""

import logging

log = logging.getLogger(__name__)

# ── 参数校准 ──
# Detune [3]: 50-200, norm=0.5→100 (默认)
# Delay [4]: 50-200, norm=0.5→100 (默认)
# Focus [5]: 20-10000Hz, 指数, norm=0→20Hz(全频段)
# Style [6]: 0.0→I, 0.33→II, 1.0→III

# ── Style — 流派选择 ──
_STYLE_I = 0.0    # H3000 — 经典主唱
_STYLE_II = 0.33  # 变体
_STYLE_III = 1.0  # AMS — 更散的和声/电子

_STYLE_BY_GENRE: dict[str, float] = {
    "folk":                    _STYLE_I,
    "ballad":                  _STYLE_I,
    "chinese_folk_bel_canto":  _STYLE_I,
    "pop":                     _STYLE_I,
    "rock":                    _STYLE_I,
    "rap":                     _STYLE_II,
    "electronic":              _STYLE_III,
}
_DEFAULT_STYLE = _STYLE_I

# ── Focus — 流派选择交叉频率 ──
# 只处理此频率以上的展宽，保持低频单声道凝聚
_FOCUS_BY_GENRE: dict[str, float] = {
    "folk":                    0.13,   # ~100Hz — 保守，保持自然
    "ballad":                  0.18,   # ~150Hz
    "chinese_folk_bel_canto":  0.13,   # ~100Hz
    "pop":                     0.24,   # ~250Hz
    "rock":                    0.30,   # ~400Hz
    "rap":                     0.30,   # ~400Hz
    "electronic":              0.40,   # ~800Hz — 只展宽中高频
}
_DEFAULT_FOCUS = 0.24

# ── Detune — 纠错层：crest 驱动 ──
# crest 高→声音已经"大"→少加宽度
# norm=0.5→100(默认), norm=0.25→70, norm=0.75→140
_DETUNE_BASE = 0.55      # 略高于默认
_DETUNE_CREST_COEFF = -0.012  # crest 高→Detune 降
_DETUNE_MIN = 0.25       # ~70
_DETUNE_MAX = 0.70       # ~140

# ── Delay — 纠错层：crest 驱动 ──
_DELAY_BASE = 0.50       # 默认 100
_DELAY_CREST_COEFF = -0.010
_DELAY_MIN = 0.25
_DELAY_MAX = 0.65


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """MicroShift 归一化（0-1 clamp）。"""
    result: dict[str, float] = {}
    for key, val in physical.items():
        result[key] = max(0.0, min(1.0, val))
    return result


def build_params(ctx, *, post_crest_db: float = 0.0) -> dict:
    """从 FXBuildContext + post-RVox 信号推导 MicroShift 归一化参数。

    两层结构：
      纠错层 — post-RVox crest 驱动 Detune/Delay（动态越大→展宽越保守）。
      美化层 — 流派选择 Style + Focus 交叉频率。
    Mix=1.0（AUX 全湿），InputGain=0.333（0dB unity）。

    Parameters
    ----------
    ctx : FXBuildContext
        必须含 `.genre`。
    post_crest_db : float
        RVox 之后 mid-chain 重分析的实际波峰因数 (dB)。

    Returns
    -------
    dict
        归一化 0-1 参数。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"

    # ── 纠错层：crest → Detune / Delay ──
    detune = round(
        max(_DETUNE_MIN, min(_DETUNE_MAX,
            _DETUNE_BASE + post_crest_db * _DETUNE_CREST_COEFF)),
        3,
    )
    delay = round(
        max(_DELAY_MIN, min(_DELAY_MAX,
            _DELAY_BASE + post_crest_db * _DELAY_CREST_COEFF)),
        3,
    )

    # ── 美化层：流派 → Style / Focus ──
    style = _STYLE_BY_GENRE.get(genre, _DEFAULT_STYLE)
    focus = _FOCUS_BY_GENRE.get(genre, _DEFAULT_FOCUS)
    style_label = {0.0: "I", 0.33: "II", 1.0: "III"}.get(style, "?")

    log.info(
        "Auto-MicroShift: detune=%.0f delay=%.0f focus=%.0fHz style=%s "
        "(crest=%.1fdB genre=%s)",
        detune * 150 + 50, delay * 150 + 50,  # norm → display approx
        focus * 10000 if focus > 0 else 20,   # rough display
        style_label,
        post_crest_db, genre,
    )

    return {
        "Mix":       1.0,     # AUX 全湿
        "InputGain": 0.333,   # 0dB unity
        "Detune":    detune,
        "Delay":     delay,
        "Focus":     focus,
        "Style":     style,
    }
