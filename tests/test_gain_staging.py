"""GainStagingEngine 单元测试。"""

import os
import pytest
from unittest.mock import MagicMock, patch

from hermes_core.gain_staging import GainStagingEngine


@pytest.fixture
def mock_bridge():
    bridge = MagicMock()
    bridge.api.CountTracks.return_value = 0
    return bridge


@pytest.fixture
def mock_tracks():
    tm = MagicMock()
    tm.create.return_value = 0
    tm.import_media.return_value = True
    return tm


@pytest.fixture
def mock_signal():
    sa = MagicMock()
    report = MagicMock()
    report.rms_db = -18.0
    report.integrated_lufs = -20.0
    report.peak_db = -3.0
    sa.analyze.return_value = report
    return sa


@pytest.fixture
def engine(mock_bridge, mock_tracks, mock_signal):
    return GainStagingEngine(mock_bridge, mock_tracks, mock_signal)


class TestClassifyRole:
    def test_vocal_classified(self):
        assert GainStagingEngine.classify_role(0, [0], [1]) == "vocal"

    def test_backing_classified(self):
        assert GainStagingEngine.classify_role(1, [0], [1]) == "backing"

    def test_other_classified(self):
        assert GainStagingEngine.classify_role(5, [0], [1]) == "other"


class TestImportStems:
    def test_import_single_stem(self, engine):
        result = engine.import_stems(["/tmp/test.wav"])
        assert len(result) == 1
        assert isinstance(result[0], dict)
        assert result[0].get("track_index") == 0

    def test_import_multiple_stems(self, engine):
        engine._tracks.create.side_effect = [0, 1, 2]
        engine._tracks.import_media.return_value = True
        result = engine.import_stems(["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"])
        assert len(result) == 3
        assert result[0]["track_index"] == 0
        assert result[2]["track_index"] == 2


class TestApplyGain:
    def test_apply_track_fader(self, engine):
        engine.apply_gain(0, -3.0, target="track_fader")
        engine._tracks.set_volume.assert_called_once_with(0, -3.0)

    def test_apply_clip_gain(self, engine):
        engine.apply_gain(0, 2.5, target="clip_gain")
        engine._tracks.set_item_volume.assert_called_once_with(0, 2.5)

    def test_apply_default_target(self, engine):
        engine.apply_gain(0, -1.5)
        engine._tracks.set_volume.assert_called_once_with(0, -1.5)


class TestPrepare:
    def test_prepare_single_stem(self, engine, tmp_path):
        with patch("hermes_core.gain_staging.SignalAnalyzer", engine._signal):
            result = engine.prepare(
                [os.path.join(str(tmp_path), "test.wav")],
                genre="pop", vocal_indices=[0],
            )
        stems = result["stems"]
        assert len(stems) == 1
        assert stems[0]["role"] == "vocal"
        assert result["genre"] == "pop"

    def test_prepare_applies_clip_gain(self, engine, tmp_path):
        report = MagicMock()
        report.rms_db = -6.0
        report.integrated_lufs = -8.0
        report.peak_db = -3.0
        engine._signal.analyze.return_value = report

        with patch("hermes_core.gain_staging.SignalAnalyzer", engine._signal):
            result = engine.prepare(
                [os.path.join(str(tmp_path), "loud.wav")],
                genre="pop", vocal_indices=[0],
            )
        s = result["stems"][0]
        assert s["clip_gain_db"] < 0, "过响分轨应被衰减"


class TestBalanceFaders:
    def test_balance_vocal_stays_at_zero(self, engine):
        stems = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": -20.0},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": -25.0},
        ]
        result = engine._balance_faders(stems, vocal_indices=[0],
                                        backing_indices=[1], genre="pop")
        assert stems[0]["fader_gain_db"] == 0.0
        assert result["ratio_lu"] > 0

    def test_backing_attenuated_below_vocal(self, engine):
        stems = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": -20.0},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": -18.0},
        ]
        engine._balance_faders(stems, vocal_indices=[0],
                               backing_indices=[1], genre="pop")
        assert stems[1]["fader_gain_db"] < 0

    def test_no_lufs_data_skips_gain(self, engine):
        stems = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": None},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": None},
        ]
        engine._balance_faders(stems, vocal_indices=[0], backing_indices=[1])
        engine._tracks.set_volume.assert_not_called()

    def test_different_genre_ratios(self, engine):
        stems1 = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": -20.0},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": -18.0},
        ]
        r1 = engine._balance_faders(
            [dict(s) for s in stems1],
            vocal_indices=[0], backing_indices=[1], genre="pop",
        )
        r2 = engine._balance_faders(
            [dict(s) for s in stems1],
            vocal_indices=[0], backing_indices=[1], genre="ballad",
        )
        assert r1["ratio_lu"] != r2["ratio_lu"]
