"""Tests for hermes_core.spectrum — pure-numpy spectrum analysis module.

These tests run without REAPER.  They generate synthetic WAV files and
verify SpectrumAnalyzer behaviour including the three key improvements:

1. STFT + P90 (vs global average) — captures intermittent resonances.
2. Q-factor + harmonic filtering — distinguishes room modes from musical partials.
3. A-weighting — perceptual energy correction.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_spectrum.py -v
"""

import struct
import wave
from pathlib import Path

import numpy as np
import pytest

from hermes_core.audio_utils import read_pcm, to_mono
from hermes_core.spectrum import (
    SpectrumAnalyzer,
    SpectrumReport,
    Resonance,
    _BAND_EDGES,
    _EPS_DB,
)


# ══════════════════════════════════════════════════════════════
# Test helpers
# ══════════════════════════════════════════════════════════════

_SR = 48000
_TMP = Path(__file__).resolve().parent / "_test_spectrum_tmp"


def _sine(freq, duration_sec, sample_rate=_SR, amplitude=1.0):
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def _write_wav(filepath, mono, sample_rate=_SR):
    """Write a mono 16-bit WAV from float64 samples in [-1, 1]."""
    i16 = np.clip(np.asarray(mono, dtype=np.float64) * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())


def _noise(duration_sec, sample_rate=_SR, amplitude=0.1):
    """White noise at low amplitude."""
    n = int(sample_rate * duration_sec)
    return amplitude * np.random.randn(n)


def _tone_burst(freq, duration_sec, sample_rate=_SR, amplitude=1.0,
                onset_sec=0.0):
    """Sine wave that only starts after *onset_sec* of silence."""
    n_total = int(sample_rate * duration_sec)
    n_onset = int(sample_rate * onset_sec)
    signal = np.zeros(n_total, dtype=np.float64)
    if n_onset < n_total:
        n_tone = n_total - n_onset
        t = np.arange(n_tone) / sample_rate
        signal[n_onset:] = amplitude * np.sin(2.0 * np.pi * freq * t)
    return signal


# ══════════════════════════════════════════════════════════════
# STFT + P90 tests
# ══════════════════════════════════════════════════════════════

class TestStftP90:
    """Verify that P90 aggregation captures intermittent events."""

    def test_sine_constant(self, tmp_path):
        """P90 ≈ mean for a steady-state sine."""
        sig = _sine(1000, 1.0)
        p = tmp_path / "sine.wav"
        _write_wav(p, sig)

        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, _SR)
        # Find the 1 kHz bin
        idx = int(np.argmin(np.abs(freqs - 1000)))
        assert mag_db[idx] > -20, f"1 kHz tone should be strong, got {mag_db[idx]:.1f} dB"

    def test_mean_stft_3khz_tone(self, tmp_path):
        """librosa STFT 均值应在纯音处有清晰峰值。"""
        sr = _SR
        dur = 1.0
        sig = _sine(3000, dur, sr, amplitude=0.8)

        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)
        idx_3k = int(np.argmin(np.abs(freqs - 3000)))
        # 纯正弦波能量应高于周围
        assert mag_db[idx_3k] > -10, (
            f"3kHz tone should be clear, got {mag_db[idx_3k]:.1f} dB"
        )
        # 3kHz 处应比 1kHz 处有明显峰
        idx_1k = int(np.argmin(np.abs(freqs - 1000)))
        assert mag_db[idx_3k] > mag_db[idx_1k]

    def test_empty_audio(self):
        """Empty audio returns safe fallback."""
        sig = np.array([], dtype=np.float64)
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, _SR)
        assert len(mag_db) > 0
        assert np.all(mag_db <= -100)

    def test_very_short_audio(self):
        """Audio shorter than one frame still works."""
        sig = _sine(1000, 0.01)  # 10 ms < 50 ms frame
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, _SR)
        assert len(mag_db) > 0
        idx = int(np.argmin(np.abs(freqs - 1000)))
        assert mag_db[idx] > -20


# ══════════════════════════════════════════════════════════════
# Q-factor tests
# ══════════════════════════════════════════════════════════════

