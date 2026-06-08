"""
CLA-76 自动化参数模块
=====================
Waves CLA-76 Mono — 1176 风格 FET 峰值压缩器。
一个模块包含所有参数范围、流派表、推导公式。

参数范围（REAPER VST3 实测 2026-06-08）
--------------------------------------
Input:  -Inf ~ 0 dB, 分段线性, 拐点 -36 dB
Output: -Inf ~ 0 dB, 分段线性, 拐点 -36 dB
Attack: 1.0 ~ 7.0, 线性, CW=快
Release: 1.0 ~ 7.0, 线性, CW=快
"""

import bisect
import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 参数范围 — REAPER 实测
# ════════════════════════════════════════════════════════════════

# Input/Output 分段线性归一化
# norm 0.00 → -Inf (特殊)
# norm 0.05-0.25 → 96 dB/norm (陡峭段, -55.2 ~ -36.0)
# norm 0.25-1.00 → 48 dB/norm (平坦段, -36.0 ~ 0.0)
_IO_BREAK_NORM = 0.25
_IO_BREAK_DB = -36.0
_IO_STEEP_SLOPE = 96.0   # dB per norm
_IO_FLAT_SLOPE = 48.0     # dB per norm
_IO_STEEP_LO_DB = -55.2   # norm=0.05 对应的 dB
_IO_STEEP_LO_NORM = 0.05

# Attack/Release 线性
_ATTACK_MIN = 1.0
_ATTACK_MAX = 7.0
_RELEASE_MIN = 1.0
_RELEASE_MAX = 7.0


def _normalize_io(physical_db: float) -> float:
    """Input/Output 物理值 → 归一化 (0-1)。分段线性。"""
    if physical_db <= _IO_STEEP_LO_DB:
        return 0.0
    if physical_db <= _IO_BREAK_DB:
        return _IO_STEEP_LO_NORM + (physical_db - _IO_STEEP_LO_DB) / _IO_STEEP_SLOPE
    return _IO_BREAK_NORM + (physical_db - _IO_BREAK_DB) / _IO_FLAT_SLOPE


def _normalize_linear(value: float, lo: float, hi: float) -> float:
    """线性映射 value[lo,hi] → [0,1]."""
    return max(0.0, min(1.0, (value - lo) / (hi - lo)))


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """CLA-76 物理参数 → 归一化字典。"""
    result = {}
    for key, val in physical.items():
        if key in ("Input", "Output"):
            result[key] = _normalize_io(val)
        elif key == "Attack":
            result[key] = _normalize_linear(val, _ATTACK_MIN, _ATTACK_MAX)
        elif key == "Release":
            result[key] = _normalize_linear(val, _RELEASE_MIN, _RELEASE_MAX)
    return result


# ════════════════════════════════════════════════════════════════
# 流派表
# ════════════════════════════════════════════════════════════════

# GR ratio — crest 乘此系数得到 GR 目标 (dB)
# 来源: 业界 1176 人声压缩实践
#   folk/ballad: 轻 (~3dB) — 自然呼吸
#   CFBC: 中轻 (~4dB) — 大气但不过度控制
#   pop/rock: 中 (~5dB) — 商业标准
#   electronic: 重 (~6dB) — 紧密控制
_GR_RATIO: dict[str, float] = {
    "folk":                    0.14,
    "ballad":                  0.15,
    "chinese_folk_bel_canto":  0.14,   # 民美轻压，保留大气线条
    "pop":                     0.20,
    "rock":                    0.21,
    "electronic":              0.25,
}

# Attack base — crest=10dB 时的 knob 值
# 复古风格 → 慢 attack（保瞬态）; 现代风格 → 快 attack（紧实）
_ATTACK_BASE: dict[str, float] = {
    "folk":                    2.3,    # 最慢 — 保呼吸感
    "ballad":                  2.3,    # 慢 — 柔和
    "chinese_folk_bel_canto":  2.8,    # 偏慢 — 民美实测锚点
    "pop":                     3.5,    # 标准 — 比民美快半档
    "rock":                    4.0,    # 偏快 — 紧实有力
    "electronic":              4.5,    # 快 — 紧密控制
}

# Attack k — crest 偏离 10 时每 dB 调整多少 knob
_ATTACK_K: dict[str, float] = {
    "folk":                    0.05,
    "ballad":                  0.05,
    "chinese_folk_bel_canto":  0.08,
    "pop":                     0.10,
    "rock":                    0.10,
    "electronic":              0.06,
}

# Release factor — 60000/BPM 乘以此系数
# 紧风格需要更快恢复（小 factor）, 松风格可以慢（大 factor）
_RELEASE_FACTOR: dict[str, float] = {
    "electronic":              0.45,   # 最紧
    "pop":                     0.60,   # 适中偏紧
    "rock":                    0.60,   # 适中
    "chinese_folk_bel_canto":  0.65,   # 适中偏松 — 比 pop 稍慢
    "folk":                    0.80,   # 松
    "ballad":                  0.85,   # 最松
}

