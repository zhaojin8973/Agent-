"""
Pro-DS 自动化参数模块
========================
FabFilter Pro-DS — 单声道齿音消除器。
一个模块包含所有参数范围、流派表、推导公式。

参数范围（REAPER VST 实测）
---------------------------
Threshold:  -60 ~ 0 dB（线性）
Range:      0 ~ 24 dB（线性）
HPF:        2k ~ 20k Hz（log 归一化, 0-1）
LPF:        2k ~ 20k Hz（log 归一化, 0-1）
Mode:       Wide Band=0 / Single Vocal=1
Lookahead:  0 ~ 15 ms

设计原则（文献验证 2026-06-08）
------------------------------
- Threshold 基于 sidechain 过滤后的 band 能量 + crest-informed margin
  来源: FabFilter Pro-DS 官方手册 ("Threshold sets the threshold
  of the side-chain level"), Sound On Sound, AudioSpectra
- HPF/LPF 动态窗口中心化于 sib_peak，性别感知 clamp
  来源: Produce Like A Pro (女 7-8k/男 5-6k), Audio Issues (女 5-9k/男 3-7k),
  Wikipedia (齿音 4-10k)
"""

import logging
import math

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 参数范围 — REAPER 实测
# ════════════════════════════════════════════════════════════════

_THRESHOLD_MIN = -60.0
_THRESHOLD_MAX = 0.0

_RANGE_MIN = 0.0
_RANGE_MAX = 24.0

_LOOKAHEAD_MIN = 0.0
_LOOKAHEAD_MAX = 15.0   # ms

_INOUT_MIN = -30.0
_INOUT_MAX = 30.0       # dB

# Pro-DS 检测频段 clamp 边界（Hz）
# 整体上移 500 Hz — 远离共振峰区，保护人声本体
# 来源: FabFilter 手册 + 行业性别共识 (Produce Like A Pro, Audio Issues, Wikipedia)
_HPF_FEMALE_LO = 5000.0
_HPF_FEMALE_HI = 6500.0
_LPF_FEMALE_LO = 10000.0
_LPF_FEMALE_HI = 12500.0

_HPF_MALE_LO = 4000.0
_HPF_MALE_HI = 5500.0
_LPF_MALE_LO = 8500.0
_LPF_MALE_HI = 10500.0

_HPF_DEFAULT_LO = 4500.0
_HPF_DEFAULT_HI = 5500.0
_LPF_DEFAULT_LO = 9500.0
_LPF_DEFAULT_HI = 12500.0

# sib_peak 偏移量（Hz）— HPF 从峰下方展开，LPF 从峰上方展开
# 窗口 clamp 已保护共振峰（5k+ 女 / 4k+ 男），sib_peak 负责微调位置
_SIB_HPF_OFFSET = 1500.0
_SIB_LPF_OFFSET_FEMALE = 3000.0
_SIB_LPF_OFFSET_MALE = 2500.0
_SIB_LPF_OFFSET_DEFAULT = 3000.0

# FabFilter 内部 log 参考点
_DETECT_REF_HZ = 2000.0

# Threshold margin — crest 系数
_MARGIN_CREST_K = 0.4
_MARGIN_MIN = 4.0
_MARGIN_MAX = 10.0

# Threshold clamp
_THR_CLAMP_LO = -40.0
_THR_CLAMP_HI = -10.0

# 默认 sib_peak (Hz) — 频谱不可用时回退
_DEFAULT_SIB_PEAK = 7000.0

# 固定参数
_MODE_WIDEBAND = 0.0          # Wide Band（FabFilter 推荐单声道人声首选）
_BAND_PROCESSING = 0.0         # Wide Band 模式
_LOOKAHEAD_MS = 10.0          # ms
_LOOKAHEAD_ENABLED = 1.0      # ON
_INPUT_LEVEL_DB = 0.0         # unity
_OUTPUT_LEVEL_DB = 0.0        # unity
_WET = 1.0                    # 100%


