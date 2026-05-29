"""Tests for hermes_core.loudness_optimizer — mastering LUFS math core."""

import json
import math

import numpy as np
import pytest
from pathlib import Path

from hermes_core.loudness_optimizer import (
    _hard_clip,
    LoudnessResult,
    VerifyResult,
    find_optimal_gain,
    verify_output,
    run_calibration,
    load_calibration,
    generate_report,
)


# ════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════


def _make_sine_wav(path: str, freq: float = 440.0, duration: float = 2.0,
                   sr: int = 48000, amplitude: float = 0.5) -> tuple[np.ndarray, int]:
    """Write a stereo sine WAV and return (pcm, sample_rate)."""
    import soundfile as sf
    t = np.linspace(0, duration, int(sr * duration), endpoint=False)
    mono = (amplitude * np.sin(2.0 * np.pi * freq * t)).astype(np.float64)
    stereo = np.column_stack([mono, mono])
    sf.write(path, stereo, sr, subtype="PCM_16")
    return stereo, sr


def _make_silence_wav(path: str, duration: float = 1.0, sr: int = 48000) -> str:
    """Write a near-silent stereo WAV."""
    import soundfile as sf
    n = int(sr * duration)
    stereo = np.full((n, 2), 1e-10, dtype=np.float64)
    sf.write(path, stereo, sr, subtype="FLOAT")
    return path


# ════════════════════════════════════════════════════════════
# _hard_clip
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestHardClip:
    def test_zero_gain_unchanged(self):
        """Zero gain with high ceiling passes audio through."""
        audio = np.array([0.1, -0.2, 0.3, -0.4], dtype=np.float64)
        result = _hard_clip(audio, gain_db=0.0, ceiling_db=0.0)
        assert np.allclose(result, audio)

    def test_positive_gain_amplifies(self):
        """+6 dB gain (~2x) increases amplitude."""
        audio = np.array([0.1, -0.1], dtype=np.float64)
        result = _hard_clip(audio, gain_db=6.0, ceiling_db=3.0)
        gain_linear = 10.0 ** (6.0 / 20.0)
        assert np.allclose(result, audio * gain_linear, atol=1e-10)

    def test_clips_at_ceiling(self):
        """Signal exceeding the ceiling is hard-clipped."""
        audio = np.array([1.5, -2.0, 0.3], dtype=np.float64)
        result = _hard_clip(audio, gain_db=0.0, ceiling_db=0.0)
        assert result[0] == 1.0   # clipped to ceiling
        assert result[1] == -1.0  # clipped to -ceiling
        assert result[2] == 0.3   # below ceiling, unchanged

    def test_gain_plus_ceiling(self):
        """Gain applied before clipping — large gain clips to ceiling."""
        audio = np.array([0.5], dtype=np.float64)
        # +20 dB gain = 10x, ceiling 0 dB = 1.0 → will clip
        result = _hard_clip(audio, gain_db=20.0, ceiling_db=0.0)
        assert result[0] == 1.0  # clipped at 1.0

    def test_custom_ceiling(self):
        """Ceiling at -0.5 dB."""
        audio = np.array([2.0], dtype=np.float64)
        ceiling_linear = 10.0 ** (-0.5 / 20.0)
        result = _hard_clip(audio, gain_db=0.0, ceiling_db=-0.5)
        assert np.isclose(result[0], ceiling_linear)

    def test_empty_array(self):
        result = _hard_clip(np.array([], dtype=np.float64), gain_db=6.0, ceiling_db=-0.5)
        assert len(result) == 0


# ════════════════════════════════════════════════════════════
# find_optimal_gain
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFindOptimalGain:
    def test_converges_on_sine(self, tmp_path):
        """Binary search converges for a sine WAV with moderate LUFS."""
        wav_path = tmp_path / "sine.wav"
        _make_sine_wav(str(wav_path), duration=3.0, amplitude=0.3)
        result = find_optimal_gain(
            str(wav_path), target_lufs=-12.0, tolerance=0.3,
        )
        assert result.converged, f"Should converge, got iters={result.iterations}"
        assert result.iterations > 0
        assert result.gain_db != 0.0  # some gain needed

    def test_empty_audio_returns_sentinel(self, tmp_path):
        """Empty/zero-sample WAV returns non-converged sentinel result."""
        wav_path = tmp_path / "empty.wav"
        _make_silence_wav(str(wav_path))
        result = find_optimal_gain(str(wav_path), target_lufs=-12.0)
        assert not result.converged
        assert result.gain_db == 0.0

    def test_narrow_gain_range(self, tmp_path):
        """Custom gain_range works."""
        wav_path = tmp_path / "sine.wav"
        _make_sine_wav(str(wav_path), duration=2.0, amplitude=0.3)
        result = find_optimal_gain(
            str(wav_path), target_lufs=-12.0,
            gain_range=(-3.0, 6.0), max_iterations=10,
        )
        # Should converge or at least not crash
        assert isinstance(result, LoudnessResult)
        assert -3.0 <= result.gain_db <= 6.0 + result.calibration_applied

    def test_calibration_offset_applied(self, tmp_path):
        """Calibration offset is added to final gain."""
        wav_path = tmp_path / "sine.wav"
        _make_sine_wav(str(wav_path), duration=2.0, amplitude=0.3)
        result_no_cal = find_optimal_gain(str(wav_path), target_lufs=-12.0,
                                          calibration_offset=0.0)
        result_cal = find_optimal_gain(str(wav_path), target_lufs=-12.0,
                                       calibration_offset=0.5)
        assert result_cal.calibration_applied == 0.5
        # Gain with calibration should be ~0.5 dB higher
        assert abs(result_cal.gain_db - result_no_cal.gain_db - 0.5) < 0.1

    def test_nonexistent_file_raises(self):
        """Missing WAV file raises an error."""
        with pytest.raises(Exception):
            find_optimal_gain("/nonexistent/file.wav")


