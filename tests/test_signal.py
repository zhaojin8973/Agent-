"""Tests for hermes_core.signal — pure-numpy audio analysis module.

These tests run without REAPER. They generate synthetic WAV files using
stdlib wave + struct modules and verify SignalAnalyzer behavior.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_signal.py -v
"""

import os
import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from hermes_core.signal import SignalAnalyzer, SignalReport


# ══════════════════════════════════════════════════════════════
# Test helpers
# ══════════════════════════════════════════════════════════════

def _sine(freq, duration_sec, sample_rate, amplitude=1.0):
    """Generate a mono sine wave.

    Args:
        freq: Frequency in Hz.
        duration_sec: Duration in seconds.
        sample_rate: Sample rate in Hz.
        amplitude: Peak amplitude in [0, 1].

    Returns:
        (num_samples,) float64 ndarray.
    """
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def _make_stereo(mono):
    """Duplicate a mono signal to both channels, producing (N, 2)."""
    return np.column_stack([mono, mono])


def _gen_16bit_stereo_wav(filepath, samples, sample_rate=48000):
    """Write a 16-bit stereo WAV from float64 samples in [-1, 1]."""
    i16 = np.clip(samples * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)  # 16-bit = 2 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())


def _gen_24bit_stereo_wav(filepath, samples, sample_rate=48000):
    """Write a 24-bit stereo WAV from float64 samples in [-1, 1].

    24-bit PCM is stored as 3-byte little-endian signed integers.
    Each sample is packed using two's complement representation.
    """
    scaled = np.clip(samples * 8388607.0, -8388608, 8388607).astype(np.int32)
    flat = scaled.flatten()
    chunks = []
    for value in flat:
        v = int(value)
        if v < 0:
            v += 1 << 24  # Convert to unsigned 24-bit representation
        chunks.append(struct.pack("<I", v)[:3])
    packed = b"".join(chunks)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(3)  # 24-bit = 3 bytes
        wf.setframerate(sample_rate)
        wf.writeframes(packed)


def _gen_32bit_float_wav(filepath, samples, sample_rate=48000):
    """Write a 32-bit IEEE float stereo WAV via soundfile."""
    import soundfile as sf
    sf.write(str(filepath), samples, sample_rate, subtype="FLOAT")


