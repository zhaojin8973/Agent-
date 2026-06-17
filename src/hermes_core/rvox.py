"""
Waves RVox Mono — 单推子动态处理器。

RVox 将 Threshold + Ratio + Makeup 合并为一个 Compression 推子。
内置输出上限 0 dBFS，配合 Gain 推子做电平补偿。

Vocal A 链角色：CLA-76 抓峰值后，RVox 做身体/RMS 一致性压缩。
"""

import logging

log = logging.getLogger(__name__)

# ── 流派 multiplier — CLA-76 GR × multiplier = RVox Compression ──
_MULTIPLIER: dict[str, float] = {
    "electronic":              1.8,
    "pop":                     1.7,
    "rock":                    1.7,
    "chinese_folk_bel_canto":  1.5,
    "folk":                    1.0,
    "ballad":                  1.2,
}
_DEFAULT_MULTIPLIER = 1.5

# ── 物理范围 ──
_COMP_MIN_DB = -36.0  # 最激进的压缩（归一化 1.0）
_COMP_MAX_DB = 0.0    # 无压缩（归一化 0.0）
_GATE_OFF_DB = -120.0  # Gate 关闭（≈ -Inf）
_GAIN_MIN_DB = -36.0
_GAIN_MAX_DB = 0.0


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """RVox 物理值 → 归一化 (0-1)。

    Compression: -36dB → 0.0, 0dB → 1.0
    Gate: -120dB → 0.0, 0dB → 1.0
    Gain: -36dB → 0.0, 0dB → 1.0
    """
    result = {}
    if "Compression" in physical:
        result["Compression"] = max(0.0, min(1.0,
            (physical["Compression"] - _COMP_MIN_DB) / abs(_COMP_MIN_DB)))
    if "Gate" in physical:
        result["Gate"] = max(0.0, min(1.0,
            (physical["Gate"] - _GATE_OFF_DB) / abs(_GATE_OFF_DB)))
    if "Gain" in physical:
        result["Gain"] = max(0.0, min(1.0,
            (physical["Gain"] - _GAIN_MIN_DB) / abs(_GAIN_MIN_DB)))
    return result


def build_params(ctx, *, gr_target_db: float = 0.0) -> dict:
    """从 FXBuildContext 推导 RVox 物理参数。

    Compression = gr_target_db × multiplier（流派驱动）
    Gain = Compression × 0.6（A/B bypass 验证的电平匹配系数）
    Gate = -120 dB（始终关闭，信号分析暂不支持自动噪声门）

    Parameters
    ----------
    ctx : FXBuildContext
    gr_target_db : float
        CLA-76 的目标增益衰减量 (dB)。

    Returns
    -------
    dict
        {"Compression", "Gate", "Gain"} 物理 dB 值。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"
    multiplier = _MULTIPLIER.get(genre, _DEFAULT_MULTIPLIER)

    compression_db = -gr_target_db * multiplier
    compression_db = max(_COMP_MIN_DB, min(_COMP_MAX_DB, compression_db))
    compression_db = round(compression_db, 1)

    gain_db = round(compression_db * 0.6, 1)
    gain_db = max(_GAIN_MIN_DB, min(_GAIN_MAX_DB, gain_db))

    gate_db = _GATE_OFF_DB

    log.info(
        "Auto-RVox: comp=%.1fdB gain=%.1fdB gate=off "
        "(GR=%.1fdB x%.1f genre=%s)",
        compression_db, gain_db, gr_target_db, multiplier, genre,
    )

    return {
        "Compression": compression_db,
        "Gate":        gate_db,
        "Gain":        gain_db,
    }
