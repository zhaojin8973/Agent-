"""Shared fixtures and helpers for hermes-core tests."""

import logging
import os
import shutil
import struct
import wave

import numpy as np
import pytest

from hermes_core.bridge import ReaperBridge


def require_reaper():
    """Connect to REAPER or skip the current test."""
    bridge = ReaperBridge()
    if not bridge.connect():
        pytest.skip("REAPER is not running -- skipping integration test")


# ════════════════════════════════════════════════════════════════
# 测试信号生成
# ════════════════════════════════════════════════════════════════


def make_test_wav(filepath, duration_sec=1.0, sample_rate=48000, frequency=440.0):
    """Generate a mono 16-bit WAV file with a sine wave. Returns filepath."""
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    signal = 0.5 * np.sin(2.0 * np.pi * frequency * t)
    i16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())
    return filepath


def make_test_signal(filepath, duration_sec=3.0, sample_rate=48000,
                     base_freq=330.0, level_db=-6.0):
    """生成带波峰因数的类音乐信号 — 多层叠加 + 包络。

    与纯正弦波不同，此信号模拟真实人声的频谱复杂度和动态特性：
    - 基频 + 2次谐波 + 3次谐波（模拟人声共振峰）
    - 粉红噪声底噪（模拟气息声）
    - ADSR 包络（模拟人声自然衰减）
    - 合理波峰因数 ~10-14 dB（正弦波仅 ~3 dB）

    Returns filepath (立体声 24-bit WAV).
    """
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    rng = np.random.default_rng(42)

    # 基频 + 谐波（人声共振峰模拟）
    fundamental = 0.6 * np.sin(2.0 * np.pi * base_freq * t)
    harmonic_2  = 0.25 * np.sin(2.0 * np.pi * base_freq * 2 * t)
    harmonic_3  = 0.10 * np.sin(2.0 * np.pi * base_freq * 3 * t)

    # 低频嗡声（模拟胸腔共振）
    body = 0.08 * np.sin(2.0 * np.pi * (base_freq * 0.5) * t)

    # 气息噪声底噪
    breath = 0.04 * rng.normal(0, 1, n)

    signal = fundamental + harmonic_2 + harmonic_3 + body + breath

    # ADSR 包络（人声自然衰减）
    attack  = min(0.05 * duration_sec, 0.1)
    release = min(0.2 * duration_sec, 0.5)
    env = np.ones(n)
    env[:int(attack * sample_rate)] = np.linspace(0, 1, int(attack * sample_rate))
    env[-int(release * sample_rate):] = np.linspace(1, 0, int(release * sample_rate))
    signal *= env

    # 归一化到目标电平
    peak = np.max(np.abs(signal)) + 1e-12
    gain_linear = 10.0 ** (level_db / 20.0)
    signal = signal / peak * gain_linear

    # 立体声 — 左右略有差异模拟真实录制
    left = signal
    right = signal * 0.92 + 0.02 * rng.normal(0, 1, n)
    stereo = np.column_stack([left, right])

    # 24-bit 写入
    i24 = (stereo * 8388607.0).astype(np.int32)
    raw = i24.tobytes()
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(3)
        wf.setframerate(sample_rate)
        wf.writeframesraw(raw)
    return filepath


# ════════════════════════════════════════════════════════════════
# 音频质量断言辅助
# ════════════════════════════════════════════════════════════════


def assert_wav_valid(filepath, min_duration_sec=0.5, expect_stereo=True):
    """断言文件是有效的 WAV，并验证基本结构。"""
    import soundfile as sf
    assert os.path.exists(filepath), f"WAV 文件不存在: {filepath}"
    assert os.path.getsize(filepath) > 1000, f"WAV 文件太小: {os.path.getsize(filepath)} bytes"

    info = sf.info(filepath)
    assert info.duration >= min_duration_sec, \
        f"持续时间 {info.duration:.2f}s < {min_duration_sec}s"
    if expect_stereo:
        assert info.channels == 2, f"期望立体声，实际 {info.channels} 声道"
    return info