# ══════════════════════════════════════════════════════════════
# Tests: _read_pcm
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestReadPcm:
    """Tests for SignalAnalyzer._read_pcm() — PCM decoding."""

    def test_read_pcm_16bit(self, tmp_path):
        """_read_pcm correctly decodes a 16-bit stereo WAV to mono float64."""
        # Arrange
        sr = 44100
        duration = 0.1
        mono_sine = _sine(440.0, duration, sr, amplitude=0.5)
        stereo = _make_stereo(mono_sine)
        wav_path = tmp_path / "test_16bit.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

        # Assert
        assert out_sr == sr
        assert pcm.ndim == 2, f"expected multi-channel (2D) output, got shape {pcm.shape}"
        assert pcm.shape[1] == 2
        assert pcm.shape[0] == len(mono_sine)
        assert pcm.dtype == np.float64
        # Values should be close to original (allow rounding from int16)
        mono_pcm = SignalAnalyzer._to_mono(pcm)
        assert np.allclose(mono_pcm, mono_sine, atol=1.0 / 32767.0 * 2)

    def test_read_pcm_24bit(self, tmp_path):
        """_read_pcm correctly decodes a 24-bit stereo WAV to mono float64."""
        # Arrange
        sr = 48000
        duration = 0.05
        mono_sine = _sine(1000.0, duration, sr, amplitude=0.3)
        stereo = _make_stereo(mono_sine)
        wav_path = tmp_path / "test_24bit.wav"
        _gen_24bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

        # Assert
        assert out_sr == sr
        assert pcm.ndim == 2, f"expected multi-channel (2D) output, got shape {pcm.shape}"
        assert pcm.shape[0] == len(mono_sine)
        assert pcm.dtype == np.float64
        # 24-bit precision (144 dB dynamic range) — very tight tolerance
        mono_pcm = SignalAnalyzer._to_mono(pcm)
        assert np.allclose(mono_pcm, mono_sine, atol=1.0 / 8388607.0 * 2)

    def test_read_pcm_stereo_to_mono(self, tmp_path):
        """_to_mono downmixes stereo to mono via standard (L+R)/2."""
        # Arrange
        sr = 48000
        duration = 0.02
        left = _sine(440.0, duration, sr, amplitude=0.9)
        right = _sine(440.0, duration, sr, amplitude=0.1)
        stereo = np.column_stack([left, right])
        expected_mono = (left + right) / 2.0
        wav_path = tmp_path / "test_stereo_to_mono.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))
        mono_pcm = SignalAnalyzer._to_mono(pcm)

        # Assert
        assert out_sr == sr
        assert pcm.ndim == 2
        assert len(mono_pcm) == len(expected_mono)
        assert np.allclose(mono_pcm, expected_mono, atol=1.0 / 32767.0 * 2)

    def test_read_pcm_shape_and_rate(self, tmp_path):
        """_read_pcm returns correct multi-channel shape and sample rate."""
        # Arrange
        for dur in [0.01, 0.1, 1.0]:
            sr = 48000
            n = int(sr * dur)
            mono = _sine(500.0, dur, sr, amplitude=0.8)
            stereo = _make_stereo(mono)
            wav_path = tmp_path / f"test_shape_{dur}s.wav"
            _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

            # Act
            pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

            # Assert
            assert out_sr == sr, f"sample rate mismatch for dur={dur}"
            assert pcm.ndim == 2, f"expected 2D for dur={dur}, got shape {pcm.shape}"
            assert abs(len(pcm) - n) <= 1, f"length mismatch for dur={dur}"

    def test_read_pcm_24bit_negative_samples(self, tmp_path):
        """_read_pcm handles negative 24-bit samples with correct sign extension."""
        # Arrange
        sr = 48000
        # Use a square-like wave with explicit negative values to test sign extension
        n = 200
        mono = np.zeros(n, dtype=np.float64)
        mono[0:50] = -0.8
        mono[50:100] = -0.3
        mono[100:150] = 0.3
        mono[150:200] = 0.8
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_24bit_neg.wav"
        _gen_24bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

        # Assert
        assert out_sr == sr
        assert pcm.ndim == 2
        assert pcm.shape[0] == n
        assert pcm.dtype == np.float64
        # All negative samples should remain negative after decode
        ch0_neg = pcm[:50, 0]
        ch0_pos = pcm[100:150, 0]
        assert np.all(ch0_neg < 0), "negative samples corrupted"
        assert np.all(ch0_pos > 0), "positive samples corrupted"
        mono_pcm = SignalAnalyzer._to_mono(pcm)
        assert np.allclose(mono_pcm, mono, atol=1.0 / 8388607.0 * 2)

    def test_read_pcm_32bit_float(self, tmp_path):
        """_read_pcm correctly decodes a 32-bit float stereo WAV to mono float64."""
        sr = 44100
        duration = 0.1
        mono_sine = _sine(440.0, duration, sr, amplitude=0.7)
        stereo = _make_stereo(mono_sine)
        wav_path = tmp_path / "test_32bit_float.wav"
        _gen_32bit_float_wav(wav_path, stereo, sample_rate=sr)

        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

        assert out_sr == sr
        assert pcm.ndim == 2
        assert pcm.shape[0] == len(mono_sine)
        assert pcm.dtype == np.float64
        mono_pcm = SignalAnalyzer._to_mono(pcm)
        assert np.allclose(mono_pcm, mono_sine, atol=1e-6)

    def test_read_pcm_32bit_float_silence(self, tmp_path):
        """_read_pcm handles 32-bit float WAV containing near-silence."""
        sr = 48000
        n = 100
        mono = np.full(n, 1e-10, dtype=np.float64)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_32bit_silence.wav"
        _gen_32bit_float_wav(wav_path, stereo, sample_rate=sr)

        pcm, out_sr = SignalAnalyzer._read_pcm(str(wav_path))

        assert out_sr == sr
        assert pcm.shape[0] == n
        mono_pcm = SignalAnalyzer._to_mono(pcm)
        assert np.allclose(mono_pcm, mono, atol=1e-8)


