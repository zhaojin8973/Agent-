"""SignalAnalyzer 纯函数单元测试 — numpy 信号处理，无 REAPER 依赖。"""
import numpy as np
import pytest
from hermes_core.signal import SignalAnalyzer


class TestBlockPowerToLUFS:
    def test_sine_wave(self):
        result = SignalAnalyzer._block_power_to_lufs(0.5)
        assert result < 0

    def test_full_scale(self):
        result = SignalAnalyzer._block_power_to_lufs(1.0)
        assert abs(result) < 1.0  # ~0 dB LUFS

    def test_monotonic(self):
        a = SignalAnalyzer._block_power_to_lufs(0.1)
        b = SignalAnalyzer._block_power_to_lufs(0.5)
        assert b > a


class TestDesignFilters:
    def test_rlb_filter(self):
        b, a = SignalAnalyzer._design_rlb_filter(48000)
        assert len(b) == 3
        assert len(a) == 3

    def test_preemphasis_filter(self):
        b, a = SignalAnalyzer._design_preemphasis_filter(48000)
        assert len(b) >= 2
        assert len(a) >= 2

    def test_rlb_filter_44100(self):
        b, a = SignalAnalyzer._design_rlb_filter(44100)
        assert len(b) == 3


class TestKWeight:
    def test_stereo(self):
        sig = np.random.normal(0, 0.01, (48000, 2)).astype(np.float64)
        result = SignalAnalyzer._k_weight(sig, 48000)
        assert result.shape == sig.shape

    def test_mono(self):
        sig = np.random.normal(0, 0.01, 48000).astype(np.float64)
        result = SignalAnalyzer._k_weight(sig, 48000)
        assert result.ndim == 1


class TestTruePeak:
    def test_sine(self):
        t = np.linspace(0, 0.1, 4800)
        sig = np.sin(2 * np.pi * 1000 * t) * 0.5
        tp = SignalAnalyzer._compute_true_peak(sig)
        assert tp < 0  # dB 值，应为负数

    def test_stereo(self):
        t = np.linspace(0, 0.1, 4800)
        sig = np.column_stack([
            np.sin(2 * np.pi * 1000 * t) * 0.5,
            np.sin(2 * np.pi * 500 * t) * 0.3,
        ])
        tp = SignalAnalyzer._compute_true_peak(sig)
        assert tp < 0


class TestLUFSBS1770:
    def test_sine_basic(self):
        t = np.linspace(0, 0.5, 24000)
        sig = np.sin(2 * np.pi * 1000 * t) * 0.1
        lufs = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert lufs < -20  # 很安静的正弦波

    def test_loudness_timeseries(self):
        sig = np.random.normal(0, 0.01, 48000).astype(np.float64)
        ts = SignalAnalyzer._loudness_timeseries(sig, 48000)
        assert ts.ndim == 1
        assert len(ts) >= 1

    def test_compute_lufs_legacy(self):
        sig = np.random.normal(0, 0.001, 48000).astype(np.float64)
        lufs = SignalAnalyzer._compute_lufs(sig, 48000)
        assert lufs < 0
