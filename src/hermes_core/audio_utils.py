"""
Layer 1.5: 音频工具函数 — 公共的音频数据处理工具。

提供跨模块共享的音频处理函数，避免代码重复。
"""

import math
import numpy as np
import soundfile as sf


# ════════════════════════════════════════════════════════════════
# dB ↔ 归一化转换
# ════════════════════════════════════════════════════════════════


def db_to_norm(db: float) -> float:
    """将 dB 值转换为 REAPER 归一化值 (0.0..1.0)。

    Parameters
    ----------
    db : float
        分贝值。-150 dB 或更低被视为静音。

    Returns
    -------
    float
        归一化值，范围 [0.0, 1.0]。

    Notes
    -----
    - -150 dB 及以下被视为静音，返回 0.0
    - 0 dB 返回 1.0
    - 公式：10^(dB/20)
    """
    if not math.isfinite(db) or db <= -150.0:
        return 0.0
    return 10.0 ** (db / 20.0)


def norm_to_db(norm: float) -> float:
    """将 REAPER 归一化值 (0.0..1.0) 转换为 dB 值。

    Parameters
    ----------
    norm : float
        归一化值，范围 [0.0, 1.0]。

    Returns
    -------
    float
        分贝值。0.0 或更小的归一化值返回 -150 dB。

    Notes
    -----
    - 0.0 返回 -150 dB（静音）
    - 1.0 返回 0 dB
    - 公式：20 * log10(norm)
    """
    if norm <= 0.0:
        return -150.0
    return 20.0 * math.log10(norm)


# ════════════════════════════════════════════════════════════════
# WAV 文件读取
# ════════════════════════════════════════════════════════════════


def read_pcm(file_path: str) -> tuple[np.ndarray, int]:
    """读取 WAV 文件并返回 PCM 数据和采样率。

    Parameters
    ----------
    file_path : str
        WAV 文件路径。

    Returns
    -------
    tuple[np.ndarray, int]
        - PCM 数据，形状为 (n_samples, n_channels)，float64 类型
        - 采样率 (Hz)

    Notes
    -----
    - 单声道文件会被重塑为 2-D 数组，以便下游处理保持一致
    - 使用 soundfile 库读取，支持多种音频格式
    """
    data, sr = sf.read(file_path, dtype="float64")
    if data.ndim == 1:
        data = data.reshape(-1, 1)
    return data, sr


def to_mono(pcm: np.ndarray) -> np.ndarray:
    """将多通道音频下混为单声道。

    Parameters
    ----------
    pcm : np.ndarray
        PCM 数据，可以是 1-D (单声道) 或 2-D (多通道) 数组。

    Returns
    -------
    np.ndarray
        单声道 PCM 数据，1-D 数组，float64 类型。

    Notes
    -----
    - 单声道输入直接返回（如果是 1-D）或提取第一列（如果是 2-D）
    - 多通道输入使用通道平均值：mean(L, R, ...)
    - 输出始终为 float64 类型
    """
    if pcm.ndim == 1:
        return pcm.astype(np.float64)
    if pcm.shape[1] == 1:
        return pcm[:, 0].astype(np.float64)
    return np.mean(pcm, axis=1)
