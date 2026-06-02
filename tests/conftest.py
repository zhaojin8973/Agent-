"""Shared fixtures and helpers for hermes-core tests."""

import logging
import shutil
import struct
import wave

import numpy as np
import pytest

from hermes_core.bridge import ReaperBridge


def require_reaper():
    """Connect to REAPER or skip the current test.

    Usage:
        require_reaper()
    """
    bridge = ReaperBridge()
    if not bridge.connect():
        pytest.skip("REAPER is not running -- skipping integration test")


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
