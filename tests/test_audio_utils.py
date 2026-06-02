"""Tests for hermes_core.audio_utils — shared audio utility functions."""

import math
import tempfile
from pathlib import Path

import numpy as np
import pytest
import soundfile as sf

from hermes_core.audio_utils import (
    db_to_norm,
    norm_to_db,
    read_pcm,
    to_mono,
)


@pytest.mark.unit
class TestDbToNorm:
    """Test dB to normalized value conversion."""

    def test_unity(self):
        """0 dB should map to 1.0 (unity gain)."""
        assert db_to_norm(0.0) == pytest.approx(1.0)

    def test_minus_6_db(self):
        """-6 dB should map to approximately 0.5."""
        assert db_to_norm(-6.0) == pytest.approx(0.5, abs=0.01)

    def test_silence_threshold(self):
        """-150 dB or lower should map to 0.0 (silence)."""
        assert db_to_norm(-150.0) == 0.0
        assert db_to_norm(-200.0) == 0.0

    def test_positive_db(self):
        """Positive dB values should map to > 1.0."""
        assert db_to_norm(6.0) == pytest.approx(2.0, abs=0.05)

    def test_non_finite_values(self):
        """Non-finite values (NaN, inf) should map to 0.0."""
        assert db_to_norm(float('nan')) == 0.0
        assert db_to_norm(float('inf')) == 0.0
        assert db_to_norm(float('-inf')) == 0.0

    def test_minus_20_db(self):
        """-20 dB should map to 0.1."""
        assert db_to_norm(-20.0) == pytest.approx(0.1, abs=0.001)


@pytest.mark.unit
class TestNormToDb:
    """Test normalized value to dB conversion."""

    def test_unity(self):
        """1.0 should map to 0 dB."""
        assert norm_to_db(1.0) == pytest.approx(0.0)

    def test_half(self):
        """0.5 should map to approximately -6 dB."""
        assert norm_to_db(0.5) == pytest.approx(-6.0, abs=0.1)

    def test_zero(self):
        """0.0 should map to -150 dB (silence)."""
        assert norm_to_db(0.0) == -150.0

    def test_negative_values(self):
        """Negative values should map to -150 dB."""
        assert norm_to_db(-0.5) == -150.0

    def test_double(self):
        """2.0 should map to approximately +6 dB."""
        assert norm_to_db(2.0) == pytest.approx(6.0, abs=0.1)

    def test_one_tenth(self):
        """0.1 should map to -20 dB."""
        assert norm_to_db(0.1) == pytest.approx(-20.0, abs=0.1)


@pytest.mark.unit
class TestRoundTrip:
    """Test dB <-> norm round-trip conversion."""

    def test_round_trip_db_norm_db(self):
        """Converting dB -> norm -> dB should preserve the value."""
        for db in [-40, -20, -12, -6, -3, 0, 3, 6]:
            norm = db_to_norm(db)
            back = norm_to_db(norm)
            assert back == pytest.approx(db, abs=0.01)

    def test_round_trip_norm_db_norm(self):
        """Converting norm -> dB -> norm should preserve the value."""
        for norm in [0.1, 0.25, 0.5, 0.75, 1.0, 1.5, 2.0]:
            db = norm_to_db(norm)
            back = db_to_norm(db)
            assert back == pytest.approx(norm, abs=0.001)


@pytest.mark.unit
class TestReadPcm:
    """Test PCM audio file reading."""

    def test_read_mono_file(self, tmp_path):
        """Reading a mono file should return 2D array with shape (n_samples, 1)."""
        # Create a mono test file
        sample_rate = 48000
        duration = 1.0
        n_samples = int(sample_rate * duration)
        data = np.random.randn(n_samples).astype(np.float64)
        file_path = tmp_path / "test_mono.wav"
        sf.write(str(file_path), data, sample_rate)

        # Read and verify
        pcm, sr = read_pcm(str(file_path))
        assert sr == sample_rate
        assert pcm.ndim == 2
        assert pcm.shape == (n_samples, 1)
        assert pcm.dtype == np.float64

    def test_read_stereo_file(self, tmp_path):
        """Reading a stereo file should return 2D array with shape (n_samples, 2)."""
        # Create a stereo test file
        sample_rate = 48000
        duration = 1.0
        n_samples = int(sample_rate * duration)
        data = np.random.randn(n_samples, 2).astype(np.float64)
        file_path = tmp_path / "test_stereo.wav"
        sf.write(str(file_path), data, sample_rate)

        # Read and verify
        pcm, sr = read_pcm(str(file_path))
        assert sr == sample_rate
        assert pcm.ndim == 2
        assert pcm.shape == (n_samples, 2)
        assert pcm.dtype == np.float64


@pytest.mark.unit
class TestToMono:
    """Test multi-channel to mono conversion."""

    def test_mono_input_1d(self):
        """1D mono input should be returned as-is (converted to float64)."""
        pcm = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
        mono = to_mono(pcm)
        assert mono.ndim == 1
        assert mono.dtype == np.float64
        np.testing.assert_array_almost_equal(mono, pcm.astype(np.float64))

    def test_mono_input_2d_single_channel(self):
        """2D mono input (n, 1) should return 1D array."""
        pcm = np.array([[0.1], [0.2], [0.3], [0.4]], dtype=np.float64)
        mono = to_mono(pcm)
        assert mono.ndim == 1
        assert mono.shape == (4,)
        np.testing.assert_array_almost_equal(mono, [0.1, 0.2, 0.3, 0.4])

    def test_stereo_to_mono(self):
        """Stereo input should be averaged to mono."""
        pcm = np.array([
            [0.2, 0.4],
            [0.4, 0.6],
            [0.6, 0.8],
        ], dtype=np.float64)
        mono = to_mono(pcm)
        assert mono.ndim == 1
        assert mono.shape == (3,)
        np.testing.assert_array_almost_equal(mono, [0.3, 0.5, 0.7])

    def test_multichannel_to_mono(self):
        """Multi-channel input should be averaged to mono."""
        pcm = np.array([
            [0.1, 0.2, 0.3, 0.4],
            [0.5, 0.6, 0.7, 0.8],
        ], dtype=np.float64)
        mono = to_mono(pcm)
        assert mono.ndim == 1
        assert mono.shape == (2,)
        np.testing.assert_array_almost_equal(mono, [0.25, 0.65])

    def test_output_dtype(self):
        """Output should always be float64."""
        pcm_int16 = np.array([100, 200, 300], dtype=np.int16)
        mono = to_mono(pcm_int16)
        assert mono.dtype == np.float64