def assert_lufs_near(filepath, expected_lufs, tolerance=0.8):
    """断言 WAV 文件的集成 LUFS 在目标范围内。"""
    import pyloudnorm as pyln
    import soundfile as sf
    data, sr = sf.read(filepath)
    meter = pyln.Meter(sr)
    lufs = meter.integrated_loudness(data)
    assert abs(lufs - expected_lufs) <= tolerance, \
        f"LUFS {lufs:.1f} 超出范围 [{expected_lufs - tolerance:.1f}, {expected_lufs + tolerance:.1f}]"
    return lufs


def assert_true_peak_under(filepath, ceiling_db=-0.5):
    """断言 WAV 文件的真峰值不超过上限。"""
    import soundfile as sf
    data, sr = sf.read(filepath)
    try:
        from scipy import signal
        upsampled = signal.resample_poly(data, 4, 1, axis=0)
    except ImportError:
        upsampled = data
    tp_linear = np.max(np.abs(upsampled)) + 1e-12
    tp_db = 20.0 * np.log10(tp_linear)
    assert tp_db <= ceiling_db, \
        f"真峰值 {tp_db:.2f} dBTP 超过上限 {ceiling_db} dBTP"
    return tp_db


def assert_no_clipping(filepath):
    """断言 WAV 文件无削波（无连续满刻度样本）。"""
    import soundfile as sf
    data, sr = sf.read(filepath)
    clips = np.sum(np.abs(data) >= 0.999)
    assert clips == 0, f"检测到 {clips} 个削波样本"


# ════════════════════════════════════════════════════════════════
# 旧辅助函数（保留向后兼容）
# ════════════════════════════════════════════════════════════════


def clean_project(bridge):
    """Delete all tracks from the current REAPER project."""
    api = bridge.api
    n = api.CountTracks(0)
    for i in range(n - 1, -1, -1):
        tr = api.GetTrack(0, i)
        if tr:
            api.DeleteTrack(tr)
    return api.CountTracks(0)


# ════════════════════════════════════════════════════════════════
# 集成测试自动清理 fixture
# ════════════════════════════════════════════════════════════════

log = logging.getLogger(__name__)

# REAPER 关闭工程不保存的命令 ID
_REAPER_CLOSE_PROJECT_NO_SAVE = 40859

# 集成测试可能创建的临时目录
_INTEGRATION_TMP_DIRS = [
    "/tmp/hermes_test",
    "/tmp/hermes_vocal_test",
    "/tmp/hermes_balance_test",
]


@pytest.fixture(autouse=True)
def _cleanup_after_integration_test(request):
    """集成测试后自动关闭 REAPER 工程并清理临时文件。

    仅对标记了 @pytest.mark.integration 的测试生效。
    使用 yield 确保测试失败时仍执行清理。
    """
    yield  # 先执行测试

    # 仅清理集成测试
    if not request.node.get_closest_marker("integration"):
        return

    # 清理 REAPER 工程（删除所有轨道 + 关闭工程 tab）
    try:
        bridge = ReaperBridge()
        if bridge.connect():
            api = bridge.api
            with bridge:  # 上下文管理器锁/解锁 UI
                # 反向删除所有轨道
                n = api.CountTracks(0)
                for i in range(n - 1, -1, -1):
                    tr = api.GetTrack(0, i)
                    if tr:
                        try:
                            api.DeleteTrack(tr)
                        except Exception:
                            pass
                # 关闭当前工程（不保存）
                api.Main_OnCommand(_REAPER_CLOSE_PROJECT_NO_SAVE, 0)
    except Exception as e:
        log.debug("Integration test REAPER cleanup failed: %s", e)

    # 清理临时目录
    for tmpdir in _INTEGRATION_TMP_DIRS:
        try:
            shutil.rmtree(tmpdir, ignore_errors=True)
        except Exception:
            pass
