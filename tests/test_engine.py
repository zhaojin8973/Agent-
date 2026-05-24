"""Tests for hermes_core.engine — MixingEngine unit tests with mocked bridge."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.engine import MixingEngine
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer, SignalReport
from hermes_core.normalize import NormalizeResult


@pytest.mark.unit
class TestConstruction:
    def test_all_managers_initialized(self):
        eng = MixingEngine()
        assert isinstance(eng._tracks, TrackManager)
        assert isinstance(eng._bus, BusManager)
        assert isinstance(eng._fx, FxManager)
        assert isinstance(eng._send, SendManager)
        assert isinstance(eng._render, RenderManager)


@pytest.mark.unit
class TestContextManager:
    def test_enter_calls_connect_and_returns_self(self):
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        result = eng.__enter__()
        assert result is eng
        eng._bridge.connect.assert_called_once()

    def test_enter_raises_on_failure(self):
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=False)
        with pytest.raises(ConnectionError, match="REAPER"):
            eng.__enter__()

    def test_exit_does_not_crash(self):
        eng = MixingEngine()
        eng.__exit__(None, None, None)


@pytest.mark.unit
class TestHealthCheck:
    def test_delegates_to_bridge(self):
        eng = MixingEngine()
        eng._bridge.health_check = MagicMock(return_value={"reapy_connected": True})
        result = eng.health_check()
        assert result == {"reapy_connected": True}


@pytest.mark.unit
class TestCreateProject:
    def test_deletes_all_tracks(self):
        eng = MixingEngine()
        eng._bridge.api.CountTracks = MagicMock(return_value=5)
        eng._bridge.api.GetTrack = MagicMock(return_value="(MediaTrack*)0x1")
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.Undo_BeginBlock = MagicMock()
        eng._bridge.api.Undo_EndBlock = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock()

        eng.create_project(sample_rate=48000)

        assert eng._bridge.api.DeleteTrack.call_count == 5
        eng._bridge.api.Undo_BeginBlock.assert_called_once()
        eng._bridge.api.GetSetProjectInfo.assert_called()

    def test_skips_null_tracks(self):
        eng = MixingEngine()
        eng._bridge.api.CountTracks = MagicMock(return_value=3)
        eng._bridge.api.GetTrack = MagicMock(
            side_effect=["(MediaTrack*)0x1", None, "(MediaTrack*)0x3"]
        )
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.Undo_BeginBlock = MagicMock()
        eng._bridge.api.Undo_EndBlock = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock()

        eng.create_project(sample_rate=48000)
        assert eng._bridge.api.DeleteTrack.call_count == 2


@pytest.mark.unit
class TestImportStems:
    def test_creates_track_per_file(self):
        eng = MixingEngine()
        eng._tracks.create = MagicMock(side_effect=[0, 1, 2])
        eng._tracks.import_media = MagicMock(return_value=True)

        paths = ["/tmp/a.wav", "/tmp/b.wav", "/tmp/c.wav"]
        result = eng.import_stems(paths)

        assert len(result) == 3
        assert eng._tracks.create.call_count == 3
        assert eng._tracks.import_media.call_count == 3
        assert all(r["success"] for r in result)

    def test_tracks_import_failures(self):
        eng = MixingEngine()
        eng._tracks.create = MagicMock(side_effect=[0, 1])
        eng._tracks.import_media = MagicMock(side_effect=[True, False])

        result = eng.import_stems(["/tmp/a.wav", "/tmp/b.wav"])
        assert result[0]["success"] is True
        assert result[1]["success"] is False


@pytest.mark.unit
class TestListTracks:
    def test_delegates_to_track_manager(self):
        eng = MixingEngine()
        t = TrackInfo(index=0, name="T", volume_db=0, pan=0, mute=False,
                       solo=False, fx_count=0, depth=0, item_count=0, selected=False)
        eng._tracks.list_all = MagicMock(return_value=[t])
        result = eng.list_tracks()
        assert len(result) == 1


@pytest.mark.unit
class TestApplyGain:
    def test_track_fader_default(self):
        eng = MixingEngine()
        eng._tracks.set_volume = MagicMock()
        eng.apply_gain(3, -3.0)
        eng._tracks.set_volume.assert_called_once_with(3, -3.0)

    def test_clip_gain_raises_not_implemented(self):
        eng = MixingEngine()
        with pytest.raises(NotImplementedError, match="clip_gain"):
            eng.apply_gain(0, -6.0, target="clip_gain")

    def test_master_fader_raises_not_implemented(self):
        eng = MixingEngine()
        with pytest.raises(NotImplementedError, match="master_fader"):
            eng.apply_gain(0, 0, target="master_fader")

    def test_invalid_target_raises(self):
        eng = MixingEngine()
        with pytest.raises(ValueError, match="Unknown gain target"):
            eng.apply_gain(0, 0, target="aux_send")


@pytest.mark.unit
class TestGetGainStructure:
    def test_returns_formatted_dict(self):
        eng = MixingEngine()
        t = TrackInfo(index=0, name="Bass", volume_db=-2.0, pan=0, mute=False,
                       solo=False, fx_count=0, depth=0, item_count=1, selected=False)
        eng._tracks.list_all = MagicMock(return_value=[t])
        result = eng.get_gain_structure()
        assert "tracks" in result
        assert result["tracks"][0]["volume_db"] == -2.0


@pytest.mark.unit
class TestCheckHeadroom:
    def test_returns_unavailable_without_render(self):
        eng = MixingEngine()
        result = eng.check_headroom()
        assert result["source"] == "unavailable_without_render"
        assert result["headroom_dbtp"] is None


@pytest.mark.unit
class TestFx:
    def test_add_fx_delegates(self):
        eng = MixingEngine()
        eng._fx.add = MagicMock(return_value=0)
        assert eng.add_fx(1, "ReaEQ") == 0
        eng._fx.add.assert_called_once_with(1, "ReaEQ")

    def test_get_fx_chain_delegates(self):
        eng = MixingEngine()
        eng._fx.get_chain = MagicMock(return_value=[{"name": "ReaEQ"}])
        assert len(eng.get_fx_chain(1)) == 1


@pytest.mark.unit
class TestCreateBus:
    def test_delegates_to_bus_manager(self):
        eng = MixingEngine()
        eng._bus.create_bus = MagicMock(return_value=2)
        assert eng.create_bus("Drum Bus", [0, 1]) == 2
        eng._bus.create_bus.assert_called_once_with("Drum Bus", [0, 1])


@pytest.mark.unit
class TestCreateReverbSend:
    def test_creates_aux_track_fx_and_send(self):
        eng = MixingEngine()
        eng._tracks.create = MagicMock(return_value=7)
        eng._fx.add = MagicMock(return_value=0)
        eng._send.create = MagicMock(return_value={"index": 0, "category": 0})

        result = eng.create_reverb_send(src_track=3, level_db=-6.0)

        eng._tracks.create.assert_called_once_with(name="Verb Return")
        eng._fx.add.assert_called_once_with(7, "ReaVerbate")
        eng._send.create.assert_called_once_with(
            src=3, dest=7, level_db=-6.0, mode="post-fader"
        )
        assert result["aux_index"] == 7
        assert result["send"]["index"] == 0
        assert result["fx_index"] == 0


@pytest.mark.unit
class TestRenderMix:
    def test_delegates_to_render_manager(self):
        eng = MixingEngine()
        eng._render.render_mix = MagicMock(
            return_value={"output_path": "/tmp/out/render.wav"}
        )

        result = eng.render_mix("/tmp/out", verify=False)

        eng._render.render_mix.assert_called_once_with(
            output_dir="/tmp/out", bounds="entire_project",
            fmt="wav", sample_rate=0, timeout=120.0,
        )
        assert result["output_path"] == "/tmp/out/render.wav"

    def test_with_signal_check(self, tmp_path):
        eng = MixingEngine()
        eng._render.render_mix = MagicMock(
            return_value={"output_path": str(tmp_path / "render.wav")}
        )

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-12, peak_db=-6, integrated_lufs=-14, true_peak_dbtp=-5,
                clip_count=0, clip_passed=True, silence_passed=True,
                duration_sec=1.0, sample_rate=48000,
            ),
        ):
            result = eng.render_mix(str(tmp_path), verify=True)

        assert "signal_check" in result
        assert result["signal_check"]["integrated_lufs"] == -14

    def test_signal_check_handles_error(self):
        eng = MixingEngine()
        eng._render.render_mix = MagicMock(
            return_value={"output_path": "/tmp/bad.wav"}
        )

        with patch.object(SignalAnalyzer, "analyze", side_effect=RuntimeError("bad")):
            result = eng.render_mix("/tmp", verify=True)

        assert "signal_check" in result
        assert "error" in result["signal_check"]

    def test_skips_signal_check_when_render_fails(self):
        eng = MixingEngine()
        eng._render.render_mix = MagicMock(
            return_value={"output_path": None, "error": "timeout"}
        )

        with patch.object(SignalAnalyzer, "analyze") as mock_a:
            result = eng.render_mix("/tmp", verify=True)

        mock_a.assert_not_called()
        assert "signal_check" not in result


@pytest.mark.unit
class TestAuditMix:
    def test_passes_clean_audio(self, tmp_path):
        eng = MixingEngine()

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-12, peak_db=-6, integrated_lufs=-14, true_peak_dbtp=-5,
                clip_count=0, clip_passed=True, silence_passed=True,
                duration_sec=1.0, sample_rate=48000,
            ),
        ):
            result = eng.audit_mix(str(tmp_path / "mix.wav"))

        assert result["passed"] is True
        assert any(c["check_name"] == "all_clear" for c in result["checks"])

    def test_detects_clipping(self, tmp_path):
        eng = MixingEngine()

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-6, peak_db=-0.1, integrated_lufs=-8, true_peak_dbtp=0.5,
                clip_count=42, clip_passed=False, silence_passed=True,
                duration_sec=1.0, sample_rate=48000,
            ),
        ):
            result = eng.audit_mix(str(tmp_path / "mix.wav"))

        assert result["passed"] is False
        assert any(c["severity"] == "critical" for c in result["checks"])

    def test_detects_silence(self, tmp_path):
        eng = MixingEngine()

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-120, peak_db=-120, integrated_lufs=-120,
                true_peak_dbtp=-120, clip_count=0, clip_passed=True,
                silence_passed=False, duration_sec=1.0, sample_rate=48000,
            ),
        ):
            result = eng.audit_mix(str(tmp_path / "silence.wav"))
        assert result["passed"] is False

    def test_warns_on_near_ceiling_true_peak(self, tmp_path):
        eng = MixingEngine()

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-10, peak_db=-5, integrated_lufs=-12, true_peak_dbtp=-0.5,
                clip_count=0, clip_passed=True, silence_passed=True,
                duration_sec=1.0, sample_rate=48000,
            ),
        ):
            result = eng.audit_mix(str(tmp_path / "mix.wav"))

        assert result["passed"] is True
        assert any(
            c["check_name"] == "true_peak" and c["severity"] == "info"
            for c in result["checks"]
        )

    def test_handles_file_error(self):
        eng = MixingEngine()

        with patch.object(SignalAnalyzer, "analyze", side_effect=FileNotFoundError):
            result = eng.audit_mix("/nonexistent.wav")

        assert result["passed"] is False
        assert "error" in result


@pytest.mark.unit
class TestNormalizeTrack:
    def test_delegates_to_normalizer(self):
        eng = MixingEngine()
        eng._normalizer.normalize_track = MagicMock(
            return_value=NormalizeResult(
                track_index=0, track_name="Kick",
                original_lufs=-20.0, target_lufs=-14.0,
                gain_applied_db=6.0, success=True,
            )
        )
        result = eng.normalize_track(0, target_lufs=-14.0, duration=5.0)
        eng._normalizer.normalize_track.assert_called_once_with(
            0, target_lufs=-14.0, duration=5.0
        )
        assert result.success is True
        assert result.gain_applied_db == 6.0

    def test_normalize_track_defaults(self):
        eng = MixingEngine()
        eng._normalizer.normalize_track = MagicMock(
            return_value=NormalizeResult(
                track_index=0, track_name="",
                original_lufs=0, target_lufs=-14.0,
                gain_applied_db=0, success=False,
            )
        )
        eng.normalize_track(0)
        eng._normalizer.normalize_track.assert_called_once_with(
            0, target_lufs=-14.0, duration=5.0
        )


@pytest.mark.unit
class TestNormalizeAll:
    def test_delegates_to_normalizer(self):
        eng = MixingEngine()
        eng._normalizer.normalize_all = MagicMock(return_value=[])
        result = eng.normalize_all(target_lufs=-16.0, duration=3.0)
        eng._normalizer.normalize_all.assert_called_once_with(
            target_lufs=-16.0, duration=3.0
        )
        assert result == []


@pytest.mark.unit
class TestFullPipeline:
    def test_mock_pipeline(self, tmp_path):
        eng = MixingEngine()
        eng._bridge.api.CountTracks = MagicMock(return_value=5)
        eng._bridge.api.GetTrack = MagicMock(return_value="(MediaTrack*)0x1")
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.Undo_BeginBlock = MagicMock()
        eng._bridge.api.Undo_EndBlock = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock()

        eng._tracks.create = MagicMock(side_effect=range(10))
        eng._tracks.import_media = MagicMock(return_value=True)
        eng._tracks.set_volume = MagicMock()
        t0 = TrackInfo(index=0, name="Kick", volume_db=0, pan=0, mute=False,
                        solo=False, fx_count=0, depth=0, item_count=1, selected=False)
        eng._tracks.list_all = MagicMock(return_value=[t0])

        eng._bus.create_bus = MagicMock(return_value=0)
        eng._send.create = MagicMock(return_value={"index": 0, "category": 0})
        eng._fx.add = MagicMock(return_value=0)
        eng._render.render_mix = MagicMock(
            return_value={"output_path": str(tmp_path / "render.wav")}
        )

        with patch.object(
            SignalAnalyzer, "analyze",
            return_value=SignalReport(
                rms_db=-12, peak_db=-6, integrated_lufs=-14, true_peak_dbtp=-5,
                clip_count=0, clip_passed=True, silence_passed=True,
                duration_sec=1.0, sample_rate=48000,
            ),
        ):
            eng.create_project()
            imported = eng.import_stems(["/tmp/kick.wav", "/tmp/snare.wav"])
            assert len(imported) == 2

            eng.apply_gain(0, -3.0)
            eng.create_bus("Drum Bus", [0, 1])
            eng.create_reverb_send(1, level_db=-10.0)

            result = eng.render_mix(str(tmp_path))
            assert result["signal_check"]["silence_passed"]

            audit = eng.audit_mix(result["output_path"])
            assert audit["passed"]
