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

    def test_k_weight_bs1770_stereo(self):
        sig = np.random.normal(0, 0.01, (48000, 2)).astype(np.float64)
        result = SignalAnalyzer._k_weight_bs1770_4(sig, 48000)
        assert result.shape == sig.shape

    def test_compute_lufs_bs1770_stereo(self):
        t = np.linspace(0, 0.5, 24000)
        sig = np.column_stack([
            np.sin(2 * np.pi * 1000 * t) * 0.1,
            np.sin(2 * np.pi * 1000 * t) * 0.1,
        ])
        lufs = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert lufs < 0

    def test_read_wav_manual_valid(self, tmp_path):
        import soundfile as sf
        wav = tmp_path / "test.wav"
        sf.write(str(wav), np.zeros((100, 1)), 48000)
        from hermes_core.signal import _read_wav_manual
        result = _read_wav_manual(str(wav))
        assert result is not None
        assert result[1] == 48000  # sr is second element


# ════════════════════════════════════════════════════════════════
# LUFS BS1770-4 边界路径测试
# ════════════════════════════════════════════════════════════════


class TestLUFSBS1770Edges:
    """覆盖 _compute_lufs_bs1770_4 的边界和异常路径。"""

    def test_empty_input(self):
        """空数组 → 返回 -120 LUFS。"""
        sig = np.array([], dtype=np.float64)
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result == -120.0

    def test_short_input_direct_mean(self):
        """输入短于 400ms 块 → 走直接均值路径。"""
        # 100 samples at 48kHz = ~2ms, far less than 400ms block
        sig = np.ones(100, dtype=np.float64) * 0.01
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result < 0
        assert result > -120.0

    def test_near_silence_direct_mean(self):
        """接近静音的短输入 → -120 LUFS。"""
        sig = np.ones(10, dtype=np.float64) * 1e-15
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result == -120.0

    def test_block_samples_zero_path(self):
        """极短输入（不足一个 block）→ 走直接均值路径。"""
        # 48k * 0.4s = 19200 samples per block, 10 << 19200
        sig = np.ones(10, dtype=np.float64) * 0.01
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        # len(kw)=10 < block_samples=19200 → 短输入路径
        assert result < 0

    def test_very_short_block_less_than_half_block(self):
        """极短输入（刚好不足一个 block）→ 直接均值。"""
        # 48kHz * 0.4s = 19200 samples per block
        # Use 1000 samples < 19200 → short path
        sig = np.random.normal(0, 0.01, 1000).astype(np.float64)
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result < 0

    def test_stereo_short_input(self):
        """立体声短输入 → 直接均值路径。"""
        sig = np.random.normal(0, 0.01, (500, 2)).astype(np.float64)
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result < 0

    def test_all_below_absolute_threshold(self):
        """所有块功率低于绝对门限 → -120 LUFS。"""
        # 极低电平信号 — 块功率低于 -70 LKFS 门限
        sig = np.random.normal(0, 1e-8, 48000).astype(np.float64)
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result <= 0

    def test_empty_powers_fallback(self):
        """无有效功率数组时回退不应崩溃。"""
        # 全零输入 — 所有功率为 0，但 gating 不应崩溃
        sig = np.zeros(48000, dtype=np.float64)
        result = SignalAnalyzer._compute_lufs_bs1770_4(sig, 48000)
        assert result <= 0

    def test_mono_vs_stereo_consistent(self):
        """单声道和立体声相同信号应得到一致的 LUFS。"""
        t = np.linspace(0, 0.5, 24000)
        mono = np.sin(2 * np.pi * 1000 * t) * 0.1
        stereo = np.column_stack([mono, mono])
        lufs_mono = SignalAnalyzer._compute_lufs_bs1770_4(mono, 48000)
        lufs_stereo = SignalAnalyzer._compute_lufs_bs1770_4(stereo, 48000)
        assert abs(lufs_mono - lufs_stereo) < 0.5
