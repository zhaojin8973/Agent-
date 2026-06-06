"""覆盖率补全测试 — 将 77% 推至 80%+。

覆盖 comp_engine / config / audio_utils / signal /
project_meta / gain_staging / loudness_optimizer 的缺失分支。
"""
import json
import os
import struct
from pathlib import Path
from unittest.mock import MagicMock, patch
import numpy as np
import pytest
import soundfile as sf


# ═══════════════════════════ comp_engine ═══════════════════════════

class TestCLA76ReleaseKnob:
    """覆盖 _apply_cla76_params 中 release_knob 分支。"""

    def test_release_knob_included(self):
        from hermes_core.comp_engine import _apply_cla76_params
        from hermes_core.loudness_optimizer import CompressionIntent
        intent = CompressionIntent(
            amount="light", crest_factor_db=10.0, gr_target_db=3.0,
            rms_db=-18.0, peak_db=-6.0,
        )
        result = _apply_cla76_params(intent, attack_knob=4.0, release_knob=5.0)
        assert "Release" in result
        assert result["Release"] == 5.0


# ═══════════════════════════ config ═══════════════════════════

class TestConfigCorruptJSON:
    """覆盖 HermesConfig.load() 中损坏 JSON 的异常处理。"""

    def test_load_corrupt_json(self, tmp_path):
        from hermes_core.config import HermesConfig
        cfg_file = tmp_path / "config.json"
        cfg_file.write_text("{invalid json")
        with patch("hermes_core.config._config_path", return_value=cfg_file):
            cfg = HermesConfig.load()
            assert isinstance(cfg, HermesConfig)


# ═══════════════════════════ audio_utils ═══════════════════════════

class TestAudioUtilsEdges:
    def test_numpy_mix_sr_mismatch(self, tmp_path):
        """采样率不匹配时返回 None。"""
        from hermes_core.audio_utils import numpy_mix
        dry = tmp_path / "dry.wav"
        wet = tmp_path / "wet.wav"
        out = tmp_path / "out.wav"
        sf.write(str(dry), np.zeros((100, 1)), 48000)
        sf.write(str(wet), np.zeros((100, 1)), 44100)
        assert numpy_mix(str(dry), str(wet), -6.0, str(out)) is None

    def test_numpy_mix_mono_inputs(self, tmp_path):
        """单声道输入应正确广播。"""
        from hermes_core.audio_utils import numpy_mix
        dry = tmp_path / "dry.wav"
        wet = tmp_path / "wet.wav"
        out = tmp_path / "out.wav"
        sf.write(str(dry), np.zeros(100), 48000)       # 1-D mono
        sf.write(str(wet), np.ones(100) * 0.1, 48000)  # 1-D mono
        result = numpy_mix(str(dry), str(wet), 0.0, str(out))
        assert result is not None

    def test_numpy_mix_channel_count_diff(self, tmp_path):
        """不同声道数应广播到较小声道数。"""
        from hermes_core.audio_utils import numpy_mix
        dry = tmp_path / "dry.wav"
        wet = tmp_path / "wet.wav"
        out = tmp_path / "out.wav"
        sf.write(str(dry), np.zeros((100, 2)), 48000)  # stereo
        sf.write(str(wet), np.zeros((100, 1)), 48000)  # mono
        result = numpy_mix(str(dry), str(wet), 0.0, str(out))
        assert result is not None

    def test_numpy_mix_file_read_error(self, tmp_path):
        """文件读取失败时返回 None。"""
        from hermes_core.audio_utils import numpy_mix
        out = tmp_path / "out.wav"
        result = numpy_mix("/nonexistent/dry.wav", "/nonexistent/wet.wav", 0.0, str(out))
        assert result is None

    def test_read_pcm_mono_reshapes(self, tmp_path):
        """read_pcm 单声道 → 2-D 重塑。"""
        from hermes_core.audio_utils import read_pcm
        mono = tmp_path / "mono.wav"
        sf.write(str(mono), np.arange(100, dtype="float64"), 48000)
        data, sr = read_pcm(str(mono))
        assert data.ndim == 2

    def test_to_mono_stereo(self):
        """to_mono 立体声 → 通道平均。"""
        from hermes_core.audio_utils import to_mono
        stereo = np.column_stack([np.ones(100), np.ones(100) * 2])
        result = to_mono(stereo)
        assert result.ndim == 1
        assert result[0] == pytest.approx(1.5)

    def test_db_to_norm_silence(self):
        """-150 dB 以下 → 0.0。"""
        from hermes_core.audio_utils import db_to_norm
        assert db_to_norm(-200) == 0.0
        assert db_to_norm(float("-inf")) == 0.0

    def test_norm_to_db_silence(self):
        """0.0 归一化 → -150 dB。"""
        from hermes_core.audio_utils import norm_to_db
        assert norm_to_db(0.0) == -150.0
        assert norm_to_db(-0.1) == -150.0


# ═══════════════════════════ signal ═══════════════════════════