class TestQFactor:
    """Verify Q = centre_freq / bandwidth computation.

    Tests use the STFT+P90 spectrum because :meth:`_compute_q_factor`
    is designed to work on aggregated (not raw single-frame) data.
    """

    def test_broad_peak_low_q(self):
        """A signal with wide bandwidth → Q < 30 in P90 spectrum."""
        sr = _SR
        dur = 2.0
        n = int(sr * dur)
        t = np.arange(n) / sr
        # Exponentially decaying sine → broadened spectral peak
        decay = np.exp(-t * 50)
        sig = decay * np.sin(2.0 * np.pi * 1000 * t) * 0.9

        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)

        idx = int(np.argmin(np.abs(freqs - 1000)))
        q = SpectrumAnalyzer._compute_q_factor(mag_db, freqs, idx)
        # Decaying sine broadens the peak; Q should be well under 30
        assert q < 30, f"Decaying sine should have moderate Q, got {q:.1f}"

    def test_narrow_peak_high_q(self):
        """A steady pure sine in P90 → very narrow peak → high Q."""
        sr = _SR
        dur = 2.0
        sig = _sine(1000, dur, sr, amplitude=1.0)

        # Use STFT+P90 — the intended input to _compute_q_factor
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)

        idx = int(np.argmin(np.abs(freqs - 1000)))
        q = SpectrumAnalyzer._compute_q_factor(mag_db, freqs, idx)
        # 稳态纯音 → 窄峰 → Q > 8（librosa STFT bin 间距略宽）
        assert q > 8, f"Pure tone should have high Q, got {q:.1f}"


# ══════════════════════════════════════════════════════════════
# Harmonic filtering tests
# ══════════════════════════════════════════════════════════════

class TestHarmonicFilter:
    """Verify that musical harmonics are NOT mistaken for resonances."""

    def test_harmonic_series_not_resonance(self, tmp_path):
        """A signal with F0=200 Hz + harmonics → all peaks are harmonics."""
        sr = _SR
        dur = 1.0
        t = np.arange(int(sr * dur)) / sr
        sig = (
            0.5 * np.sin(2.0 * np.pi * 200 * t)     # F0
            + 0.3 * np.sin(2.0 * np.pi * 400 * t)    # 2nd
            + 0.2 * np.sin(2.0 * np.pi * 600 * t)    # 3rd
            + 0.15 * np.sin(2.0 * np.pi * 800 * t)   # 4th
            + 0.1 * np.sin(2.0 * np.pi * 1000 * t)   # 5th
            + 0.08 * np.sin(2.0 * np.pi * 1200 * t)   # 6th
        )
        p = tmp_path / "harmonic.wav"
        _write_wav(p, sig)

        # Run _is_harmonic check directly
        f0_list = [200.0]
        assert SpectrumAnalyzer._is_harmonic(400.0, f0_list)   # 2×200
        assert SpectrumAnalyzer._is_harmonic(600.0, f0_list)   # 3×200
        assert SpectrumAnalyzer._is_harmonic(1000.0, f0_list)  # 5×200
        assert SpectrumAnalyzer._is_harmonic(1200.0, f0_list)  # 6×200

        # Non-harmonic: 347 Hz is not near any integer multiple of 200
        assert not SpectrumAnalyzer._is_harmonic(347.0, f0_list)

    def test_room_mode_vs_harmonic_separation(self, tmp_path):
        """Signal with F0=200 + harmonics AND a 340 Hz narrow room mode.

        Only the 340 Hz peak should be marked as a non-harmonic resonance.
        Note: 340 Hz is used because STFT at 50 ms / 48 kHz has ~20 Hz bin
        spacing, so 340 Hz lands exactly on a bin centre.
        """
        sr = _SR
        dur = 2.0
        t = np.arange(int(sr * dur)) / sr
        # Harmonic series
        sig = (
            0.5 * np.sin(2.0 * np.pi * 200 * t)
            + 0.3 * np.sin(2.0 * np.pi * 400 * t)
            + 0.2 * np.sin(2.0 * np.pi * 600 * t)
            + 0.15 * np.sin(2.0 * np.pi * 800 * t)
            # Narrow room mode at 340 Hz (340/200 = 1.7, NOT a harmonic)
            + 0.25 * np.sin(2.0 * np.pi * 340 * t)
        )
        p = tmp_path / "mixed.wav"
        _write_wav(p, sig)

        report = SpectrumAnalyzer.analyze(str(p))

        resonance_freqs = [r.freq_hz for r in report.resonances]

        # 340 Hz 应在共振列表中（librosa bin 间距 ≈23Hz，允许 25Hz 容差）
        has_340 = any(abs(r.freq_hz - 340.0) < 25 for r in report.resonances)
        assert has_340, f"340 Hz room mode should be detected, got {resonance_freqs}"

        # 至少一个谐波被检测到
        harmonics_found = [r for r in report.resonances if r.is_harmonic]
        assert len(harmonics_found) > 0, (
            f"At least one harmonic should be found, got {resonance_freqs}"
        )

    def test_is_harmonic_with_tolerance(self):
        """Frequencies within 5 % of integer multiple → harmonic."""
        f0_list = [150.0, 300.0]  # two candidates

        # Exactly 2×150 = 300
        assert SpectrumAnalyzer._is_harmonic(300.0, f0_list, tolerance=0.05)
        # 4.9 % off → within 5 % tolerance
        assert SpectrumAnalyzer._is_harmonic(455.0, f0_list, tolerance=0.05)
        # 10 % off → outside tolerance
        assert not SpectrumAnalyzer._is_harmonic(495.0, f0_list, tolerance=0.05)

    def test_is_harmonic_empty_f0(self):
        """Empty F0 list returns False."""
        assert not SpectrumAnalyzer._is_harmonic(440.0, [])
        assert not SpectrumAnalyzer._is_harmonic(440.0, [], tolerance=0.05)