# 默认流派 (未知时回退)
_DEFAULT_GENRE = "pop"


# ════════════════════════════════════════════════════════════════
# ms ↔ knob 转换表
# ════════════════════════════════════════════════════════════════

# Release ms → knob (线性插值)
_RELEASE_MS_TABLE: list[tuple[float, float]] = [
    (50.0,   7.0),
    (150.0,  6.0),
    (300.0,  5.0),
    (500.0,  4.0),
    (700.0,  3.0),
    (900.0,  2.0),
    (1100.0, 1.0),
]


def _ms_to_knob(ms: float, table: list[tuple[float, float]]) -> float:
    """ms → knob 二分查表 + 线性插值。"""
    ms_list = [r[0] for r in table]
    if ms <= ms_list[0]:
        return table[0][1]
    if ms >= ms_list[-1]:
        return table[-1][1]
    idx = bisect.bisect_left(ms_list, ms)
    lo_ms, lo_knob = table[idx - 1]
    hi_ms, hi_knob = table[idx]
    t = (ms - lo_ms) / (hi_ms - lo_ms)
    return lo_knob + t * (hi_knob - lo_knob)


# ════════════════════════════════════════════════════════════════
# 推导公式
# ════════════════════════════════════════════════════════════════

def gr_target(crest_db: float, genre: str = "pop") -> float:
    """波峰因数 → GR 目标 (dB)。"""
    ratio = _GR_RATIO.get(genre, _GR_RATIO[_DEFAULT_GENRE])
    return round(crest_db * ratio, 1)


def input_db(gr: float, peak_db: float, genre: str = "pop") -> float:
    """GR 目标 + 峰值 → Input 物理值 (dB)。

    基于望归 vocal 实测校准 (2026-05-31)。
    将目标 GR 映射到 CLA-76 Input 衰减值。
    """
    base = -40.4
    slope = 0.46
    val = base + gr * slope - peak_db
    return round(max(_IO_STEEP_LO_DB, min(0.0, val)), 1)


def output_db(input_val: float, gr: float) -> float:
    """Input + GR → Output 衰减值 (dB), 保持 unity through 76。

    推导:
      CLA-76 默认 Input=-30, Output=-18 时 unity (无 GR)。
      增加 Input (更多驱动) → FET 输出更热 → Output 需要更多衰减。
      GR 减小输出电平 → Output 需要更少衰减。
      Output = -48 - Input - GR
    """
    val = -50.0 - input_val - gr
    return round(max(_IO_STEEP_LO_DB, min(0.0, val)), 1)


def attack_knob(crest_db: float, genre: str = "pop") -> float:
    """波峰因数 + 流派 → Attack knob (1-7)。"""
    base = _ATTACK_BASE.get(genre, _ATTACK_BASE[_DEFAULT_GENRE])
    k = _ATTACK_K.get(genre, _ATTACK_K[_DEFAULT_GENRE])
    knob = base - (crest_db - 10.0) * k
    return round(max(_ATTACK_MIN, min(_ATTACK_MAX, knob)), 2)


def release_knob(bpm: float | None, genre: str = "pop") -> float:
    """BPM + 流派 → Release knob (1-7)。
    无 BPM 时默认中速 knob 4。
    """
    if bpm is None or bpm <= 0:
        return 4.0
    factor = _RELEASE_FACTOR.get(genre, _RELEASE_FACTOR[_DEFAULT_GENRE])
    ms = 60000.0 / bpm * factor
    return round(_ms_to_knob(ms, _RELEASE_MS_TABLE), 2)


# ════════════════════════════════════════════════════════════════
# Builder — 供 fx_builder 调用
# ════════════════════════════════════════════════════════════════

def build_params(ctx) -> dict | None:
    """从 FXBuildContext 推导完整的 CLA-76 物理参数。

    由 fx_builder._build_compressor_params 在检测到 CLA-76 时调用。
    """
    rms = ctx.raw_rms_db
    peak = ctx.raw_peak_db
    if rms is None or peak is None:
        return None

    crest = peak - rms
    genre = ctx.genre or _DEFAULT_GENRE
    bpm = ctx.bpm

    gr = gr_target(crest, genre)
    inp = input_db(gr, peak, genre)
    out = output_db(inp, gr)
    atk = attack_knob(crest, genre)
    rel = release_knob(bpm, genre)

    physical = {
        "Input":   inp,
        "Output":  out,
        "Attack":  atk,
        "Release": rel,
    }

    log.info(
        "CLA-76: crest=%.1fdB GR=%.1fdB → Input=%.1f Output=%.1f "
        "Attack=%.2f Release=%.2f (genre=%s BPM=%s)",
        crest, gr, inp, out, atk, rel, genre, bpm or "?",
    )
    return physical
