"""
Decapitator 自动化参数模块
===========================
Soundtoys Decapitator Mono — 5 种模拟饱和建模的谐波增强器。
一个模块包含所有参数范围、流派表、推导公式。

参数范围（REAPER VST3，所有参数 0.0-1.0 归一化）
----------------------------------------------------
Drive:     0.00-1.00  (GUI: 0-10)
Style:     0.00-1.00  (A=0.0, E=0.25, N=0.5, T=0.75, P=1.0)
Mix:       0.00-1.00  (GUI: 0-100%)
Tone:      0.00-1.00  (暗→亮, PRE-saturation tilt EQ)
Output:    0.00-1.00  (输出补偿)
Punish:    0/1        (OFF/ON, +20dB 额外增益)
LowCut:    0.00-1.00  (预饱和低切)
HighCut:   0.00-1.00  (后饱和高切)
Thump:     0.00-1.00  (低频谐振隆起)
AutoGain:  0/1        (自动增益补偿)
Steep:     0/1        (高切陡度切换)

关键参数技术细节：
- Tone 是 PRE-saturation tilt EQ — 决定哪些频率被失真，不是 post
- Thump 在 LowCut 频率处加谐振隆起，需 LowCut > 0 才生效
- Drive 超过 0VU 产生可辨失真 — 人声极保守 (1-3/10 = 0.1-0.3)
- Punish = +20dB 额外增益，人声绝对 OFF

来源: Soundtoys 官方手册, Music Guy Mixing, Gearspace, AUDIO PLUGIN NEWS,
      SonicScoop (Qmillion), Produce Like A Pro
"""

import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 参数范围 — VST3 归一化
# ════════════════════════════════════════════════════════════════

# 所有参数在 VST3 中线性映射到 0.0-1.0
_PARAM_MIN = 0.0
_PARAM_MAX = 1.0

# Style 离散值
_STYLE_A = 0.0     # Ampex 350 — 磁带温暖
_STYLE_E = 0.25    # EMI 台子 — 平滑控台
_STYLE_N = 0.5     # Neve 台子 — 质感中频
_STYLE_T = 0.75    # Triode 三极管 — 管味偶次谐波
_STYLE_P = 1.0     # Pentode 五极管 — 激进（不用于人声）

# Drive clamp（人声安全范围：GUI 0.5-3.0）
_DRIVE_MIN = 0.05
_DRIVE_MAX = 0.30

# Tone clamp（pre-saturation tilt EQ 安全范围）
_TONE_MIN = 0.42
_TONE_MAX = 0.58

# OutputTrim clamp
_OUTPUT_MIN = 0.825
_OUTPUT_MAX = 1.0


def _normalize_linear(value: float, lo: float = _PARAM_MIN, hi: float = _PARAM_MAX) -> float:
    """线性映射 value[lo,hi] → [0,1]."""
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """Decapitator 物理参数 → 归一化字典 (0-1)。

    所有参数使用线性归一化，因为 VST3 暴露范围就是 0-1。
    """
    result = {}
    for key, val in physical.items():
        result[key] = max(0.0, min(1.0, val))
    return result


# ════════════════════════════════════════════════════════════════
# 流派表
# ════════════════════════════════════════════════════════════════

# Style 选择 — 流派匹配饱和建模特质
# 来源: Gearspace ("E for the win on vocals"),
#       Music Guy Mixing ("A with conservative Drive", "T is go to for vocal saturation")
_STYLE_BY_GENRE: dict[str, float] = {
    "folk":                    _STYLE_A,   # Ampex 磁带温暖 — 最保守最自然
    "ballad":                  _STYLE_A,   # Ampex 磁带温暖 — 柔和
    "chinese_folk_bel_canto":  _STYLE_E,   # EMI 平滑控台 — 大气细腻
    "pop":                     _STYLE_E,   # EMI 平滑控台 — 商业质感
    "rock":                    _STYLE_N,   # Neve 质感中频 — 有态度不极端
    "electronic":              _STYLE_T,   # Triode 管味 — 偶次谐波
}

# Drive 基础值 — crest=10dB 时的 drive 值
# 流派越重 → drive 越高，但全部在人声安全区内 (≤0.30)
_DRIVE_BASE: dict[str, float] = {
    "folk":                    0.12,
    "ballad":                  0.13,
    "chinese_folk_bel_canto":  0.16,
    "pop":                     0.20,
    "rock":                    0.24,
    "electronic":              0.28,
}

# Drive K — crest 偏离 10 时每 dB 调整多少 drive
# 高 K → crest 变化时 drive 响应更敏感
_DRIVE_K: dict[str, float] = {
    "folk":                    0.006,
    "ballad":                  0.006,
    "chinese_folk_bel_canto":  0.008,
    "pop":                     0.010,
    "rock":                    0.010,
    "electronic":              0.008,
}

# Mix — 流派差异化（干湿混合比）
# 来源: 知识库 + SonicScoop Qmillion
_MIX_BY_GENRE: dict[str, float] = {
    "folk":                    0.30,
    "ballad":                  0.35,
    "chinese_folk_bel_canto":  0.35,
    "pop":                     0.40,
    "rock":                    0.40,
    "electronic":              0.50,
}