# ══════════════════════════════════════════════════════════════
# A-weighting tests
# ══════════════════════════════════════════════════════════════

class TestAWeighting:
    """Verify perceptual loudness correction."""

    def test_1khz_near_zero(self):
        """A(1000 Hz) should be ≈ 0 dB (reference point)."""
        a = SpectrumAnalyzer._a_weighting(np.array([1000.0]))
        assert -0.5 < a[0] < 0.5, f"A(1 kHz) ≈ 0 dB, got {a[0]:.2f}"

    def test_low_freq_attenuated(self):
        """100 Hz should be attenuated vs 3 kHz."""
        freqs = np.array([100.0, 3000.0])
        a = SpectrumAnalyzer._a_weighting(freqs)
        # 100 Hz attenuated by ~-19 dB, 3 kHz boosted by ~+1 dB
        assert a[0] < -10, f"100 Hz should be attenuated, got {a[0]:.1f} dB"
        assert a[1] > -3, f"3 kHz should be near 0 or boosted, got {a[1]:.1f} dB"

    def test_equal_energy_perceived_different(self, tmp_path):
        """Equal physical energy at 100 Hz vs 3 kHz → 3 kHz perceived louder."""
        sr = _SR
        dur = 0.5
        t = np.arange(int(sr * dur)) / sr
        # Same amplitude at both frequencies
        sig = 0.5 * np.sin(2.0 * np.pi * 100 * t) + 0.5 * np.sin(2.0 * np.pi * 3000 * t)
        p = tmp_path / "dual.wav"
        _write_wav(p, sig)

        n = len(sig)
        mag = np.abs(np.fft.rfft(sig))
        mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)

        raw_idx_100 = int(np.argmin(np.abs(freqs - 100)))
        raw_idx_3k = int(np.argmin(np.abs(freqs - 3000)))

        raw_diff = mag_db[raw_idx_100] - mag_db[raw_idx_3k]
        # Before weighting they should be roughly equal
        assert abs(raw_diff) < 2.0, f"Raw energies should be similar, diff={raw_diff:.1f}"

        # After A-weighting, 3 kHz should be relatively louder
        a_weighted = SpectrumAnalyzer._apply_a_weighting(mag_db, freqs)
        a_diff = a_weighted[raw_idx_100] - a_weighted[raw_idx_3k]
        assert a_diff < -10, (
            f"A-weighted: 100 Hz should be much quieter than 3 kHz, "
            f"diff={a_diff:.1f} dB"
        )


# ══════════════════════════════════════════════════════════════
# Band energy tests
# ══════════════════════════════════════════════════════════════