# ════════════════════════════════════════════════════════════
# verify_output
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestVerifyOutput:
    def test_passes_when_within_threshold(self, tmp_path):
        wav_path = tmp_path / "verify.wav"
        _make_sine_wav(str(wav_path), duration=3.0, amplitude=0.3)
        result = verify_output(str(wav_path), target_lufs=-12.0, pass_threshold=20.0)
        assert result.passed  # threshold is very wide, should pass

    def test_nonexistent_file_raises(self):
        with pytest.raises(Exception):
            verify_output("/nonexistent/file.wav")


# ════════════════════════════════════════════════════════════
# load_calibration
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLoadCalibration:
    def test_returns_zero_when_file_missing(self, monkeypatch, tmp_path):
        """No calibration file → returns 0.0."""
        cal_path = tmp_path / "nonexistent.json"
        monkeypatch.setattr(
            "hermes_core.loudness_optimizer._CALIBRATION_FILE",
            str(cal_path),
        )
        assert load_calibration() == 0.0

    def test_loads_valid_calibration(self, monkeypatch, tmp_path):
        cal_path = tmp_path / "loudness_calibration.json"
        cal_path.write_text(json.dumps({"calibration_offset_db": -0.35}))
        monkeypatch.setattr(
            "hermes_core.loudness_optimizer._CALIBRATION_FILE",
            str(cal_path),
        )
        assert load_calibration() == -0.35

    def test_missing_key_returns_zero(self, monkeypatch, tmp_path):
        cal_path = tmp_path / "cal.json"
        cal_path.write_text('{"other_field": 123}')
        monkeypatch.setattr(
            "hermes_core.loudness_optimizer._CALIBRATION_FILE",
            str(cal_path),
        )
        assert load_calibration() == 0.0


# ════════════════════════════════════════════════════════════
# run_calibration
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRunCalibration:
    def test_writes_calibration_file(self, tmp_path, monkeypatch):
        cal_path = tmp_path / "cal.json"
        monkeypatch.setattr(
            "hermes_core.loudness_optimizer._CALIBRATION_FILE",
            str(cal_path),
        )
        # Two sine WAVs with different gain — simulate probe and final
        probe = tmp_path / "probe.wav"
        final = tmp_path / "final.wav"
        _make_sine_wav(str(probe), duration=2.0, amplitude=0.3)
        _make_sine_wav(str(final), duration=2.0, amplitude=0.5)

        offset = run_calibration(str(probe), str(final), applied_gain=4.0)
        assert isinstance(offset, float)
        assert cal_path.exists()
        data = json.loads(cal_path.read_text())
        assert "calibration_offset_db" in data
        assert "sim_lufs" in data
        assert "actual_lufs" in data


# ════════════════════════════════════════════════════════════
# generate_report
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGenerateReport:
    def test_report_without_verify(self):
        result = LoudnessResult(
            gain_db=5.0, predicted_lufs=-12.5, probe_lufs=-20.0,
            iterations=8, converged=True, calibration_applied=0.0,
        )
        report = generate_report(result)
        assert "Mastering Loudness Report" in report
        assert "5.00 dB" in report
        assert "-12.5 LUFS" in report
        assert "8" in report  # iterations
        assert "Yes" in report  # converged

    def test_report_with_verify_pass(self):
        result = LoudnessResult(
            gain_db=5.0, predicted_lufs=-12.5, probe_lufs=-20.0,
            iterations=8, converged=True, calibration_applied=0.0,
        )
        verify = VerifyResult(
            actual_lufs=-12.2, target_lufs=-12.0, deviation=-0.2,
            passed=True, needs_correction=False, suggested_correction=0.0,
        )
        report = generate_report(result, verify)
        assert "PASS" in report
        assert "-12.2 LUFS" in report

    def test_report_with_verify_need_correction(self):
        result = LoudnessResult(
            gain_db=3.0, predicted_lufs=-10.0, probe_lufs=-18.0,
            iterations=12, converged=True, calibration_applied=0.0,
        )
        verify = VerifyResult(
            actual_lufs=-10.5, target_lufs=-12.0, deviation=1.5,
            passed=False, needs_correction=True, suggested_correction=-1.2,
        )
        report = generate_report(result, verify)
        assert "NEEDS CORRECTION" in report
        assert "Suggested fix" in report
        assert "-1.20 dB" in report


# ════════════════════════════════════════════════════════════
# Dataclass serialisation
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDataclassSerialisation:
    def test_loudness_result_to_dict(self):
        r = LoudnessResult(3.0, -12.0, -20.0, 10, True, 0.0)
        d = r.to_dict()
        assert d["gain_db"] == 3.0
        assert d["converged"] is True

    def test_verify_result_defaults(self):
        v = VerifyResult(-12.1, -12.0, -0.1, True, False, 0.0)
        assert v.passed is True
        assert v.needs_correction is False
        assert v.suggested_correction == 0.0