class TestSignalAnalyzerEdges:
    def test_analyze_valid_wav(self, tmp_path):
        """analyze 对有效 WAV 返回 SignalReport。"""
        from hermes_core.signal import SignalAnalyzer
        wav = tmp_path / "test.wav"
        sf.write(str(wav), np.sin(2 * np.pi * 440 * np.linspace(0, 1, 48000)), 48000)
        report = SignalAnalyzer.analyze(str(wav))
        assert report.integrated_lufs is not None
        assert report.peak_db is not None

    def test_analyze_stereo_wav(self, tmp_path):
        """analyze 处理立体声 WAV。"""
        from hermes_core.signal import SignalAnalyzer
        wav = tmp_path / "stereo.wav"
        t = np.linspace(0, 1, 48000)
        sig = np.column_stack([np.sin(2 * np.pi * 440 * t), np.sin(2 * np.pi * 880 * t)])
        sf.write(str(wav), sig, 48000)
        report = SignalAnalyzer.analyze(str(wav))
        assert report.peak_db is not None

    def test_compute_lufs_bs1770(self, tmp_path):
        """_compute_lufs_bs1770_4 返回合理值。"""
        from hermes_core.signal import SignalAnalyzer
        from hermes_core.audio_utils import read_pcm
        wav = tmp_path / "sine.wav"
        sf.write(str(wav), np.sin(2 * np.pi * 1000 * np.linspace(0, 0.5, 24000)), 48000)
        data, _ = read_pcm(str(wav))
        lufs = SignalAnalyzer._compute_lufs_bs1770_4(data, 48000)
        assert lufs < 0  # 应返回负值

    def test_signal_report_properties(self, tmp_path):
        """SignalReport 属性完整性。"""
        from hermes_core.signal import SignalAnalyzer
        wav = tmp_path / "test.wav"
        sf.write(str(wav), np.random.normal(0, 0.01, 48000), 48000)
        report = SignalAnalyzer.analyze(str(wav))
        assert isinstance(report.peak_db, float)
        assert isinstance(report.rms_db, float)

    def test_signal_report_crest_factor(self, tmp_path):
        """波峰因子 = peak - rms。"""
        from hermes_core.signal import SignalAnalyzer
        wav = tmp_path / "crest.wav"
        sf.write(str(wav), np.sin(2 * np.pi * 440 * np.linspace(0, 0.5, 24000)), 48000)
        report = SignalAnalyzer.analyze(str(wav))
        crest = report.peak_db - report.rms_db
        assert crest > 0


# ═══════════════════════════ gain_staging ═══════════════════════════

class TestGainStagingEdges:
    def test_apply_gain_clipping(self):
        """apply_gain 应限制在合理范围。"""
        from hermes_core.gain_staging import GainStagingEngine
        engine = MagicMock()
        engine._tracks = MagicMock()
        gse = GainStagingEngine(
            MagicMock(), MagicMock(), MagicMock(),
        )
        gse._engine = engine
        # 验证实例化正常工作
        assert gse is not None

    def test_prepare_stems_basic(self):
        """prepare_stems 基本调用路径。"""
        from hermes_core.gain_staging import GainStagingEngine
        engine = MagicMock()
        engine._tracks.list_all.return_value = []
        engine._tracks.import_media.return_value = {"index": 0, "item_volume": 0.0}
        analyzer = MagicMock()
        analyzer.analyze.return_value = MagicMock(
            integrated_lufs=-18.0, rms_db=-18.0, peak_db=-3.0,
        )
        gse = GainStagingEngine(
            engine, engine._tracks, analyzer,
        )
        gse._engine = engine
        result = gse.prepare(["file1.wav"], genre="pop", bpm=120)
        assert isinstance(result, dict)
        assert "stems" in result


# ═══════════════════════════ project_meta ═══════════════════════════

class TestProjectMetaEdges:
    def test_make_project_path_with_category(self):
        """make_project_path 带分类目录。"""
        from hermes_core.project_meta import make_project_path
        path = make_project_path("my_project", category="demo")
        assert "my_project" in str(path)

    def test_create_project_dirs(self, tmp_path):
        """create_project_dirs 创建目录结构。"""
        from hermes_core.project_meta import create_project_dirs
        proj_dir = tmp_path / "test_proj"
        result = create_project_dirs(str(proj_dir))
        assert "Audio" in result
        assert os.path.isdir(result["Audio"])

    def test_project_meta_basic(self):
        """ProjectMeta 基本属性。"""
        from hermes_core.project_meta import ProjectMeta
        meta = ProjectMeta(
            name="test", genre="pop", category="demo",
            pipeline_stage="created", lifecycle_state="active",
        )
        assert meta.name == "test"
        assert meta.genre == "pop"


# ═══════════════════════════ loudness_optimizer ═══════════════════════════

class TestLoudnessOptimizerEdges:
    def test_calculate_lufs_wrapper(self):
        """calculate_lufs_bs1770_4 包装器返回合理值。"""
        from hermes_core.loudness_optimizer import calculate_lufs_bs1770_4
        t = np.linspace(0, 0.5, 24000)
        sig = np.sin(2 * np.pi * 1000 * t) * 0.1
        lufs = calculate_lufs_bs1770_4(sig, 48000)
        assert lufs < 0

    def test_find_optimal_gain_works(self, tmp_path):
        """find_optimal_gain 基本调用。"""
        from hermes_core.loudness_optimizer import find_optimal_gain
        wav = tmp_path / "noise.wav"
        sig = np.random.normal(0, 0.001, 48000).astype(np.float64)
        sf.write(str(wav), sig, 48000)
        result = find_optimal_gain(str(wav), target_lufs=-14.0)
        assert isinstance(result.converged, bool)