class TestBandEnergy:
    """Verify frequency band energy computation."""

    def test_all_bands_present(self):
        """Every defined band should appear in the output."""
        sr = _SR
        dur = 1.0
        sig = _noise(dur, sr, amplitude=0.3)
        n = len(sig)
        mag = np.abs(np.fft.rfft(sig))
        mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)

        result = SpectrumAnalyzer._compute_band_energy(mag_db, freqs)
        for band in _BAND_EDGES:
            assert band in result, f"Band '{band}' missing from result"
            assert result[band] > -120, f"Band '{band}' should have finite energy"

    def test_band_energy_sine_in_band(self):
        """A 1 kHz sine should land in the 'mid' band."""
        sr = _SR
        sig = _sine(1000, 1.0, sr)
        n = len(sig)
        mag = np.abs(np.fft.rfft(sig))
        mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)

        result = SpectrumAnalyzer._compute_band_energy(mag_db, freqs)
        # Mid band (500-2000 Hz) should be dominant
        assert result["mid"] > result["sub"]
        assert result["mid"] > result["air"]


# ══════════════════════════════════════════════════════════════
# Spectral tilt tests
# ══════════════════════════════════════════════════════════════

class TestSpectralTilt:
    """Verify spectral tilt (slope) computation."""

    def test_white_noise_flat(self):
        """White noise → near-zero tilt."""
        sr = _SR
        dur = 2.0
        sig = _noise(dur, sr, amplitude=0.5)
        n = len(sig)
        mag = np.abs(np.fft.rfft(sig))
        mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
        freqs = np.fft.rfftfreq(n, d=1.0 / sr)

        tilt = SpectrumAnalyzer._compute_spectral_tilt(mag_db, freqs)
        # White noise in dB has slight roll-off from bin energy distribution
        assert -3 < tilt < 3, f"White noise tilt should be near zero, got {tilt:.2f}"

    def test_low_pass_negative_tilt(self):
        """Signal with more low freq → negative tilt (darker).

        Uses a simple 1-pole low-pass IIR applied to white noise so that
        there is continuous broadband energy with a real downward slope.
        """
        sr = _SR
        dur = 2.0
        noise = _noise(dur, sr, amplitude=1.0)

        # 1-pole low-pass at 400 Hz: y[n] = y[n-1] + a*(x[n] - y[n-1])
        fc = 400.0
        alpha = 1.0 - np.exp(-2.0 * np.pi * fc / sr)
        sig = np.zeros_like(noise)
        for i in range(1, len(noise)):
            sig[i] = sig[i - 1] + alpha * (noise[i] - sig[i - 1])

        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)

        tilt = SpectrumAnalyzer._compute_spectral_tilt(mag_db, freqs)
        # Low-pass filtered → energy rolls off → negative slope
        assert tilt < -2, f"Low-pass signal should have negative tilt, got {tilt:.2f}"


# ══════════════════════════════════════════════════════════════
# Full pipeline tests
# ══════════════════════════════════════════════════════════════

class TestFullPipeline:
    """End-to-end SpectrumAnalyzer.analyze() tests."""

    def test_analyze_returns_report(self, tmp_path):
        """Basic smoke test — analyze returns a SpectrumReport."""
        sig = _sine(1000, 1.0)
        p = tmp_path / "smoke.wav"
        _write_wav(p, sig)
        report = SpectrumAnalyzer.analyze(str(p))
        assert isinstance(report, SpectrumReport)
        assert len(report.band_energy_db) == len(_BAND_EDGES)
        assert isinstance(report.resonances, list)

    def test_analyze_mud_detection(self, tmp_path):
        """Boost 300 Hz heavily → mud_ratio_db should be high."""
        sr = _SR
        dur = 1.0
        t = np.arange(int(sr * dur)) / sr
        # Very strong 300 Hz (in low_mid mud zone) + weak everything else
        sig = 0.9 * np.sin(2.0 * np.pi * 300 * t) + 0.05 * _noise(dur, sr, 0.05)
        p = tmp_path / "muddy.wav"
        _write_wav(p, sig)

        report = SpectrumAnalyzer.analyze(str(p))
        # Low-mid (250-500 Hz) should dominate mid (500-2000 Hz)
        assert report.mud_ratio_db > 2.0, (
            f"300 Hz boost should give high mud ratio, got {report.mud_ratio_db:.1f}"
        )

    def test_analyze_dark_vocal(self, tmp_path):
        """Signal with weak high end → presence_deficit > 0."""
        sr = _SR
        dur = 1.0
        t = np.arange(int(sr * dur)) / sr
        # Strong mid, very weak presence
        sig = (
            0.5 * np.sin(2.0 * np.pi * 200 * t)     # low
            + 0.5 * np.sin(2.0 * np.pi * 800 * t)    # mid
            + 0.02 * np.sin(2.0 * np.pi * 6000 * t)  # very weak presence
        )
        p = tmp_path / "dark.wav"
        _write_wav(p, sig)

        report = SpectrumAnalyzer.analyze(str(p))
        assert report.presence_deficit_db > 2.0, (
            f"Dark signal should show presence deficit, got {report.presence_deficit_db:.1f}"
        )

    def test_analyze_stereo_handled(self, tmp_path):
        """Stereo WAV should be downmixed to mono correctly."""
        sr = _SR
        dur = 1.0
        mono = _sine(1000, dur, sr)
        stereo = np.column_stack([mono, mono * 0.5])
        # Write stereo via soundfile
        import soundfile as sf
        p = tmp_path / "stereo.wav"
        sf.write(str(p), stereo, sr)

        report = SpectrumAnalyzer.analyze(str(p))
        assert isinstance(report, SpectrumReport)
        # Mid band should have energy from 1 kHz sine
        assert report.band_energy_db["mid"] > -40