# ══════════════════════════════════════════════════════════════
# Tests: analyze()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestAnalyze:
    """Tests for SignalAnalyzer.analyze() — full signal analysis."""

    def test_analyze_sine_1khz(self, tmp_path):
        """analyze() returns correct metrics for a 1 kHz sine at -6 dBFS peak."""
        # Arrange
        sr = 48000
        duration = 0.5
        # -6 dBFS peak amplitude: 10^(-6/20) ~ 0.5012
        amplitude = 10.0 ** (-6.0 / 20.0)
        mono = _sine(1000.0, duration, sr, amplitude=amplitude)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_sine_1khz.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert — identity and types
        assert isinstance(report, SignalReport)

        # Peak should be close to -6 dBFS (within 0.5 dB)
        expected_peak = 20.0 * np.log10(amplitude)
        assert abs(report.peak_db - expected_peak) < 0.5, (
            f"peak_db={report.peak_db:.3f}, expected ~{expected_peak:.3f}"
        )

        # RMS: for sine waves, RMS is typically 3 dB below peak (standard)
        # or equal to peak under AES-17 convention. Accept either.
        assert -10.0 < report.rms_db < 0.0, (
            f"rms_db={report.rms_db:.3f} out of expected range [-10, 0]"
        )

        # LUFS should be a finite float in a reasonable range
        assert report.integrated_lufs is not None
        assert np.isfinite(report.integrated_lufs), (
            f"integrated_lufs is not finite: {report.integrated_lufs}"
        )
        assert -100.0 < report.integrated_lufs < 0.0, (
            f"integrated_lufs={report.integrated_lufs:.3f} out of range [-100, 0]"
        )

        # True peak should be valid
        assert report.true_peak_dbtp is not None
        assert np.isfinite(report.true_peak_dbtp), (
            f"true_peak_dbtp is not finite: {report.true_peak_dbtp}"
        )
        # True peak for a sine at -6 dBFS should be near -6 (within a few dB)
        assert -10.0 < report.true_peak_dbtp < 0.0, (
            f"true_peak_dbtp={report.true_peak_dbtp:.3f} out of range [-10, 0]"
        )

        # No clipping in a clean sine
        assert report.clip_count == 0, f"unexpected clip_count={report.clip_count}"
        assert report.clip_passed is True

        # Not silent
        assert report.silence_passed is True

        # Duration within tolerance
        assert report.duration_sec == pytest.approx(duration, abs=0.02)

        # Sample rate matches
        assert report.sample_rate == sr

    def test_analyze_silence(self, tmp_path):
        """analyze() reports silence_passed=False for near-silent audio."""
        # Arrange
        sr = 48000
        duration = 0.2
        n = int(sr * duration)
        mono = np.full(n, 1e-7, dtype=np.float64)  # ~-140 dBFS, essentially silent
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_silence.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.silence_passed is False, (
            f"silence_passed should be False for silent audio, "
            f"got rms_db={report.rms_db:.3f}"
        )

    def test_analyze_clipped(self, tmp_path):
        """analyze() detects clipping when samples reach full scale."""
        # Arrange
        sr = 48000
        duration = 0.1
        n = int(sr * duration)
        # Generate a sine at 2x amplitude — will hard-clip at int16 boundaries
        mono = _sine(500.0, duration, sr, amplitude=2.0)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_clipped.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.clip_count > 0, (
            f"Expected clip_count > 0 for hard-clipped signal, got {report.clip_count}"
        )
        assert report.clip_passed is False

    def test_analyze_16bit(self, tmp_path):
        """analyze() handles 16-bit PCM WAV files correctly."""
        # Arrange
        sr = 44100
        duration = 0.15
        mono = _sine(500.0, duration, sr, amplitude=0.4)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_16bit_analyze.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.sample_rate == sr
        assert report.duration_sec == pytest.approx(duration, abs=0.02)
        assert report.clip_count == 0
        assert report.silence_passed is True
        assert np.isfinite(report.rms_db)
        assert np.isfinite(report.peak_db)

    def test_analyze_24bit(self, tmp_path):
        """analyze() handles 24-bit PCM WAV files correctly."""
        # Arrange
        sr = 48000
        duration = 0.15
        mono = _sine(800.0, duration, sr, amplitude=0.6)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_24bit_analyze.wav"
        _gen_24bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.sample_rate == sr
        assert report.duration_sec == pytest.approx(duration, abs=0.02)
        assert report.clip_count == 0
        assert report.silence_passed is True
        assert np.isfinite(report.rms_db)
        assert np.isfinite(report.peak_db)

    def test_analyze_nonexistent_file(self):
        """analyze() raises a clear error for a nonexistent file path."""
        # Arrange
        nonexistent = "/tmp/hermes_core_test_nonexistent_xyz789.wav"
        # Ensure the file genuinely does not exist
        assert not os.path.exists(nonexistent)

        # Act / Assert
        with pytest.raises((FileNotFoundError, OSError, RuntimeError)):
            SignalAnalyzer.analyze(nonexistent)

    def test_analyze_short_file(self, tmp_path):
        """analyze() handles very short audio files (< 50 ms) without error."""
        # Arrange
        sr = 48000
        duration = 0.005  # 5 ms — very short
        mono = _sine(1000.0, duration, sr, amplitude=0.5)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_short.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.duration_sec > 0
        assert np.isfinite(report.rms_db)
        assert np.isfinite(report.peak_db)


