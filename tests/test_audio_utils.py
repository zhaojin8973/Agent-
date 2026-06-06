"""Tests for hermes_core.audio_utils — shared audio utility functions."""

import math
import tempfile
from pathlib import Path
from unittest.mock import patch

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


# ════════════════════════════════════════════════════════════════
# PCM 临时文件生命周期测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCleanupPcmTemps:
    """测试 _cleanup_pcm_temps 和 _register_pcm_temp 函数。"""

    def teardown_method(self):
        """每个测试后清理模块级状态。"""
        import hermes_core.audio_utils as au
        with au._pcm_temp_lock:
            au._pcm_temp_files.clear()
        au._atexit_registered = False

    def test_register_pcm_temp_returns_path(self, tmp_path):
        """注册后应返回 Path 对象并追踪文件。"""
        import hermes_core.audio_utils as au

        test_file = tmp_path / "test_pcm.wav"
        test_file.write_text("dummy")

        result = au._register_pcm_temp(str(test_file))
        assert isinstance(result, Path)
        assert str(test_file) in au._pcm_temp_files

    def test_register_pcm_temp_path_object(self, tmp_path):
        """接受 Path 对象作为输入。"""
        import hermes_core.audio_utils as au

        test_file = tmp_path / "test_pcm2.wav"
        test_file.write_text("dummy")

        result = au._register_pcm_temp(test_file)
        assert result == test_file

    def test_cleanup_removes_registered_file(self, tmp_path):
        """注册后清理应删除文件。"""
        import hermes_core.audio_utils as au

        test_file = tmp_path / "to_clean.wav"
        test_file.write_text("dummy")

        au._register_pcm_temp(str(test_file))
        assert test_file.exists()
        assert str(test_file) in au._pcm_temp_files

        au._cleanup_pcm_temps()
        assert not test_file.exists()

    def test_cleanup_removes_registered_directory(self, tmp_path):
        """注册目录后清理应删除目录。"""
        import hermes_core.audio_utils as au

        test_dir = tmp_path / "pcm_cache"
        test_dir.mkdir()

        au._register_pcm_temp(str(test_dir))
        assert test_dir.exists()

        au._cleanup_pcm_temps()
        assert not test_dir.exists()

    def test_cleanup_handles_already_deleted(self, tmp_path):
        """文件已被手动删除时清理不报错。"""
        import hermes_core.audio_utils as au

        test_file = tmp_path / "already_gone.wav"
        test_file.write_text("dummy")

        au._register_pcm_temp(str(test_file))
        # 手动删除
        test_file.unlink()
        # 应不报错
        au._cleanup_pcm_temps()

    def test_cleanup_clears_registry(self, tmp_path):
        """清理后注册表应为空。"""
        import hermes_core.audio_utils as au

        f1 = tmp_path / "f1.wav"
        f2 = tmp_path / "f2.wav"
        f1.write_text("x")
        f2.write_text("y")

        au._register_pcm_temp(str(f1))
        au._register_pcm_temp(str(f2))
        assert len(au._pcm_temp_files) >= 2

        au._cleanup_pcm_temps()
        assert len(au._pcm_temp_files) == 0

    def test_cleanup_empty_list(self):
        """空注册表时清理不报错。"""
        import hermes_core.audio_utils as au

        au._cleanup_pcm_temps()  # 应不报错

    def test_cleanup_oserror_swallowed(self, tmp_path):
        """OSError（如权限问题）应被吞没不传播。"""
        import hermes_core.audio_utils as au

        test_file = tmp_path / "perm.wav"
        test_file.write_text("dummy")

        au._register_pcm_temp(str(test_file))
        # Mock os.unlink 抛出 OSError
        with patch("os.unlink", side_effect=OSError("Permission denied")):
            au._cleanup_pcm_temps()  # 应不抛出异常

    def test_cleanup_mixed_files_and_dirs(self, tmp_path):
        """混合文件和目录的清理。"""
        import hermes_core.audio_utils as au

        f1 = tmp_path / "audio.wav"
        f1.write_text("audio")
        d1 = tmp_path / "cache"
        d1.mkdir()

        au._register_pcm_temp(str(f1))
        au._register_pcm_temp(str(d1))

        au._cleanup_pcm_temps()
        assert not f1.exists()
        assert not d1.exists()
        assert len(au._pcm_temp_files) == 0