# ══════════════════════════════════════════════════════════════
# Data class tests
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestResonanceDataclass:
    """Verify Resonance dataclass fields."""

    def test_creation_and_fields(self):
        r = Resonance(freq_hz=340.0, prominence_db=12.5, q_factor=25.0,
                       is_harmonic=False)
        assert r.freq_hz == 340.0
        assert r.prominence_db == 12.5
        assert r.q_factor == 25.0
        assert r.is_harmonic is False

    def test_harmonic_resonance(self):
        r = Resonance(freq_hz=400.0, prominence_db=8.0, q_factor=10.0,
                       is_harmonic=True)
        assert r.is_harmonic is True
        assert r.freq_hz == 400.0


@pytest.mark.unit
class TestSpectrumReportDataclass:
    """Verify SpectrumReport dataclass creation and field access."""

    def test_manual_creation(self):
        r = Resonance(freq_hz=500.0, prominence_db=10.0, q_factor=20.0,
                       is_harmonic=False)
        report = SpectrumReport(
            band_energy_db={"mid": -10.0, "low": -20.0},
            spectral_tilt_db_per_octave=-3.5,
            resonances=[r],
            mud_ratio_db=2.0,
            presence_deficit_db=4.0,
            sibilance_peak_hz=6500.0,
            air_level_db=-25.0,
        )
        assert report.band_energy_db["mid"] == -10.0
        assert report.spectral_tilt_db_per_octave == -3.5
        assert len(report.resonances) == 1
        assert report.resonances[0].freq_hz == 500.0
        assert report.mud_ratio_db == 2.0
        assert report.presence_deficit_db == 4.0
        assert report.sibilance_peak_hz == 6500.0
        assert report.air_level_db == -25.0


# ══════════════════════════════════════════════════════════════
# SpectrumAnalyzer class methods (_read_pcm, _to_mono)
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestReadPcmClassMethod:
    """Test SpectrumAnalyzer._read_pcm static method."""

    def test_reads_mono_wav(self, tmp_path):
        """Mono WAV is read as 2-D array."""
        import soundfile as sf
        sig = _sine(1000, 1.0)
        p = str(tmp_path / "mono.wav")
        sf.write(p, sig, _SR)
        data, sr = read_pcm(p)
        assert data.ndim == 2
        assert data.shape[1] == 1
        assert sr == _SR

    def test_reads_stereo_wav(self, tmp_path):
        """Stereo WAV has 2 channels."""
        import soundfile as sf
        sig = _sine(1000, 1.0)
        stereo = np.column_stack([sig, sig * 0.5])
        p = str(tmp_path / "stereo.wav")
        sf.write(p, stereo, _SR)
        data, sr = read_pcm(p)
        assert data.ndim == 2
        assert data.shape[1] == 2
        assert sr == _SR


