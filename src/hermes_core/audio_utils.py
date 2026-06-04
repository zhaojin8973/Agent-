"""
Layer 1.5: 音频工具函数 — 公共的音频数据处理工具。

提供跨模块共享的音频处理函数，避免代码重复。
"""

import atexit
import logging
import math
import os
import shutil
import threading
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)


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


# ════════════════════════════════════════════════════════════════
# 音符值 ↔ 毫秒转换
# ════════════════════════════════════════════════════════════════


def note_to_ms(note: str, bpm: float = 120.0) -> float:
    """将音乐音符值转换为毫秒。

    Parameters
    ----------
    note : str
        音符值，如 ``"1/4"``、``"1/8D"``（附点）、``"1/4T"``（三连音）。
        也接受纯数字字符串（如 ``"100.0"``），直接作为毫秒值返回。
    bpm : float
        每分钟拍数，默认 120。

    Returns
    -------
    float
        对应的毫秒值。

    Raises
    ------
    ValueError
        音符值格式无法识别。

    Examples
    --------
    >>> note_to_ms("1/4", 120)
    500.0
    >>> note_to_ms("1/8D", 120)  # 附点八分音符
    375.0
    >>> note_to_ms("100.0")
    100.0
    """
    stripped = note.strip()
    # 纯毫秒值
    try:
        return float(stripped)
    except ValueError:
        pass

    quarter_ms = 60000.0 / max(bpm, 1.0)
    mapping = {
        "1/1":   4.0,
        "1/2":   2.0,
        "1/4":   1.0,
        "1/8":   0.5,
        "1/16":  0.25,
        "1/32":  0.125,
        "1/8D":  0.5 * 1.5,       # 附点八分
        "1/4D":  1.0 * 1.5,       # 附点四分
        "1/16D": 0.25 * 1.5,      # 附点十六分
        "1/4T":  1.0 * 2.0 / 3.0, # 四分三连音
        "1/8T":  0.5 * 2.0 / 3.0, # 八分三连音
        "1/2T":  2.0 * 2.0 / 3.0, # 二分三连音
    }
    multiplier = mapping.get(stripped)
    if multiplier is None:
        raise ValueError(
            f"未知音符值 '{note}'。支持: {sorted(mapping.keys())} 或纯毫秒值"
        )
    return quarter_ms * multiplier


# ════════════════════════════════════════════════════════════════
# PCM 临时文件生命周期管理
# ════════════════════════════════════════════════════════════════

_pcm_temp_files: list[str] = []
_pcm_temp_lock: threading.Lock = threading.Lock()
_atexit_registered: bool = False


def _cleanup_pcm_temps() -> None:
    """清理所有已注册的 PCM 临时文件。

    在 atexit 或 finally 块中调用，确保进程退出时不留残留文件。
    """
    global _pcm_temp_files
    with _pcm_temp_lock:
        paths = list(_pcm_temp_files)
        _pcm_temp_files.clear()

    cleaned = 0
    for path in paths:
        try:
            if os.path.isfile(path):
                os.unlink(path)
                cleaned += 1
            elif os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
                cleaned += 1
        except OSError:
            pass

    if cleaned:
        log.debug("已清理 %d 个 PCM 临时文件", cleaned)


def _register_pcm_temp(path: str | Path) -> Path:
    """注册 PCM 临时文件路径，进程退出时自动清理。

    在创建 PCM 临时文件后立即调用此函数，确保 atexit 兜底清理。

    Parameters
    ----------
    path : str | Path
        临时文件路径。

    Returns
    -------
    Path
        注册后的绝对路径。
    """
    global _atexit_registered
    if not _atexit_registered:
        atexit.register(_cleanup_pcm_temps)
        _atexit_registered = True

    path_str = str(Path(path).expanduser().resolve())
    with _pcm_temp_lock:
        if path_str not in _pcm_temp_files:
            _pcm_temp_files.append(path_str)
            log.debug("注册 PCM 临时文件: %s", path_str)
    return Path(path_str)