# ══════════════════════════════════════════════════════════════
# Tests: SignalReport
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSignalReport:
    """Tests for the SignalReport dataclass structure."""

    def test_signal_report_fields(self, tmp_path):
        """SignalReport has all required fields with correct types."""
        # Arrange — generate a real report from a minimal WAV
        sr = 48000
        mono = _sine(1000.0, 0.1, sr, amplitude=0.5)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_fields.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert — all required fields are present
        required_fields = [
            "rms_db", "peak_db", "integrated_lufs", "true_peak_dbtp",
            "clip_count", "clip_passed", "silence_passed",
            "duration_sec", "sample_rate",
        ]
        for field_name in required_fields:
            assert hasattr(report, field_name), (
                f"SignalReport missing field: {field_name}"
            )

        # Assert — correct types
        assert isinstance(report.rms_db, float), f"rms_db type: {type(report.rms_db)}"
        assert isinstance(report.peak_db, float)
        assert isinstance(report.integrated_lufs, float), (
            f"integrated_lufs type: {type(report.integrated_lufs)}"
        )
        assert isinstance(report.true_peak_dbtp, float)
        assert isinstance(report.clip_count, int)
        assert isinstance(report.clip_passed, bool)
        assert isinstance(report.silence_passed, bool)
        assert isinstance(report.duration_sec, float)
        assert isinstance(report.sample_rate, int)

        # Assert — reasonable value ranges
        assert report.clip_count >= 0
        assert report.duration_sec > 0
        assert report.sample_rate > 0

    def test_signal_report_direct_construction(self):
        """SignalReport can be constructed directly from field values."""
        # Arrange & Act
        report = SignalReport(
            rms_db=-12.0,
            peak_db=-6.0,
            integrated_lufs=-14.0,
            true_peak_dbtp=-5.5,
            clip_count=0,
            clip_passed=True,
            silence_passed=True,
            duration_sec=1.0,
            sample_rate=48000,
        )

        # Assert — all fields store the values correctly
        assert report.rms_db == -12.0
        assert report.peak_db == -6.0
        assert report.integrated_lufs == -14.0
        assert report.true_peak_dbtp == -5.5
        assert report.clip_count == 0
        assert report.clip_passed is True
        assert report.silence_passed is True
        assert report.duration_sec == 1.0
        assert report.sample_rate == 48000

    def test_signal_report_clipped_state(self):
        """SignalReport.clip_passed reflects clipping state correctly."""
        # Arrange & Act
        clean = SignalReport(
            rms_db=-12.0, peak_db=-6.0, integrated_lufs=-14.0,
            true_peak_dbtp=-5.5, clip_count=0, clip_passed=True,
            silence_passed=True, duration_sec=1.0, sample_rate=48000,
        )
        clipped = SignalReport(
            rms_db=-6.0, peak_db=-0.1, integrated_lufs=-8.0,
            true_peak_dbtp=0.2, clip_count=5, clip_passed=False,
            silence_passed=True, duration_sec=1.0, sample_rate=48000,
        )

        # Assert
        assert clean.clip_count == 0 and clean.clip_passed is True
        assert clipped.clip_count > 0 and clipped.clip_passed is False