# Tone 基础值 — pre-saturation tilt EQ
# <0.5 = 暗（低频先失真→暖厚）; >0.5 = 亮（高频先失真→清晰）
# 来源: Soundtoys 官方手册 "Tone knob affects sound BEFORE saturation"
_TONE_BASE: dict[str, float] = {
    "folk":                    0.45,   # 偏暗 → 温暖磁带感
    "ballad":                  0.45,   # 偏暗 → 温暖
    "chinese_folk_bel_canto":  0.48,   # 微暗 → 圆润大气
    "pop":                     0.50,   # 中性
    "rock":                    0.53,   # 微亮 → 清晰有力
    "electronic":              0.55,   # 偏亮 → 颗粒感
}

# 默认流派 (未知时回退)
_DEFAULT_GENRE = "pop"


# ════════════════════════════════════════════════════════════════
# 推导公式
# ════════════════════════════════════════════════════════════════

def style_code(genre: str = "pop") -> float:
    """流派 → Style 归一化值。

    A=0.0 (Ampex), E=0.25 (EMI), N=0.5 (Neve), T=0.75 (Triode).
    """
    return _STYLE_BY_GENRE.get(genre, _STYLE_BY_GENRE[_DEFAULT_GENRE])


def drive(crest_db: float, genre: str = "pop") -> float:
    """波峰因数 + 流派 → Drive 归一化值 (0.05-0.30)。

    crest 越大（动态丰富）→ drive 越低（保留瞬态）
    crest 越小（已经压缩）→ drive 越高（增加谐波密度）
    """
    base = _DRIVE_BASE.get(genre, _DRIVE_BASE[_DEFAULT_GENRE])
    k = _DRIVE_K.get(genre, _DRIVE_K[_DEFAULT_GENRE])
    val = base - (crest_db - 10.0) * k
    return round(max(_DRIVE_MIN, min(_DRIVE_MAX, val)), 3)


def mix_val(genre: str = "pop") -> float:
    """流派 → Mix 归一化值 (0.30-0.50)。"""
    return _MIX_BY_GENRE.get(genre, _MIX_BY_GENRE[_DEFAULT_GENRE])


def tone_val(genre: str = "pop", presence_deficit: float = 0.0) -> float:
    """流派 + 频谱暗亮度 → Tone 归一化值 (0.42-0.58)。

    Tone 是 pre-saturation tilt——影响哪些频率被失真：
    - < 0.5 (暗) → 低频先失真 → 暖厚
    - > 0.5 (亮) → 高频先失真 → 清晰颗粒

    presence_deficit > 0 说明声音偏暗 → Tone 适当提高补偿。
    """
    base = _TONE_BASE.get(genre, _TONE_BASE[_DEFAULT_GENRE])
    val = base + presence_deficit * 0.015
    return round(max(_TONE_MIN, min(_TONE_MAX, val)), 3)


def output_trim(drive_val: float) -> float:
    """Drive → OutputTrim 补偿值。

    Drive 增加信号电平，OutputTrim 适当衰减保持 unity gain。
    公式: OutputTrim = 1.0 - Drive * 0.25
    clamp(0.825, 1.0)
    """
    val = 1.0 - drive_val * 0.25
    return round(max(_OUTPUT_MIN, min(_OUTPUT_MAX, val)), 3)


# ════════════════════════════════════════════════════════════════
# Builder — 供 fx_builder 调用
# ════════════════════════════════════════════════════════════════

def build_params(ctx) -> dict | None:
    """从 FXBuildContext 推导完整的 Decapitator 物理参数。

    由 fx_builder._build_decapitator_params 在检测到 Decapitator 时调用。

    Returns
    -------
    dict or None
        物理参数字典，rms/peak 缺失时返回 None。
    """
    rms = ctx.raw_rms_db
    peak = ctx.raw_peak_db
    if rms is None or peak is None:
        return None

    crest = peak - rms
    genre = ctx.genre or _DEFAULT_GENRE
    presence = getattr(ctx, "presence_deficit", 0.0) or 0.0

    sty = style_code(genre)
    drv = drive(crest, genre)
    mix = mix_val(genre)
    ton = tone_val(genre, presence)
    out = output_trim(drv)

    physical = {
        "Style":       sty,
        "Drive":       drv,
        "Punish":      0.0,
        "LowCut":      0.0,
        "Tone":        ton,
        "HighCut":     0.0,
        "Mix":         mix,
        "AutoGain":    0.0,
        "LowThump":    0.0,
        "HighSlope":   0.0,
        "OutputTrim":  out,
    }

    log.info(
        "Decapitator: crest=%.1fdB → Drive=%.3f Style=%.2f "
        "Tone=%.3f Mix=%.0f%% OutputTrim=%.3f (genre=%s)",
        crest, drv, sty, ton, mix * 100, out, genre,
    )
    return physical