def _normalize_log_freq(hz: float, ref_hz: float = _DETECT_REF_HZ) -> float:
    """FabFilter log 频率归一化: log10(freq/ref) → 0-1 clamp."""
    val = math.log10(hz / ref_hz)
    return round(max(0.0, min(1.0, val)), 4)


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """Pro-DS 物理参数 → 归一化字典 (0-1)。"""
    result = {}
    for key, val in physical.items():
        if key == "Threshold":
            result[key] = round(max(0.0, min(1.0, (val - _THRESHOLD_MIN) / (_THRESHOLD_MAX - _THRESHOLD_MIN))), 4)
        elif key == "Range":
            result[key] = round(max(0.0, min(1.0, (val - _RANGE_MIN) / (_RANGE_MAX - _RANGE_MIN))), 4)
        elif key == "Lookahead":
            result[key] = round(max(0.0, min(1.0, (val - _LOOKAHEAD_MIN) / (_LOOKAHEAD_MAX - _LOOKAHEAD_MIN))), 4)
        elif key in ("Input Level", "Output Level"):
            result[key] = round(max(0.0, min(1.0, (val - _INOUT_MIN) / (_INOUT_MAX - _INOUT_MIN))), 4)
        else:
            result[key] = round(max(0.0, min(1.0, val)), 4)
    return result


# ════════════════════════════════════════════════════════════════
# 流派表
# ════════════════════════════════════════════════════════════════

# Range (最大增益衰减量 dB) — 流派差异化
# 稀疏编曲 → 低 Range（自然人声，轻触）
# 密集编曲 → 高 Range（强力齿音控制）
_RANGE_BY_GENRE: dict[str, float] = {
    "folk":                    6.0,
    "ballad":                  6.0,
    "chinese_folk_bel_canto":  7.0,
    "pop":                     8.5,
    "rock":                    8.5,
    "electronic":              9.0,
}

_DEFAULT_GENRE = "pop"


# ════════════════════════════════════════════════════════════════
# 推导公式
# ════════════════════════════════════════════════════════════════

def threshold_db(
    band_rms: float,
    crest_db: float,
) -> float:
    """检测频段 RMS + 波峰因数 → Threshold (dB)。

    band_rms: 检测频段（presence 5-8k）的 A-weighted 平均能量 (dB)。
    crest_db: 信号整体波峰因数 (peak - rms)。

    公式: Threshold = band_rms + margin
    其中 margin = clamp(crest × 0.4, 4, 10)

    - band_rms 确保 Threshold 高于常态能量（不误触发）
    - margin 基于 crest 留空间给齿音峰（抓得到齿音）
    - clamp(-40, -10) 防止极端值
    """
    margin = max(_MARGIN_MIN, min(_MARGIN_MAX, crest_db * _MARGIN_CREST_K))
    val = band_rms + margin
    return round(max(_THR_CLAMP_LO, min(_THR_CLAMP_HI, val)), 1)


def range_db(genre: str = "pop") -> float:
    """流派 → Range (dB)。"""
    return _RANGE_BY_GENRE.get(genre, _RANGE_BY_GENRE[_DEFAULT_GENRE])


def detection_hpf(sib_peak_hz: float, gender: str = "") -> float:
    """sib_peak + 性别 → 检测 HPF 频率 (Hz)。

    女性: clamp(sib_peak - 1500, 4500, 6000)
    男性: clamp(sib_peak - 1500, 3500, 5000)
    默认: clamp(sib_peak - 1500, 4000, 5000)
    """
    raw = sib_peak_hz - _SIB_HPF_OFFSET
    if gender == "female":
        return round(max(_HPF_FEMALE_LO, min(_HPF_FEMALE_HI, raw)))
    elif gender == "male":
        return round(max(_HPF_MALE_LO, min(_HPF_MALE_HI, raw)))
    else:
        return round(max(_HPF_DEFAULT_LO, min(_HPF_DEFAULT_HI, raw)))