# ══════════════════════════════════════════════════════════════
# Tests: edge cases
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestEdgeCases:
    """Edge case and boundary tests for signal analysis."""

    def test_analyze_dc_offset(self, tmp_path):
        """analyze() handles a DC offset signal gracefully."""
        # Arrange
        sr = 48000
        duration = 0.1
        n = int(sr * duration)
        mono = np.full(n, 0.5, dtype=np.float64)  # pure DC at -6 dBFS
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_dc.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert — should not crash, produce valid numbers
        assert isinstance(report, SignalReport)
        assert np.isfinite(report.rms_db)
        assert np.isfinite(report.peak_db)
        assert report.duration_sec == pytest.approx(duration, abs=0.02)

    def test_analyze_inverted_phase(self, tmp_path):
        """analyze() gives identical RMS/peak for a signal and its inverse."""
        # Arrange
        sr = 48000
        duration = 0.1
        mono = _sine(800.0, duration, sr, amplitude=0.6)
        stereo_normal = _make_stereo(mono)
        stereo_inverted = _make_stereo(-mono)
        wav_normal = tmp_path / "test_normal.wav"
        wav_inverted = tmp_path / "test_inverted.wav"
        _gen_16bit_stereo_wav(wav_normal, stereo_normal, sample_rate=sr)
        _gen_16bit_stereo_wav(wav_inverted, stereo_inverted, sample_rate=sr)

        # Act
        r_normal = SignalAnalyzer.analyze(str(wav_normal))
        r_inverted = SignalAnalyzer.analyze(str(wav_inverted))

        # Assert — RMS and peak should be identical (magnitude only)
        assert r_normal.rms_db == pytest.approx(r_inverted.rms_db, abs=0.01)
        assert r_normal.peak_db == pytest.approx(r_inverted.peak_db, abs=0.01)
        assert r_normal.clip_count == r_inverted.clip_count

    def test_analyze_large_file(self, tmp_path):
        """analyze() handles a moderately large file (10 seconds) without error."""
        # Arrange
        sr = 44100
        duration = 10.0
        mono = _sine(440.0, duration, sr, amplitude=0.3)
        # Add some amplitude variation to make it realistic
        envelope = np.linspace(0.3, 0.9, len(mono))
        mono = mono * envelope
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_large.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert isinstance(report, SignalReport)
        assert report.duration_sec == pytest.approx(duration, abs=0.05)
        assert report.sample_rate == sr
        assert np.isfinite(report.rms_db)
        assert np.isfinite(report.peak_db)
        assert np.isfinite(report.integrated_lufs)

    def test_analyze_mid_level(self, tmp_path):
        """analyze() places signal between silence and clipping thresholds."""
        # Arrange
        sr = 48000
        duration = 0.3
        # -30 dBFS peak — well above silence, well below clipping
        amplitude = 10.0 ** (-30.0 / 20.0)
        mono = _sine(2000.0, duration, sr, amplitude=amplitude)
        stereo = _make_stereo(mono)
        wav_path = tmp_path / "test_midlevel.wav"
        _gen_16bit_stereo_wav(wav_path, stereo, sample_rate=sr)

        # Act
        report = SignalAnalyzer.analyze(str(wav_path))

        # Assert
        assert report.silence_passed is True, (
            f"Expected not silent for -30 dBFS signal, got rms_db={report.rms_db:.2f}"
        )
        assert report.clip_passed is True, (
            f"Expected no clipping for -30 dBFS signal, got clip_count={report.clip_count}"
        )