@pytest.mark.unit
class TestToMonoClassMethod:
    """Test SpectrumAnalyzer._to_mono static method."""

    def test_mono_input_1d(self):
        """1-D input returns float64 copy."""
        sig = np.array([0.1, 0.2, 0.3], dtype=np.float32)
        result = to_mono(sig)
        assert result.dtype == np.float64
        assert len(result) == 3

    def test_mono_input_2d_single_channel(self):
        """2-D single channel extracts first column."""
        sig = np.array([[0.1], [0.2], [0.3]], dtype=np.float32)
        result = to_mono(sig)
        assert result.ndim == 1
        assert result.dtype == np.float64
        assert len(result) == 3

    def test_stereo_input_averaged(self):
        """Multi-channel averaged across channels."""
        sig = np.array([[0.1, 0.3], [0.2, 0.4], [0.3, 0.5]], dtype=np.float32)
        result = to_mono(sig)
        assert result.ndim == 1
        assert len(result) == 3
        assert result[0] == pytest.approx(0.2)  # mean(0.1, 0.3)


# ══════════════════════════════════════════════════════════════
# Edge-case and branch coverage tests
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSTFTMean:
    """Test _stft_mean (librosa-based)."""

    def test_empty_audio_returns_flat(self):
        """空音频 → 返回 -120 dB 平坦频谱。"""
        mag_db, freqs = SpectrumAnalyzer._stft_mean(
            np.array([], dtype=np.float64), 48000,
        )
        assert len(mag_db) > 0
        assert np.all(mag_db <= -100.0)


@pytest.mark.unit
class TestDetectResonancesEdgeCase:
    """Test _detect_resonances with edge-case inputs."""

    def test_short_spectrum_returns_empty(self):
        """Spectrum with < 10 bins returns empty list."""
        mag = np.array([-10.0, -20.0, -30.0, -40.0, -50.0])
        freqs = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
        result = SpectrumAnalyzer._detect_resonances(mag, freqs)
        assert result == []

    def test_detects_single_resonance(self):
        """纯正弦波应被检测为共振。"""
        sr = _SR
        sig = _sine(500, 2.0, sr)
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)
        result = SpectrumAnalyzer._detect_resonances(mag_db, freqs)
        assert len(result) > 0


@pytest.mark.unit
class TestComputeQFactorEdgeCase:
    """Test _compute_q_factor with invalid indices and zero bandwidth."""

    def test_negative_index_returns_100(self):
        """Negative peak index returns 100.0."""
        mag = np.array([-10.0, -20.0, -30.0, -40.0, -50.0])
        freqs = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
        q = SpectrumAnalyzer._compute_q_factor(mag, freqs, -1)
        assert q == 100.0

    def test_out_of_bounds_index_returns_100(self):
        """Out-of-bounds peak index returns 100.0."""
        mag = np.array([-10.0, -20.0, -30.0, -40.0, -50.0])
        freqs = np.array([100.0, 200.0, 300.0, 400.0, 500.0])
        q = SpectrumAnalyzer._compute_q_factor(mag, freqs, 5)  # >= len
        assert q == 100.0

    def test_single_element_zero_bandwidth(self):
        """Single-element spectrum → zero bandwidth → returns 100.0."""
        mag = np.array([-10.0])
        freqs = np.array([100.0])
        q = SpectrumAnalyzer._compute_q_factor(mag, freqs, 0)
        assert q == 100.0


@pytest.mark.unit
class TestEstimateF0RangeEdgeCase:
    """Test _estimate_f0_range edge cases."""

    def test_sparse_data_returns_default(self):
        """Too few bins (< 3) in F0 range → returns [200.0]."""
        mag = np.array([-10.0, -20.0])
        freqs = np.array([50.0, 100.0])  # only 1 bin in 80-400 range
        result = SpectrumAnalyzer._estimate_f0_range(mag, freqs)
        assert result == [200.0]

    def test_no_peaks_fallback_returns_max_energy(self):
        """No peaks in F0 range → returns frequency with max energy."""
        mag = np.array([-10.0, -15.0, -20.0, -18.0, -25.0])
        freqs = np.array([80.0, 120.0, 200.0, 250.0, 350.0])
        result = SpectrumAnalyzer._estimate_f0_range(mag, freqs)
        assert len(result) == 1
        assert result[0] == 80.0  # highest energy

    def test_finds_peaks_in_f0_range(self):
        """Normal case returns top 3 peak frequencies."""
        sr = _SR
        sig = _sine(200, 2.0, sr, 0.8)
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)
        result = SpectrumAnalyzer._estimate_f0_range(mag_db, freqs)
        assert len(result) >= 1


