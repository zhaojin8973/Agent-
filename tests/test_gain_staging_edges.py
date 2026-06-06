"""gain_staging 边缘情况测试 — 纯函数覆盖。"""
from unittest.mock import MagicMock
import numpy as np
from hermes_core.gain_staging import GainStagingEngine


class TestGainStagingPrepare:
    def test_prepare_basic(self):
        engine = MagicMock()
        engine._tracks.list_all.return_value = []
        engine._tracks.import_media.return_value = {"index": 0, "item_volume": 0.0}
        analyzer = MagicMock()
        analyzer.analyze.return_value = MagicMock(
            integrated_lufs=-18.0, rms_db=-18.0, peak_db=-3.0,
        )
        gse = GainStagingEngine(engine._tracks, engine._tracks, analyzer)
        gse._engine = engine
        result = gse.prepare(["file.wav"], genre="pop", bpm=120)
        assert len(result["stems"]) == 1

    def test_prepare_with_vocal_boost(self):
        engine = MagicMock()
        engine._tracks.list_all.return_value = []
        engine._tracks.import_media.return_value = {"index": 0, "item_volume": 0.0}
        engine._tracks.set_volume.return_value = True
        analyzer = MagicMock()
        analyzer.analyze.return_value = MagicMock(
            integrated_lufs=-18.0, rms_db=-18.0, peak_db=-3.0,
        )
        gse = GainStagingEngine(engine._tracks, engine._tracks, analyzer)
        gse._engine = engine
        result = gse.prepare(
            ["vocal.wav"], genre="pop", bpm=120,
            vocal_indices=[0], backing_indices=[],
        )
        assert len(result["stems"]) == 1

    def test_import_stems(self):
        engine = MagicMock()
        engine._tracks.list_all.return_value = []
        engine._tracks.import_media.return_value = {"index": 0, "item_volume": 0.0}
        gse = GainStagingEngine(engine._tracks, MagicMock(), MagicMock())
        gse._engine = engine
        result = gse.import_stems(["file.wav"])
        assert len(result) == 1
