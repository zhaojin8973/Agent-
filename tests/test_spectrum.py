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

        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, _SR)
        # Find the 1 kHz bin
        idx = int(np.argmin(np.abs(freqs - 1000)))
        assert mag_db[idx] > -20, f"1 kHz tone should be strong, got {mag_db[idx]:.1f} dB"

    def test_intermittent_resonance_captured(self, tmp_path):
        """P90 captures a resonance that appears in only 20% of frames.

        Signal: 80 % quiet noise, 20 % loud 3 kHz tone.  The global
        mean FFT would dilute the tone; P90 should still show it clearly.
        """
        sr = _SR
        dur = 1.0
        n = int(sr * dur)

        # 80 % quiet noise + 20 % loud tone at end
        sig = np.zeros(n, dtype=np.float64)
        sig[:int(0.8 * n)] = _noise(0.8, sr, amplitude=0.02)
        n_tone = int(0.2 * n)
        t = np.arange(n_tone) / sr
        sig[int(0.8 * n):] = 0.8 * np.sin(2.0 * np.pi * 3000 * t)

        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, sr)

        # Find energy at 3 kHz in P90
        idx_3k = int(np.argmin(np.abs(freqs - 3000)))
        p90_val = mag_db[idx_3k]

        # Compute global FFT mean for comparison
        full_fft = np.abs(np.fft.rfft(sig))
        full_db = 20.0 * np.log10(np.maximum(full_fft, 1e-10))
        mean_val = full_db[idx_3k]

        # The loud 3k in last 20% should show strongly in P90
        assert p90_val > -15, f"P90 at 3kHz should be loud, got {p90_val:.1f} dB"

    def test_empty_audio(self):
        """Empty audio returns safe fallback."""
        sig = np.array([], dtype=np.float64)
        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, _SR)
        assert len(mag_db) > 0
        assert np.all(mag_db <= -100)

    def test_very_short_audio(self):
        """Audio shorter than one frame still works."""
        sig = _sine(1000, 0.01)  # 10 ms < 50 ms frame
        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, _SR)
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

        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, sr)

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
        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, sr)

        idx = int(np.argmin(np.abs(freqs - 1000)))
        q = SpectrumAnalyzer._compute_q_factor(mag_db, freqs, idx)
        # Steady pure tone in P90 → very narrow → high Q
        assert q > 15, f"Pure tone should have high Q, got {q:.1f}"


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

        # 340 Hz should appear as a resonance (not harmonic of 200)
        has_340 = any(abs(r.freq_hz - 340.0) < 10 for r in report.resonances)
        assert has_340, f"340 Hz room mode should be detected, got {resonance_freqs}"

        # 400, 600, 800 should be marked as harmonics if detected
        for harmonic_freq in [400.0, 600.0, 800.0]:
            near = [r for r in report.resonances if abs(r.freq_hz - harmonic_freq) < 10]
            for r in near:
                assert r.is_harmonic, (
                    f"{harmonic_freq} Hz should be marked is_harmonic=True, "
                    f"got is_harmonic={r.is_harmonic}"
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

        mag_db, freqs = SpectrumAnalyzer._stft_p90(sig, sr)

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