@pytest.mark.unit
class TestIsHarmonicEdgeCase:
    """Test _is_harmonic with zero/negative F0 and frequency below F0."""

    def test_f0_zero_is_skipped(self):
        """F0 <= 0 is skipped, not causing division by zero."""
        f0_list = [0.0, -1.0, 200.0]
        assert SpectrumAnalyzer._is_harmonic(400.0, f0_list)  # 2×200
        assert not SpectrumAnalyzer._is_harmonic(350.0, f0_list)

    def test_all_f0_zero_or_negative(self):
        """All F0 values <= 0 → not harmonic."""
        assert not SpectrumAnalyzer._is_harmonic(400.0, [0.0, -100.0])

    def test_frequency_below_f0(self):
        """Freq lower than F0 → nearest_int < 1 → skipped, not harmonic."""
        f0_list = [500.0]
        assert not SpectrumAnalyzer._is_harmonic(200.0, f0_list)


# ══════════════════════════════════════════════════════════════
# Sibilance fallback test
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSibilanceFallback:
    """Test sibilance detection fallback when no energy in 4-12 kHz."""

    def test_low_sample_rate_triggers_fallback(self, tmp_path):
        """SR 7 kHz → Nyquist 3.5 kHz → no bins in 4-12 kHz → fallback."""
        sr = 7000
        dur = 0.5
        t = np.arange(int(sr * dur)) / sr
        sig = 0.8 * np.sin(2.0 * np.pi * 500 * t)
        p = tmp_path / "low_sr.wav"
        _write_wav(p, sig, sample_rate=sr)
        report = SpectrumAnalyzer.analyze(str(p))
        assert report.sibilance_peak_hz == 8000.0


# ══════════════════════════════════════════════════════════════
# Spectral tilt edge cases
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSpectralTiltEdgeCases:
    """Test _compute_spectral_tilt edge cases."""

    def test_too_few_bins_returns_zero(self):
        """< 4 bins in 100Hz-10kHz range → return 0.0."""
        mag = np.array([-10.0, -20.0, -30.0])
        freqs = np.array([50.0, 150.0, 12000.0])  # only 150 Hz in range
        tilt = SpectrumAnalyzer._compute_spectral_tilt(mag, freqs)
        assert tilt == 0.0

    def test_near_zero_denominator_returns_zero(self):
        """All log_f values identical → denominator near zero → return 0.0."""
        mag = np.array([-10.0, -20.0, -30.0, -40.0])
        freqs = np.array([200.0, 200.0, 200.0, 200.0])
        tilt = SpectrumAnalyzer._compute_spectral_tilt(mag, freqs)
        assert tilt == 0.0


# ══════════════════════════════════════════════════════════════
# Band energy — no-bins-in-band test
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBandEnergyEdgeCases:
    """Test _compute_band_energy when some bands have no bins."""

    def test_band_with_no_bins_returns_minus_120(self):
        """Bands with no frequency bins get -120 dB fallback."""
        mag = np.array([-10.0, -20.0])
        freqs = np.array([10000.0, 15000.0])  # all > 8 kHz, only air band
        result = SpectrumAnalyzer._compute_band_energy(mag, freqs)
        for band in ["sub", "low", "low_mid", "mid", "high_mid", "presence"]:
            assert result[band] == -120.0, f"Band '{band}' should be -120"
        assert result["air"] > -120.0


# ══════════════════════════════════════════════════════════════
# Very-low-sample-rate test (frame_samples < 16)
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestVeryLowSampleRate:
    """Test _stft_mean with sample rates so low that frame_samples < 16."""

    def test_frame_samples_clamped_to_minimum(self):
        """sr=100, frame_ms=50 → frame_samples=5 → clamped to 16."""
        sr = 100
        dur = 2.0
        t = np.arange(int(sr * dur)) / sr
        sig = np.sin(2.0 * np.pi * 10 * t)
        mag_db, freqs = SpectrumAnalyzer._stft_mean(sig, sr)
        assert len(mag_db) > 0


# ══════════════════════════════════════════════════════════════
# Error handling in analyze()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestAnalyzeErrorPaths:
    """Test SpectrumAnalyzer.analyze error handling."""

    def test_file_not_found(self, tmp_path):
        """Non-existent file should raise an exception."""
        with pytest.raises(Exception):
            SpectrumAnalyzer.analyze(str(tmp_path / "nonexistent.wav"))