def detection_lpf(sib_peak_hz: float, gender: str = "") -> float:
    """sib_peak + 性别 → 检测 LPF 频率 (Hz)。

    女性: clamp(sib_peak + 3000, 9500, 12000)
    男性: clamp(sib_peak + 2500, 8000, 10000)
    默认: clamp(sib_peak + 3000, 9000, 12000)
    """
    if gender == "female":
        offset = _SIB_LPF_OFFSET_FEMALE
        lo, hi = _LPF_FEMALE_LO, _LPF_FEMALE_HI
    elif gender == "male":
        offset = _SIB_LPF_OFFSET_MALE
        lo, hi = _LPF_MALE_LO, _LPF_MALE_HI
    else:
        offset = _SIB_LPF_OFFSET_DEFAULT
        lo, hi = _LPF_DEFAULT_LO, _LPF_DEFAULT_HI
    return round(max(lo, min(hi, sib_peak_hz + offset)))


# ════════════════════════════════════════════════════════════════
# Builder — 供 fx_builder 调用
# ════════════════════════════════════════════════════════════════

def build_params(ctx, gender: str = "", *, spectrum: dict | None = None) -> dict:
    """从 FXBuildContext + 频谱数据推导完整的 Pro-DS 物理参数。

    Parameters
    ----------
    ctx : FXBuildContext
        推导上下文（含 rms/peak）。
    gender : str
        "female" / "male" / ""（未知）。影响 HPF/LPF 检测窗口。
    spectrum : dict | None
        mid-chain 频谱分析结果，含 sibilance_peak_hz, band_energy_db 等。

    Returns
    -------
    dict
        物理参数字典。
    """
    genre = ctx.genre or _DEFAULT_GENRE

    # ── 频谱数据（来自 mid-chain 重分析） ──
    if spectrum is None:
        spectrum = {}

    # sib_peak — 4-12k 齿音能量峰
    sib_peak = spectrum.get("sibilance_peak_hz", _DEFAULT_SIB_PEAK)
    if not isinstance(sib_peak, (int, float)) or sib_peak <= 0:
        sib_peak = _DEFAULT_SIB_PEAK

    # band_rms — presence 频段 (5-8k) A-weighted 能量
    band_energy = spectrum.get("band_energy_db", {}) or {}
    band_rms = band_energy.get("presence", -30.0)
    if not isinstance(band_rms, (int, float)):
        band_rms = -30.0

    # crest — 波峰因数
    crest_db = 10.0
    if ctx.raw_rms_db is not None and ctx.raw_peak_db is not None:
        crest_db = ctx.raw_peak_db - ctx.raw_rms_db

    # ── 推导 ──
    thr = threshold_db(band_rms, crest_db)
    rng = range_db(genre)
    hpf_hz = detection_hpf(sib_peak, gender)
    lpf_hz = detection_lpf(sib_peak, gender)

    physical = {
        "Mode":                _MODE_WIDEBAND,
        "Band Processing":     _BAND_PROCESSING,
        "Threshold":           thr,
        "Range":               rng,
        "Lookahead":           _LOOKAHEAD_MS,
        "Lookahead Enabled":   _LOOKAHEAD_ENABLED,
        "High-Pass Frequency": _normalize_log_freq(hpf_hz),
        "Low-Pass Frequency":  _normalize_log_freq(lpf_hz),
        "Input Level":         _INPUT_LEVEL_DB,
        "Output Level":        _OUTPUT_LEVEL_DB,
        "Wet":                 _WET,
    }

    log.info(
        "Pro-DS: band_rms=%.1f crest=%.1f → threshold=%.1f dB, range=%.1f dB "
        "sib_peak=%.0f Hz → HPF=%.0f LPF=%.0f (genre=%s gender=%s)",
        band_rms, crest_db, thr, rng, sib_peak, hpf_hz, lpf_hz, genre, gender or "?",
    )
    return physical
