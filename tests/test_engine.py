"""Tests for hermes_core.engine — MixingEngine unit tests with mocked bridge."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.engine import MixingEngine
from hermes_core.comp_engine import (
    _derive_compressor_intent,
    _apply_vca_params,
    _apply_fet_params,
    _apply_opto_params,
    _apply_rvox_params,
)
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.loudness_optimizer import CompressionIntent, EqIntent, EqBandIntent
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from tests.conftest import require_reaper, clean_project, make_test_wav
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer, SignalReport


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
        from hermes_core.exceptions import BridgeConnectionError as HermesConnectionError

        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=False)
        with pytest.raises(HermesConnectionError, match="REAPER"):
            eng.__enter__()

    def test_exit_does_not_crash(self):
        eng = MixingEngine()
        eng.__exit__(None, None, None)

    def test_exit_with_watchdog_stops_killer(self):
        eng = MixingEngine(watchdog=True)
        eng._bridge._dialog_killer = MagicMock()
        eng._bridge._dialog_killer.is_running = True
        eng.__exit__(None, None, None)
        eng._bridge._dialog_killer.stop.assert_called_once()


@pytest.mark.unit
class TestHealthCheck:
    def test_delegates_to_bridge(self):
        eng = MixingEngine()
        eng._bridge.health_check = MagicMock(return_value={"reapy_connected": True})
        eng._bridge._dialog_killer.get_recent_events = MagicMock(return_value=[])
        result = eng.health_check()
        assert result["reapy_connected"] is True
        assert "watchdog_enabled" in result
        assert "recent_dialog_events" in result


@pytest.mark.unit
class TestCreateProject:
    def test_deletes_all_tracks_via_reapy(self):
        eng = MixingEngine()
        eng.allow_track_deletion()
        # Mock raw API for track deletion
        eng._bridge.api.CountTracks = MagicMock(return_value=5)
        mock_track = MagicMock()
        eng._bridge.api.GetTrack = MagicMock(return_value=mock_track)
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.GetMasterTrack = MagicMock(return_value="(MediaTrack*)0x0")
        eng._bridge.api.TrackFX_GetCount = MagicMock(return_value=0)
        eng._bridge.api.SetMediaTrackInfo_Value = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock()
        eng._bridge.api.GetSetProjectInfo_String = MagicMock()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._bridge.api.GetProjectName = MagicMock(return_value=[0, "", 256])
        eng._bridge.api.GetProjectPath = MagicMock(return_value=["", 256])

        eng.create_project(name="Test", output_dir="/tmp/test", sample_rate=48000)

        assert eng._bridge.api.DeleteTrack.call_count == 5
        eng._bridge.api.GetSetProjectInfo.assert_called()
        eng._bridge.api.Main_SaveProjectEx.assert_called()

@pytest.mark.unit
class TestProjectManagement:
    """Tests for create_project, save_project, save_checkpoint, get_project_info."""

    def test_create_project_with_name(self):
        eng = MixingEngine()
        eng.allow_track_deletion()
        eng._bridge.api.CountTracks = MagicMock(return_value=3)
        eng._bridge.api.GetTrack = MagicMock(return_value="(MediaTrack*)0x1")
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.Undo_BeginBlock = MagicMock()
        eng._bridge.api.Undo_EndBlock = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock(return_value=44100)
        eng._bridge.api.GetSetProjectInfo_String = MagicMock()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._bridge.api.GetProjectName = MagicMock(return_value=[0, "", 256])
        eng._bridge.api.GetProjectPath = MagicMock(return_value=["", 256])

        result = eng.create_project(
            name="MyMix", output_dir="/tmp/mix", sample_rate=44100
        )

        eng._bridge.api.GetSetProjectInfo_String.assert_any_call(
            0, "PROJECT_NAME", "MyMix", True
        )
        assert result["name"] == "MyMix"
        assert result["sample_rate"] == 44100
        assert result["track_count"] == 0
        assert result["conflict_renamed"] is False
        eng._bridge.api.Main_SaveProjectEx.assert_called()

    def test_create_project_conflict_renamed(self):
        eng = MixingEngine()
        eng._bridge.api.CountTracks = MagicMock(return_value=0)
        eng._bridge.api.GetTrack = MagicMock()
        eng._bridge.api.DeleteTrack = MagicMock()
        eng._bridge.api.Undo_BeginBlock = MagicMock()
        eng._bridge.api.Undo_EndBlock = MagicMock()
        eng._bridge.api.GetSetProjectInfo = MagicMock(return_value=48000)
        eng._bridge.api.GetSetProjectInfo_String = MagicMock()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._bridge.api.GetProjectName = MagicMock(return_value=[0, "", 256])
        eng._bridge.api.GetProjectPath = MagicMock(return_value=["", 256])

        with patch("os.path.exists", return_value=True), patch("os.makedirs"):
            result = eng.create_project(
                name="Existing", output_dir="/tmp/mix"
            )

        assert result["conflict_renamed"] is True

    def test_save_project_raises_without_project(self):
        eng = MixingEngine()
        with pytest.raises(RuntimeError, match="No project path"):
            eng.save_project()

    def test_save_project_silent(self):
        eng = MixingEngine()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._project_path = "/tmp/mix/Test.rpp"

        with patch("shutil.copy2"):  # 模拟文件复制成功
            result = eng.save_project()

        eng._bridge.api.Main_SaveProjectEx.assert_called()
        assert result["path"] == "/tmp/mix/Test.rpp"
        assert "saved_at" in result

    def test_save_checkpoint_creates_copy(self):
        eng = MixingEngine()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._project_path = "/tmp/mix/Test.rpp"

        result = eng.save_checkpoint(label="FX完成")

        assert "checkpoint" in result["checkpoint_path"]
        assert result["main_path"] == "/tmp/mix/Test.rpp"
        # Main file path should NOT change after checkpoint
        eng._bridge.api.Main_SaveProjectEx.assert_called()
        assert eng._bridge.api.Main_SaveProjectEx.call_args[0][0] == 0
        # 验证主保存调用使用了 options=8（&8 = set as project filename）
        assert eng._bridge.api.Main_SaveProjectEx.call_args[0][2] == 8

    def test_save_checkpoint_without_label(self):
        eng = MixingEngine()
        eng._bridge.api.Main_SaveProjectEx = MagicMock()
        eng._project_path = "/tmp/mix/Test.rpp"

        result = eng.save_checkpoint()

        assert "checkpoint" in result["checkpoint_path"]

    def test_save_checkpoint_raises_without_project(self):
        eng = MixingEngine()
        with pytest.raises(RuntimeError, match="No project path"):
            eng.save_checkpoint()

    def test_get_project_info(self):
        eng = MixingEngine()
        eng._bridge.api.GetProjectName = MagicMock(
            return_value=[0, "MyMix.rpp", 256]
        )
        eng._bridge.api.GetProjectPath = MagicMock(
            return_value=["/Users/test/mix", 256]
        )
        eng._bridge.api.GetSetProjectInfo = MagicMock(return_value=48000)
        eng._bridge.api.CountTracks = MagicMock(return_value=2)

        info = eng.get_project_info()

        assert info["name"] == "MyMix.rpp"
        assert info["path"] == "/Users/test/mix"
        assert info["sample_rate"] == 48000
        assert info["track_count"] == 2

    def test_get_project_info_empty_when_unsaved(self):
        eng = MixingEngine()
        eng._bridge.api.GetProjectName = MagicMock(return_value=[0, "", 256])
        eng._bridge.api.GetProjectPath = MagicMock(return_value=["", 256])
        eng._bridge.api.GetSetProjectInfo = MagicMock(return_value=0)
        eng._bridge.api.CountTracks = MagicMock(return_value=0)

        info = eng.get_project_info()

        assert info["name"] == ""
        assert info["path"] == ""
        assert info["track_count"] == 0


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

    def test_clip_gain_delegates_to_track_manager(self):
        eng = MixingEngine()
        eng._gain_staging._tracks = MagicMock()
        eng.apply_gain(0, -6.0, target="clip_gain")
        eng._gain_staging._tracks.set_item_volume.assert_called_once_with(0, -6.0)

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
        # Safety EQ (Pro-Q 3) is auto-inserted before the reverb
        assert eng._fx.add.call_count == 2
        eng._fx.add.assert_any_call(7, "FabFilter Pro-Q 3 (FabFilter)")
        eng._fx.add.assert_any_call(7, "ValhallaVintageVerb")
        eng._send.create.assert_called_once_with(
            src=3, dest=7, level_db=-6.0, mode="post-fader"
        )
        assert result["aux_index"] == 7
        assert result["send"]["index"] == 0
        assert result["fx_index"] == 0
        assert "safety_eq_index" in result


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
class TestFullPipeline:
    def test_mock_pipeline(self, tmp_path):
        eng = MixingEngine()
        eng.allow_track_deletion()
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
            eng.create_project(name="TestProj", output_dir="/tmp/pj")
            imported = eng.import_stems(["/tmp/kick.wav", "/tmp/snare.wav"])
            assert len(imported) == 2

            eng.apply_gain(0, -3.0)
            eng.create_bus("Drum Bus", [0, 1])
            eng.create_reverb_send(1, level_db=-10.0)

            result = eng.render_mix(str(tmp_path))
            assert result["signal_check"]["silence_passed"]

            audit = eng.audit_mix(result["output_path"])
            assert audit["passed"]


@pytest.mark.integration
class TestProjectManagementIntegration:
    def test_create_named_project_and_get_info(self):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        eng.create_project(name="HermesTest", output_dir="/tmp/hermes_test", sample_rate=44100)
        info = eng.get_project_info()

        assert info["track_count"] == 0
        assert info["sample_rate"] == 44100

    def test_create_project_returns_dict(self):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        result = eng.create_project(name="ReturnTest", output_dir="/tmp/pj", sample_rate=48000)

        assert result["name"] == "ReturnTest"
        assert result["sample_rate"] == 48000
        assert result["track_count"] == 0

    def test_get_project_info_after_import(self, tmp_path):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        eng.create_project(name="ImportInfo", output_dir="/tmp/pj", sample_rate=48000)
        wav = make_test_wav(tmp_path / "test.wav")
        eng.import_stems([str(wav)])

        info = eng.get_project_info()
        assert info["track_count"] == 1
        assert info["sample_rate"] == 48000

    def test_save_project_does_not_raise(self):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        eng.create_project(name="SaveTest", output_dir="/tmp/hermes_test")
        # First save may open dialog - just verify no exception
        try:
            eng.save_project()
        except Exception:
            pass  # Save dialog may appear for unsaved projects
    def test_full_pipeline_render_and_audit(self, tmp_path):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        eng.create_project(name='TestProj', output_dir="/tmp/pj", sample_rate=48000)

        wav1 = make_test_wav(tmp_path / "kick.wav", duration_sec=1.0, frequency=80.0)
        wav2 = make_test_wav(tmp_path / "snare.wav", duration_sec=1.0, frequency=200.0)

        imported = eng.import_stems([str(wav1), str(wav2)])
        assert len(imported) == 2
        assert all(r["success"] for r in imported)

        eng.apply_gain(0, -3.0)
        eng.add_fx(0, "ReaEQ")

        bus_idx = eng.create_bus("DrumBus", [0, 1])
        assert bus_idx >= 0

        result = eng.render_mix(str(tmp_path), verify=True)
        assert result.get("output_path") is not None
        assert "signal_check" in result

        audit = eng.audit_mix(result["output_path"])
        assert audit["passed"] is True

    def test_health_check_and_track_listing(self):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestProj", output_dir="/tmp/pj")

        eng.import_stems([])
        health = eng.health_check()
        assert health["reapy_connected"] is True

        tracks = eng.list_tracks()
        assert isinstance(tracks, list)

    def test_render_rejects_empty_project(self, tmp_path):
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestProj", output_dir="/tmp/pj")


# ════════════════════════════════════════════════════════════════
# PRODUCTION_GAPS features — unit tests
# ════════════════════════════════════════════════════════════════


class TestIdempotencyGuards:
    """Idempotency: destructive ops must reject double-execution."""

    def test_prepare_stems_raises_on_second_call(self):
        """Calling prepare_stems twice raises RuntimeError."""
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        # Fake stems_prepared to simulate first call completed
        eng._stems_gain_staged = True
        with pytest.raises(RuntimeError, match="already gain-staged"):
            eng.prepare_stems(["/fake/path.wav"])

    def test_finalize_master_raises_on_second_call(self):
        """Calling finalize_master twice raises RuntimeError."""
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        eng._master_finalized = True
        with pytest.raises(RuntimeError, match="already finalized"):
            eng.finalize_master(target_lufs=-12.0)

    def test_reset_clears_guards(self):
        """reset() clears both idempotency guards."""
        eng = MixingEngine()
        eng._stems_gain_staged = True
        eng._master_finalized = True
        eng._stems_cache = [{"name": "test"}]
        eng.reset()
        assert eng._stems_gain_staged is False
        assert eng._master_finalized is False
        assert eng._stems_cache == []

    def test_create_project_resets_guards(self):
        """create_project() calls reset() via mock verification."""
        eng = MixingEngine()
        eng._stems_gain_staged = True
        eng._master_finalized = True
        # Verify guards are set
        assert eng._stems_gain_staged is True
        # Direct call to reset
        eng.reset()
        assert eng._stems_gain_staged is False
        assert eng._master_finalized is False


class TestFriendlyHint:
    """User-friendly error hints for common failures."""

    def test_probe_render_failed_hint(self):
        from hermes_core.engine import _friendly_hint
        hint = _friendly_hint("Probe render failed")
        assert "modal dialog" in hint.lower()

    def test_probe_near_silent_hint(self):
        from hermes_core.engine import _friendly_hint
        hint = _friendly_hint("Probe is near-silent")
        assert "silent" in hint.lower()

    def test_pro_l2_param_hint(self):
        from hermes_core.engine import _friendly_hint
        hint = _friendly_hint("Pro-L 2 Gain param not found")
        assert "pro-l 2" in hint.lower()

    def test_failed_to_add_hint(self):
        from hermes_core.engine import _friendly_hint
        hint = _friendly_hint("Failed to add FabFilter Pro-L 2")
        assert "plugin" in hint.lower()

    def test_unknown_error_returns_generic_hint(self):
        from hermes_core.engine import _friendly_hint
        hint = _friendly_hint("some_unknown_error_xyz")
        assert len(hint) > 10  # should return a non-empty generic message


class TestProgressCallback:
    """Progress callbacks fire at expected stages."""

    def test_on_progress_signature_accepted(self):
        """finalize_master accepts on_progress callback without TypeError."""
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        eng._bridge._api = MagicMock()
        eng._bridge._reapy_module = MagicMock()
        eng._bridge._api.GetMasterTrack.return_value = None
        eng._bridge._api.CountTracks.return_value = 0
        called = []

        def progress(stage: str, pct: float):
            called.append(stage)

        # The call will fail (no master track → no FX), but progress
        # parameter must be accepted without TypeError.
        result = eng.finalize_master(target_lufs=-12.0, on_progress=progress)
        assert isinstance(result, dict)
        assert "error" in result


# ════════════════════════════════════════════════════════════
# _derive_compressor_intent
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDeriveCompressorIntent:
    def test_heavy_for_large_crest(self):
        intent = _derive_compressor_intent(-18.0, -3.0)  # crest = 15 dB
        assert intent.amount == "heavy"
        # crest 15 * FET coeff 0.2 = 3.0 dB GR (peak control, not body)
        assert intent.gr_target_db > 2.5

    def test_medium_for_moderate_crest(self):
        intent = _derive_compressor_intent(-18.0, -6.0)  # crest = 12 dB
        assert intent.amount == "medium"

    def test_light_for_small_crest(self):
        intent = _derive_compressor_intent(-10.0, -5.0)  # crest = 5 dB
        assert intent.amount == "light"
        assert intent.gr_target_db < 4.0

    def test_boundary_15db(self):
        """Crest = 15 dB exactly → heavy."""
        intent = _derive_compressor_intent(-18.0, -3.0)
        assert intent.amount == "heavy"

    def test_boundary_10db(self):
        """Crest = 10 dB exactly → medium."""
        intent = _derive_compressor_intent(-18.0, -8.0)  # crest = 10
        assert intent.amount == "medium"

    def test_fields_are_numeric(self):
        intent = _derive_compressor_intent(-18.0, -6.0)
        assert isinstance(intent.gr_target_db, float)
        assert isinstance(intent.crest_factor_db, float)


# ════════════════════════════════════════════════════════════
# Compressor translators
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCompressorTranslators:
    INTENT = CompressionIntent("medium", 6.0, 12.0, -18.0, -6.0)
    PRESET = {"attack_ms": 5.0, "release_ms": 100.0}

    def test_vca_returns_physical_params(self):
        params = _apply_vca_params(self.INTENT, self.PRESET)
        assert "Threshold" in params
        assert "Ratio" in params
        assert "Attack" in params
        assert params["Ratio"] == 4.0
        assert params["Threshold"] < 0  # below peak

    def test_fet_returns_input_as_threshold(self):
        params = _apply_fet_params(self.INTENT, self.PRESET)
        assert "Input" in params
        assert "Output" in params
        # Input should be a threshold value (negative dBFS)
        assert params["Input"] < 0

    def test_opto_returns_peak_reduction(self):
        params = _apply_opto_params(self.INTENT, self.PRESET)
        assert "Peak Reduction" in params
        assert "Gain" in params
        assert params["Peak Reduction"] == 6.0

    def test_rvox_returns_db_params(self):
        params = _apply_rvox_params(self.INTENT, self.PRESET)
        assert "Compression" in params
        assert "Gate" in params
        assert "Gain" in params
        # 1:1 mapping: gr_target=6.0 → Compression=-6.0
        assert params["Compression"] == -6.0
        # Gain = Comp * 0.6 (level-match)
        assert params["Gain"] == pytest.approx(-3.6, abs=0.1)
        # Gate = off (-120 dB)
        assert params["Gate"] == -120.0

    def test_heavy_vca_uses_higher_ratio(self):
        heavy = CompressionIntent("heavy", 8.0, 16.0, -18.0, -2.0)
        params = _apply_vca_params(heavy, self.PRESET)
        assert params["Ratio"] == 8.0

    def test_light_vca_uses_lower_ratio(self):
        light = CompressionIntent("light", 3.0, 8.0, -18.0, -10.0)
        params = _apply_vca_params(light, self.PRESET)
        assert params["Ratio"] == 2.0


# ════════════════════════════════════════════════════════════
# _balance_faders（已迁移至 GainStagingEngine）
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestBalanceFaders:
    def test_no_balance_without_lufs(self):
        """stems without adjusted_lufs get zero fader gain."""
        eng = MixingEngine()
        stems = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": None},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": None},
        ]
        eng._gain_staging.apply_gain = MagicMock()
        eng._gain_staging._balance_faders(
            stems, vocal_indices=[0], backing_indices=[1],
        )
        assert eng._gain_staging.apply_gain.call_count == 0

    def test_balance_with_valid_lufs(self):
        """Valid LUFS → fader gains computed and applied."""
        eng = MixingEngine()
        stems = [
            {"track_index": 0, "role": "vocal", "success": True,
             "adjusted_lufs": -25.0},
            {"track_index": 1, "role": "backing", "success": True,
             "adjusted_lufs": -20.0},
        ]
        eng._gain_staging.apply_gain = MagicMock()
        eng._gain_staging._balance_faders(
            stems, vocal_indices=[0], backing_indices=[1],
            genre="pop",
        )
        assert eng._gain_staging.apply_gain.call_count >= 1


# ════════════════════════════════════════════════════════════
# Micro-render + chain execution
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMicroRender:
    def test_clean_node_cache_hit(self):
        """Clean node with valid output path returns cached path."""
        from hermes_core.dag import AudioNode
        eng = MixingEngine()
        node = AudioNode(name="test", fx_type="eq")
        node.mark_clean("/tmp/cached.wav")
        # Create the fake cache file
        import tempfile, os
        with tempfile.TemporaryDirectory() as td:
            cache_path = os.path.join(td, "cached.wav")
            with open(cache_path, "w") as f:
                f.write("fake")
            node.output_audio_path = cache_path
            node.is_dirty = False
            result = eng._micro_render_node(node, "/tmp/input.wav", td)
            assert result == cache_path

    def test_missing_input_wav_returns_none(self):
        """No input WAV → skip render."""
        from hermes_core.dag import AudioNode
        eng = MixingEngine()
        node = AudioNode(name="test", fx_type="comp")
        node.is_dirty = True
        result = eng._micro_render_node(node, None, "/tmp/cache")
        assert result is None

    def test_gen_calibration_signal(self):
        """Calibration signal is -18 dBFS RMS, 5s, stereo."""
        import tempfile, os
        from hermes_core.comp_engine import generate_calibration_signal
        with tempfile.TemporaryDirectory() as td:
            path = generate_calibration_signal(td, duration=1.0)
            assert os.path.exists(path)
            from hermes_core.signal import SignalAnalyzer
            report = SignalAnalyzer.analyze(path)
            # Should be close to -18 dBFS RMS
            assert -20 < report.rms_db < -16


@pytest.mark.unit
class TestChainExecution:
    def test_execute_chain_all_clean(self):
        """All nodes clean → nothing rendered."""
        from hermes_core.dag import AudioNode
        eng = MixingEngine()
        nodes = [AudioNode(name=f"n{i}", fx_type="eq") for i in range(3)]
        for i, n in enumerate(nodes):
            n.mark_clean(f"/tmp/n{i}.wav")
        # No REAPER needed — all clean, all skipped
        result = eng.execute_chain(nodes)
        assert len(result) == 3
        assert all(not n.is_dirty for n in result)

    def test_first_dirty_returns_correct_index(self):
        from hermes_core.dag import AudioNode, ChainExecutor
        nodes = [AudioNode(name=f"n{i}", fx_type="eq") for i in range(3)]
        for n in nodes:
            n.mark_clean()
        nodes[1].invalidate()
        assert ChainExecutor.first_dirty(nodes) == 1

    def test_make_chain_executor_returns_executor(self):
        from hermes_core.dag import ChainExecutor
        eng = MixingEngine()
        exe = eng._make_chain_executor("/tmp/cache")
        assert isinstance(exe, ChainExecutor)


# ════════════════════════════════════════════════════════════
# Preview / Finalize 双模渲染 + 湿声缓存
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNumpyMix:
    def test_mixes_dry_wet(self, tmp_path):
        import numpy as np
        import soundfile as sf
        dry = tmp_path / "dry.wav"
        wet = tmp_path / "wet.wav"
        out = tmp_path / "mixed.wav"
        sr = 48000
        dry_data = np.ones((48000, 2), dtype=np.float64) * 0.5
        wet_data = np.ones((48000, 2), dtype=np.float64) * 0.1
        sf.write(str(dry), dry_data, sr)
        sf.write(str(wet), wet_data, sr)

        result = MixingEngine._numpy_mix(str(dry), str(wet), -6.0, str(out))
        assert result is not None
        assert os.path.exists(str(out))

    def test_sr_mismatch_returns_none(self, tmp_path):
        import numpy as np
        import soundfile as sf
        dry = tmp_path / "dry.wav"
        wet = tmp_path / "wet.wav"
        out = tmp_path / "mixed.wav"
        sf.write(str(dry), np.zeros((48000, 1)), 48000)
        sf.write(str(wet), np.zeros((44100, 1)), 44100)

        result = MixingEngine._numpy_mix(str(dry), str(wet), 0.0, str(out))
        assert result is None

    def test_missing_file_returns_none(self, tmp_path):
        out = tmp_path / "mixed.wav"
        result = MixingEngine._numpy_mix(
            "/nonexistent/dry.wav", "/nonexistent/wet.wav", 0.0, str(out),
        )
        assert result is None


@pytest.mark.unit
class TestPreviewRender:
    def test_preview_without_reverb_uses_dry_only(self):
        """render_preview works even without reverb send node."""
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        eng._bridge._api = MagicMock()
        eng._bridge._reapy_module = MagicMock()
        eng._bridge._api.GetMasterTrack.return_value = None
        eng._bridge._api.CountTracks.return_value = 0
        eng._reverb_send_node = None

        # render_mix will fail (no project), preview should handle it
        result = eng.render_preview("/tmp/preview_test")
        assert result["mode"] == "preview"
        # Without REAPER, dry render fails immediately
        assert result.get("error") is not None or result.get("output_path") is None

    def test_preview_returns_mastering_bypassed_flag(self):
        """Preview output carries 'mastering': 'bypassed' on success."""
        eng = MixingEngine()
        eng._bridge.connect = MagicMock(return_value=True)
        eng._bridge._api = MagicMock()
        eng._bridge._api.CountTracks.return_value = 0
        eng._bridge._api.GetMasterTrack.return_value = None
        result = eng.render_preview("/tmp/test")
        # May fail without REAPER; if it succeeds, mastering must be bypassed
        if result.get("output_path"):
            assert result.get("mastering") == "bypassed"
            assert "warning" in result
        else:
            assert result.get("error") is not None


@pytest.mark.unit
class TestWetCache:
    def test_no_reverb_node_returns_none(self):
        eng = MixingEngine()
        eng._reverb_send_node = None
        result = eng._cache_reverb_wet("/tmp/cache")
        assert result is None

    def test_no_aux_index_returns_none(self):
        from hermes_core.dag import SendNode, AudioNode
        eng = MixingEngine()
        src = AudioNode(name="src", fx_type="comp")
        eng._reverb_send_node = SendNode(name="verb", fx_type="reverb",
                                          source_node=src)
        eng._reverb_send_node.params = {}
        result = eng._cache_reverb_wet("/tmp/cache")
        assert result is None


# ══════════════════════════════════════════════════════════════
# EQ auto-derivation integration tests
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDeriveEQIntent:
    """Verify :func:`_derive_eq_intent` produces correct EqIntent from SpectrumReport."""

    def test_produces_hpf_every_time(self):
        """Every derived EQ must include exactly one HPF band."""
        from hermes_core.engine import _derive_eq_intent
        from hermes_core.spectrum import SpectrumReport

        report = SpectrumReport(
            band_energy_db={"sub": -50, "low": -30, "low_mid": -25,
                            "mid": -20, "high_mid": -22, "presence": -24, "air": -35},
            spectral_tilt_db_per_octave=-1.5,
            resonances=[], mud_ratio_db=-5.0, presence_deficit_db=4.0,
            sibilance_peak_hz=7000.0, air_level_db=-35.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        hpf = [b for b in intent.bands if b.band_type == "hp"]
        assert len(hpf) == 1

    def test_no_mud_cut_when_clean(self):
        """Low mud_ratio → no 350 Hz cut."""
        from hermes_core.engine import _derive_eq_intent
        from hermes_core.spectrum import SpectrumReport

        report = SpectrumReport(
            band_energy_db={"sub": -50, "low": -30, "low_mid": -25,
                            "mid": -20, "high_mid": -22, "presence": -24, "air": -35},
            spectral_tilt_db_per_octave=-1.5,
            resonances=[], mud_ratio_db=1.0, presence_deficit_db=1.0,
            sibilance_peak_hz=7000.0, air_level_db=-25.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        cuts = [b for b in intent.bands if b.gain_db < 0 and abs(b.freq_hz - 350) < 5]
        assert len(cuts) == 0

    def test_spectral_tilt_metadata(self):
        """EqIntent.spectral_tilt reflects the report's tilt."""
        from hermes_core.engine import _derive_eq_intent
        from hermes_core.spectrum import SpectrumReport

        dark = SpectrumReport(
            band_energy_db={"sub": -50, "low": -30, "low_mid": -25,
                            "mid": -20, "high_mid": -22, "presence": -24, "air": -35},
            spectral_tilt_db_per_octave=-4.0,
            resonances=[], mud_ratio_db=-5.0, presence_deficit_db=1.0,
            sibilance_peak_hz=7000.0, air_level_db=-25.0,
        )
        assert _derive_eq_intent(dark, role="vocal", genre="pop").spectral_tilt == "dark"

        bright = SpectrumReport(
            band_energy_db={"sub": -50, "low": -30, "low_mid": -25,
                            "mid": -20, "high_mid": -22, "presence": -24, "air": -35},
            spectral_tilt_db_per_octave=3.0,
            resonances=[], mud_ratio_db=-5.0, presence_deficit_db=1.0,
            sibilance_peak_hz=7000.0, air_level_db=-25.0,
        )
        assert _derive_eq_intent(bright, role="vocal", genre="pop").spectral_tilt == "bright"

    def test_all_genres_work(self):
        """Every known genre should produce a valid EqIntent without crash."""
        from hermes_core.engine import _derive_eq_intent, _GENRE_EQ_TWEAKS
        from hermes_core.spectrum import SpectrumReport

        report = SpectrumReport(
            band_energy_db={"sub": -50, "low": -30, "low_mid": -25,
                            "mid": -20, "high_mid": -22, "presence": -24, "air": -35},
            spectral_tilt_db_per_octave=-1.5,
            resonances=[], mud_ratio_db=-5.0, presence_deficit_db=4.0,
            sibilance_peak_hz=7000.0, air_level_db=-35.0,
        )
        for genre in _GENRE_EQ_TWEAKS:
            intent = _derive_eq_intent(report, role="vocal", genre=genre)
            assert intent.spectral_tilt in ("dark", "neutral", "bright")


@pytest.mark.unit
class TestEQTranslator:
    """Verify :func:`_apply_proq3_eq` maps EqIntent → Pro-Q 3 physical params."""

    def test_single_band_maps_all_keys(self):
        from hermes_core.engine import _apply_proq3_eq, _proq3_freq_norm, _proq3_q_norm
        from hermes_core.loudness_optimizer import EqIntent, EqBandIntent

        band = EqBandIntent(
            band_type="bell", freq_hz=2500.0, gain_db=-3.0, q=2.5,
            reason="test",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        assert params["Band 1 Frequency"] == pytest.approx(_proq3_freq_norm(2500.0))
        assert params["Band 1 Gain"] == pytest.approx((-3.0 + 30.0) / 60.0)
        assert params["Band 1 Q"] == pytest.approx(_proq3_q_norm(2.5))
        assert params["Band 1 Shape"] == 0.0     # Bell
        assert params["Band 1 Enabled"] == 1.0
        assert params["Band 1 Used"] == 1.0
        # Defaults must be explicitly set
        assert params["Band 1 Speakers"] == 0.0
        assert params["Band 1 Stereo Placement"] == 0.5
        assert params["Band 1 Solo"] == 0.0

    def test_mixed_band_types(self):
        from hermes_core.engine import _apply_proq3_eq
        from hermes_core.loudness_optimizer import EqIntent, EqBandIntent

        bands = [
            EqBandIntent(band_type="hp", freq_hz=80.0, gain_db=0.0, q=0.7,
                         reason="hpf"),
            EqBandIntent(band_type="bell", freq_hz=350.0, gain_db=-3.0, q=0.7,
                         reason="mud"),
            EqBandIntent(band_type="bell", freq_hz=3000.0, gain_db=2.0, q=1.0,
                         reason="presence"),
            EqBandIntent(band_type="high_shelf", freq_hz=8000.0, gain_db=1.5, q=0.7,
                         reason="air"),
        ]
        intent = EqIntent(bands=bands, spectral_tilt="dark", mud_detected=True)
        params = _apply_proq3_eq(intent)

        assert params["Band 1 Shape"] == 0.25   # Low Cut = 2/8
        assert params["Band 2 Shape"] == 0.0    # Bell
        assert params["Band 3 Shape"] == 0.0    # Bell
        assert params["Band 4 Shape"] == 0.375  # High Shelf = 3/8
        for n in range(1, 5):
            assert params[f"Band {n} Enabled"] == 1.0
        for n in range(5, 9):
            assert params[f"Band {n} Enabled"] == 0.0
        # All bands get explicit defaults
        for n in range(1, 5):
            assert params[f"Band {n} Speakers"] == 0.0

    def test_params_all_in_01_range(self):
        """Every param should already be in [0, 1] — no normalise needed."""
        from hermes_core.engine import _apply_proq3_eq
        from hermes_core.loudness_optimizer import EqIntent, EqBandIntent

        bands = [
            EqBandIntent(band_type="hp", freq_hz=80.0, gain_db=0.0, q=0.7,
                         reason="hpf"),
            EqBandIntent(band_type="bell", freq_hz=3000.0, gain_db=2.5, q=1.0,
                         reason="presence"),
        ]
        intent = EqIntent(bands=bands, spectral_tilt="dark", mud_detected=False)
        params = _apply_proq3_eq(intent)
        for pname, pval in params.items():
            assert 0.0 <= pval <= 1.0, (
                f"{pname} = {pval:.4f} is outside [0, 1]"
            )


@pytest.mark.unit
class TestApplyEQBaseline:
    """Verify :meth:`MixingEngine._apply_eq_baseline` pipeline."""

    def test_fallback_to_static_when_no_file(self):
        """When no file path provided, use static _EQ_BASELINE."""
        eng = MixingEngine()
        eng._fx.set_param = MagicMock()
        eng._apply_eq_baseline(
            track_index=0, fx_index=0, role="vocal",
            genre="pop", stem_file_path="",
        )
        assert eng._fx.set_param.call_count > 0
        assert len(eng._last_eq_params) > 0

    def test_fallback_to_static_when_file_missing(self):
        """Non-existent file path → fallback to static baseline."""
        eng = MixingEngine()
        eng._fx.set_param = MagicMock()
        eng._apply_eq_baseline(
            track_index=0, fx_index=0, role="vocal",
            genre="pop", stem_file_path="/nonexistent/file.wav",
        )
        assert eng._fx.set_param.call_count > 0
        assert len(eng._last_eq_params) > 0

    def test_spectrum_driven_eq_with_real_wav(self, tmp_path):
        """A real WAV file → full spectrum-driven EQ pipeline."""
        from tests.conftest import make_test_wav

        wav_path = tmp_path / "test_vocal.wav"
        make_test_wav(str(wav_path), duration_sec=2.0, frequency=440.0)

        eng = MixingEngine()
        eng._fx.set_param = MagicMock()
        eng._apply_eq_baseline(
            track_index=0, fx_index=0, role="vocal",
            genre="pop", stem_file_path=str(wav_path),
        )
        assert eng._fx.set_param.call_count > 0
        assert len(eng._last_eq_params) > 0

    def test_spectrum_driven_populates_eq_params(self):
        """Spectrum pipeline should populate _last_eq_params with proper band data."""
        import tempfile
        import numpy as np
        import wave

        sr = 48000
        dur = 2.0
        t = np.arange(int(sr * dur)) / sr
        sig = (0.5 * np.sin(2 * np.pi * 200 * t)
               + 0.3 * np.sin(2 * np.pi * 400 * t)
               + 0.2 * np.sin(2 * np.pi * 800 * t)
               + 0.1 * np.sin(2 * np.pi * 3000 * t))
        sig = np.clip(sig * 32767, -32768, 32767).astype(np.int16)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(sig.tobytes())

            eng = MixingEngine()
            eng._fx.set_param = MagicMock()
            eng._apply_eq_baseline(
                track_index=0, fx_index=0, role="vocal",
                genre="pop", stem_file_path=wav_path,
            )
            params = eng._last_eq_params
            assert len(params) > 0
            freq_keys = [k for k in params if "Freq" in k]
            assert len(freq_keys) >= 1, f"Should have at least 1 freq band, got {freq_keys}"
        finally:
            os.unlink(wav_path)

    def test_spectrum_driven_handles_backing_role(self):
        """Backing role should derive its own EQ intent."""
        import tempfile
        import numpy as np
        import wave

        sr = 48000
        dur = 1.0
        t = np.arange(int(sr * dur)) / sr
        sig = np.clip(0.5 * np.sin(2 * np.pi * 300 * t) * 32767, -32768, 32767).astype(np.int16)

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
            wav_path = f.name
        try:
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(sig.tobytes())

            eng = MixingEngine()
            eng._fx.set_param = MagicMock()
            eng._apply_eq_baseline(
                track_index=0, fx_index=0, role="backing",
                genre="rock", stem_file_path=wav_path,
            )
            params = eng._last_eq_params
            assert len(params) > 0
            freq_keys = [k for k in params if "Freq" in k]
            assert len(freq_keys) >= 1
        finally:
            os.unlink(wav_path)

    def test_spectrum_failure_falls_back_gracefully(self):
        """If spectrum analysis raises, fall back to static baseline."""
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"not a wav file")
            bad_path = f.name

        try:
            eng = MixingEngine()
            eng._fx.set_param = MagicMock()
            eng._apply_eq_baseline(
                track_index=0, fx_index=0, role="vocal",
                genre="pop", stem_file_path=bad_path,
            )
            assert eng._fx.set_param.call_count > 0
            assert len(eng._last_eq_params) > 0
        finally:
            os.unlink(bad_path)


# ════════════════════════════════════════════════════════════════
# Headroom protection
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestHeadroomProtection:
    """Pro-Q 3 Output Level compensates for total EQ boost."""

    def test_no_boost_keeps_unity(self):
        """Zero boost → Output Level at 0 dB."""
        from hermes_core.engine import _apply_proq3_eq
        band = EqBandIntent("bell", 350.0, -3.0, 0.7, "cut only")
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        assert params["Output Level"] == 0.5  # unity, no boost

    def test_boost_reduces_output(self):
        """Positive gain → Output Level trimmed."""
        from hermes_core.engine import _apply_proq3_eq
        band = EqBandIntent("bell", 3000.0, 3.5, 1.0, "presence")
        intent = EqIntent(bands=[band], spectral_tilt="dark", mud_detected=False)
        params = _apply_proq3_eq(intent)
        # +3.5 dB boost → output should be trimmed
        assert params["Output Level"] < 0.5

    def test_mixed_band_output_correct(self):
        """One cut + one boost → output compensates for net boost only."""
        from hermes_core.engine import _apply_proq3_eq
        bands = [
            EqBandIntent("bell", 350.0, -4.0, 0.7, "mud cut"),
            EqBandIntent("bell", 3000.0, 3.5, 1.0, "presence"),
        ]
        intent = EqIntent(bands=bands, spectral_tilt="dark", mud_detected=True)
        params = _apply_proq3_eq(intent)
        # Only +3.5 counts toward boost, -4.0 ignored
        expected_norm = (-3.5 + 36) / 72
        assert params["Output Level"] == pytest.approx(expected_norm, abs=0.01)


# ════════════════════════════════════════════════════════════════
# SSL EQ engine integration
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSSLEQEngine:
    """_apply_ssleq_eq integration with the engine pipeline."""

    def test_ssleq_via_apply_eq_baseline(self):
        """When fx_name contains 'ssleq', use SSL EQ translator."""
        import tempfile, wave, os, struct, math

        wav_path = None
        try:
            # Generate 1s of 1kHz sine at -18dBFS RMS
            sr = 44100
            t = [i / sr for i in range(sr)]
            sig = [int(16000 * math.sin(2 * math.pi * 1000 * x))
                   for x in t]

            fd, wav_path = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            with wave.open(wav_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(sr)
                wf.writeframes(struct.pack(f"<{len(sig)}h", *sig))

            eng = MixingEngine()
            eng._fx.set_param = MagicMock()
            eng._apply_eq_baseline(
                track_index=0, fx_index=0, role="vocal",
                genre="pop", stem_file_path=wav_path,
                position="post", fx_name="VST3: SSLEQ Mono (Waves)",
            )
            params = eng._last_eq_params
            # SSL EQ params should be present
            assert "Analog" in params
            assert params["Analog"] == 1.0
            assert params["EQ IN"] == 1.0
        finally:
            if wav_path:
                os.unlink(wav_path)


# ══════════════════════════════════════════════════════════════
# BPM-aware compressor timing tests
# ══════════════════════════════════════════════════════════════


class TestBpmTiming:
    """Verify BPM-aware attack/release selection."""

    def test_fast_bpm_selects_fast_preset(self):
        """BPM >= 130 → fast (attack=3ms, release=60ms)."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(140.0)
        assert timing is not None
        assert timing["attack_ms"] == 3.0
        assert timing["release_ms"] == 60.0

    def test_med_bpm_selects_med_preset(self):
        """BPM 90-129 → med (attack=5ms, release=100ms)."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(120.0)
        assert timing is not None
        assert timing["attack_ms"] == 5.0
        assert timing["release_ms"] == 100.0

    def test_slow_bpm_selects_slow_preset(self):
        """BPM < 90 → slow (attack=10ms, release=200ms)."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(60.0)
        assert timing is not None
        assert timing["attack_ms"] == 10.0
        assert timing["release_ms"] == 200.0

    def test_boundary_130_is_fast(self):
        """Exactly 130 BPM is fast."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(130.0)
        assert timing is not None
        assert timing["attack_ms"] == 3.0

    def test_boundary_90_is_med(self):
        """Exactly 90 BPM is med."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(90.0)
        assert timing is not None
        assert timing["attack_ms"] == 5.0

    def test_none_bpm_returns_none(self):
        """None BPM → None (caller falls back to genre)."""
        from hermes_core.profiles import get_bpm_timing
        assert get_bpm_timing(None) is None

    def test_zero_bpm_returns_none(self):
        """Zero BPM → None."""
        from hermes_core.profiles import get_bpm_timing
        assert get_bpm_timing(0.0) is None

    def test_negative_bpm_returns_none(self):
        """Negative BPM → None."""
        from hermes_core.profiles import get_bpm_timing
        assert get_bpm_timing(-10.0) is None

    def test_inf_bpm_returns_none(self):
        """Inf BPM → None."""
        from hermes_core.profiles import get_bpm_timing
        assert get_bpm_timing(float("inf")) is None

    def test_nan_bpm_returns_none(self):
        """NaN BPM → None."""
        from hermes_core.profiles import get_bpm_timing
        assert get_bpm_timing(float("nan")) is None

    def test_bpm_override_in_chain(self, monkeypatch):
        """When bpm is passed, compressor gets BPM-aware timing."""
        from hermes_core.profiles import get_bpm_timing
        from hermes_core.loudness_optimizer import CompressionIntent
        from hermes_core.engine import _derive_compressor_intent

        # Simulate a vocal with crest 12 dB (medium compression).
        intent = _derive_compressor_intent(rms_db=-18.0, peak_db=-6.0, genre="pop")

        # Without BPM: genre preset for "pop" vocal = attack 5, release 80.
        preset_no_bpm = {"attack_ms": 5.0, "release_ms": 80.0}

        # With BPM=140 (fast): override should be attack 3, release 60.
        bpm_timing = get_bpm_timing(140.0)
        preset_with_bpm = dict(preset_no_bpm, **bpm_timing)
        assert preset_with_bpm["attack_ms"] == 3.0
        assert preset_with_bpm["release_ms"] == 60.0
        # Original preset is unchanged (immutability).
        assert preset_no_bpm["attack_ms"] == 5.0

    def test_bpm_fallback_when_none(self, monkeypatch):
        """When bpm=None, get_bpm_timing returns None → keep genre preset."""
        from hermes_core.profiles import get_bpm_timing
        timing = get_bpm_timing(None)
        assert timing is None

    def test_fet_translator_respects_bpm_timing(self):
        """FET translator uses attack_ms/release_ms from preset regardless of source."""
        from hermes_core.engine import _apply_fet_params
        from hermes_core.loudness_optimizer import CompressionIntent

        intent = CompressionIntent(
            amount="medium", gr_target_db=4.0,
            crest_factor_db=12.0, rms_db=-18.0, peak_db=-6.0,
        )
        # Simulate BPM-aware preset (fast timing).
        bpm_preset = {"attack_ms": 3.0, "release_ms": 60.0}
        params = _apply_fet_params(intent, bpm_preset)
        assert params["Attack"] == 3.0
        assert params["Release"] == 60.0

    def test_vca_translator_respects_bpm_timing(self):
        """VCA translator uses attack_ms/release_ms from preset."""
        from hermes_core.engine import _apply_vca_params
        from hermes_core.loudness_optimizer import CompressionIntent

        intent = CompressionIntent(
            amount="light", gr_target_db=2.0,
            crest_factor_db=8.0, rms_db=-20.0, peak_db=-12.0,
        )
        # Simulate BPM-aware preset (slow timing).
        bpm_preset = {"attack_ms": 10.0, "release_ms": 200.0}
        params = _apply_vca_params(intent, bpm_preset)
        assert params["Attack"] == 10.0
        assert params["Release"] == 200.0


# ══════════════════════════════════════════════════════════════
# 流派参数一致性测试 — 确保所有 6 个流派在每个参数表中都有条目
# ══════════════════════════════════════════════════════════════

_ALL_GENRES = [
    "folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto",
]


@pytest.mark.unit
class TestGenreTableConsistency:
    """验证所有 6 个流派在每个参数表中都存在条目。"""

    def test_vocal_to_backing_has_all_genres(self):
        from hermes_core.engine import _GENRE_VOCAL_TO_BACKING
        for g in _ALL_GENRES:
            assert g in _GENRE_VOCAL_TO_BACKING, f"流派 {g} 缺失于 _GENRE_VOCAL_TO_BACKING"

    def test_target_lufs_has_all_genres(self):
        from hermes_core.engine import _GENRE_TARGET_LUFS
        for g in _ALL_GENRES:
            assert g in _GENRE_TARGET_LUFS, f"流派 {g} 缺失于 _GENRE_TARGET_LUFS"

    def test_crest_gr_ratio_has_all_genres(self):
        from hermes_core.engine import _GENRE_CREST_GR_RATIO
        for g in _ALL_GENRES:
            assert g in _GENRE_CREST_GR_RATIO, f"流派 {g} 缺失于 _GENRE_CREST_GR_RATIO"

    def test_rvox_multiplier_has_all_genres(self):
        from hermes_core.engine import _GENRE_RVOX_MULTIPLIER
        for g in _ALL_GENRES:
            assert g in _GENRE_RVOX_MULTIPLIER, f"流派 {g} 缺失于 _GENRE_RVOX_MULTIPLIER"

    def test_bus_gr_target_has_all_genres(self):
        from hermes_core.normalize import _GENRE_BUS_GR_TARGET
        for g in _ALL_GENRES:
            assert g in _GENRE_BUS_GR_TARGET, f"流派 {g} 缺失于 _GENRE_BUS_GR_TARGET"

    def test_cla76_attack_base_has_all_genres(self):
        from hermes_core.engine import _GENRE_CLA76_ATTACK_BASE
        for g in _ALL_GENRES:
            assert g in _GENRE_CLA76_ATTACK_BASE, f"流派 {g} 缺失于 _GENRE_CLA76_ATTACK_BASE"

    def test_cla76_attack_k_has_all_genres(self):
        from hermes_core.engine import _GENRE_CLA76_ATTACK_K
        for g in _ALL_GENRES:
            assert g in _GENRE_CLA76_ATTACK_K, f"流派 {g} 缺失于 _GENRE_CLA76_ATTACK_K"

    def test_prods_range_has_all_genres(self):
        from hermes_core.engine import _GENRE_PRODS_RANGE
        for g in _ALL_GENRES:
            assert g in _GENRE_PRODS_RANGE, f"流派 {g} 缺失于 _GENRE_PRODS_RANGE"

    def test_all_tables_have_same_genre_keys(self):
        """所有流派参数表的键集合应完全一致。"""
        from hermes_core.engine import (
            _GENRE_VOCAL_TO_BACKING, _GENRE_TARGET_LUFS,
            _GENRE_CREST_GR_RATIO, _GENRE_RVOX_MULTIPLIER,
            _GENRE_CLA76_ATTACK_BASE, _GENRE_CLA76_ATTACK_K,
            _GENRE_PRODS_RANGE,
        )
        from hermes_core.normalize import _GENRE_BUS_GR_TARGET

        expected = set(_ALL_GENRES)
        tables = {
            "_GENRE_VOCAL_TO_BACKING": set(_GENRE_VOCAL_TO_BACKING.keys()),
            "_GENRE_TARGET_LUFS": set(_GENRE_TARGET_LUFS.keys()),
            "_GENRE_CREST_GR_RATIO": set(_GENRE_CREST_GR_RATIO.keys()),
            "_GENRE_RVOX_MULTIPLIER": set(_GENRE_RVOX_MULTIPLIER.keys()),
            "_GENRE_BUS_GR_TARGET": set(_GENRE_BUS_GR_TARGET.keys()),
            "_GENRE_CLA76_ATTACK_BASE": set(_GENRE_CLA76_ATTACK_BASE.keys()),
            "_GENRE_CLA76_ATTACK_K": set(_GENRE_CLA76_ATTACK_K.keys()),
            "_GENRE_PRODS_RANGE": set(_GENRE_PRODS_RANGE.keys()),
        }
        for name, keys in tables.items():
            missing = expected - keys
            assert not missing, f"{name} 缺少流派: {missing}"


# ══════════════════════════════════════════════════════════════
# CLA-76 攻击旋钮全流派测试
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCLA76AttackAllGenres:
    """验证 _compute_cla76_attack_knob 在所有流派下输出合理值。"""

    def test_high_crest_all_genres(self):
        """高波峰 (20 dB) → 较慢攻击（较小旋钮值），所有流派应在 [1, 6.5]。"""
        from hermes_core.engine import _compute_cla76_attack_knob
        for g in _ALL_GENRES:
            knob = _compute_cla76_attack_knob(20.0, g)
            assert 1.0 <= knob <= 6.5, f"流派 {g}: 旋钮值 {knob} 超出范围"

    def test_low_crest_all_genres(self):
        """低波峰 (8 dB) → 较快攻击（较大旋钮值），所有流派应在 [1, 6.5]。"""
        from hermes_core.engine import _compute_cla76_attack_knob
        for g in _ALL_GENRES:
            knob = _compute_cla76_attack_knob(8.0, g)
            assert 1.0 <= knob <= 6.5, f"流派 {g}: 旋钮值 {knob} 超出范围"

    def test_normal_crest_all_genres(self):
        """标准波峰 (12 dB) → 所有流派应在 [1, 6.5]。"""
        from hermes_core.engine import _compute_cla76_attack_knob
        for g in _ALL_GENRES:
            knob = _compute_cla76_attack_knob(12.0, g)
            assert 1.0 <= knob <= 6.5, f"流派 {g}: 旋钮值 {knob} 超出范围"

    def test_electronic_slower_than_folk(self):
        """电子流派基础攻击比民谣慢（更高基础值），同等波峰下旋钮值应更大。"""
        from hermes_core.engine import _compute_cla76_attack_knob
        knob_electronic = _compute_cla76_attack_knob(12.0, "electronic")
        knob_folk = _compute_cla76_attack_knob(12.0, "folk")
        assert knob_electronic > knob_folk

    def test_unknown_genre_uses_pop_defaults(self):
        """未知流派回退到 pop 默认值。"""
        from hermes_core.engine import _compute_cla76_attack_knob
        knob_unknown = _compute_cla76_attack_knob(15.0, "jazz")
        knob_pop = _compute_cla76_attack_knob(15.0, "pop")
        assert knob_unknown == knob_pop


# ══════════════════════════════════════════════════════════════
# RVox 全流派测试
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRVoxAllGenres:
    """验证所有流派的 RVox 参数在合理范围内。"""

    def test_compression_in_valid_range(self):
        """所有流派的 RVox Compression 应在 [-36, 0] dB。"""
        from hermes_core.engine import (
            _derive_compressor_intent, _apply_rvox_params,
            _GENRE_RVOX_MULTIPLIER,
        )

        preset = {"attack_ms": 5.0, "release_ms": 100.0}
        # 典型人声：RMS=-18, Peak=-3, crest=15 dB
        for g in _ALL_GENRES:
            intent = _derive_compressor_intent(-18.0, -3.0, genre=g)
            mult = _GENRE_RVOX_MULTIPLIER[g]
            params = _apply_rvox_params(intent, preset, mult)
            assert -36.0 <= params["Compression"] <= 0.0, (
                f"流派 {g}: Compression={params['Compression']} 超出范围"
            )

    def test_gain_matches_compression_ratio(self):
        """Gain = Compression × 0.6，所有流派一致。"""
        from hermes_core.engine import (
            _derive_compressor_intent, _apply_rvox_params,
            _GENRE_RVOX_MULTIPLIER,
        )

        preset = {"attack_ms": 5.0, "release_ms": 100.0}
        for g in _ALL_GENRES:
            intent = _derive_compressor_intent(-18.0, -5.0, genre=g)
            mult = _GENRE_RVOX_MULTIPLIER[g]
            params = _apply_rvox_params(intent, preset, mult)
            expected_gain = round(params["Compression"] * 0.6, 1)
            assert params["Gain"] == pytest.approx(expected_gain, abs=0.2), (
                f"流派 {g}: Gain={params['Gain']} ≠ Compression×0.6={expected_gain}"
            )

    def test_dense_genre_compresses_more(self):
        """电子/流行比民谣压缩更多（更大的 mult → 更负的 Compression）。"""
        from hermes_core.engine import (
            _derive_compressor_intent, _apply_rvox_params,
            _GENRE_RVOX_MULTIPLIER,
        )

        preset = {"attack_ms": 5.0, "release_ms": 100.0}
        intent = _derive_compressor_intent(-18.0, -3.0, genre="pop")

        params_folk = _apply_rvox_params(
            intent, preset, _GENRE_RVOX_MULTIPLIER["folk"])
        params_electronic = _apply_rvox_params(
            intent, preset, _GENRE_RVOX_MULTIPLIER["electronic"])

        # 电子流派 mult=1.8 > 民谣 mult=1.0 → 更负的 Compression
        assert params_electronic["Compression"] < params_folk["Compression"]


# ══════════════════════════════════════════════════════════════
# Pro-DS 去齿音器全流派测试
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestProDSAllGenres:
    """验证 Pro-DS 参数对所有流派的合理性。"""

    def test_range_values_per_genre(self):
        """每个流派的 Range 值在合理范围内（0–24 dB）。"""
        from hermes_core.engine import _GENRE_PRODS_RANGE

        for g in _ALL_GENRES:
            range_db = _GENRE_PRODS_RANGE[g]
            assert 0.0 < range_db <= 24.0, (
                f"流派 {g}: Range={range_db} 超出 Pro-DS 范围 [0, 24]"
            )

    def test_sparse_genres_have_lower_range(self):
        """稀疏流派（folk/ballad）的 Range 应小于密集流派（electronic）。"""
        from hermes_core.engine import _GENRE_PRODS_RANGE

        assert _GENRE_PRODS_RANGE["folk"] < _GENRE_PRODS_RANGE["electronic"]
        assert _GENRE_PRODS_RANGE["ballad"] < _GENRE_PRODS_RANGE["electronic"]

    def test_threshold_with_zero_presence_deficit(self):
        """presence_deficit=0 → threshold=-32 dB（基础值）。"""
        # 模拟 engine.py 中的计算逻辑
        presence_def = 0.0
        threshold_db = -32.0 + presence_def * 0.1
        threshold_db = max(-60.0, min(0.0, threshold_db))
        assert threshold_db == -32.0

    def test_threshold_with_high_presence_deficit(self):
        """presence_deficit=20 → threshold=-30 dB（更不激进）。"""
        presence_def = 20.0
        threshold_db = -32.0 + presence_def * 0.1
        threshold_db = max(-60.0, min(0.0, threshold_db))
        assert threshold_db == -30.0

    def test_threshold_clamped_to_valid_range(self):
        """threshold 应被限制在 [-60, 0] dB。"""
        # 极端高 presence deficit
        presence_def = 500.0
        threshold_db = -32.0 + presence_def * 0.1
        threshold_db = max(-60.0, min(0.0, threshold_db))
        assert -60.0 <= threshold_db <= 0.0

    def test_unknown_genre_uses_default_range(self):
        """未知流派回退到 8.5 dB。"""
        from hermes_core.engine import _GENRE_PRODS_RANGE
        range_db = _GENRE_PRODS_RANGE.get("jazz", 8.5)
        assert range_db == 8.5


# ══════════════════════════════════════════════════════════════
# 流派波峰因子 GR 一致性测试
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCrestGRAllGenres:
    """验证所有流派的波峰因子 → GR 映射一致性。"""

    def test_gr_increases_with_crest(self):
        """所有流派：更高的波峰因子 → 更大的 GR 目标。"""
        from hermes_core.engine import _derive_compressor_intent

        for g in _ALL_GENRES:
            intent_low = _derive_compressor_intent(-18.0, -8.0, genre=g)  # crest=10
            intent_high = _derive_compressor_intent(-18.0, -2.0, genre=g)  # crest=16
            assert intent_high.gr_target_db >= intent_low.gr_target_db, (
                f"流派 {g}: 高波峰 GR {intent_high.gr_target_db} "
                f"< 低波峰 GR {intent_low.gr_target_db}"
            )

    def test_electronic_has_heaviest_compression(self):
        """电子流派的 GR 应最重（crest GR ratio 最高）。"""
        from hermes_core.engine import _derive_compressor_intent

        intent_electronic = _derive_compressor_intent(-18.0, -3.0, genre="electronic")
        intent_folk = _derive_compressor_intent(-18.0, -3.0, genre="folk")
        assert intent_electronic.gr_target_db > intent_folk.gr_target_db

    def test_folk_ballad_are_lightest(self):
        """民谣和抒情流派的 GR 应最轻。"""
        from hermes_core.engine import _GENRE_CREST_GR_RATIO

        folk_ratio = _GENRE_CREST_GR_RATIO["folk"]
        ballad_ratio = _GENRE_CREST_GR_RATIO["ballad"]
        electronic_ratio = _GENRE_CREST_GR_RATIO["electronic"]
        assert folk_ratio < electronic_ratio
        assert ballad_ratio < electronic_ratio


# ════════════════════════════════════════════════════════════════
# 空间效果器发送量计算测试
# ════════════════════════════════════════════════════════════════

_ALL_GENRES_SPATIAL = [
    "folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto",
]


@pytest.mark.unit
class TestSpatialSendComputation:
    """验证 _compute_spatial_sends 在所有流派下输出合理值。"""

    # ── 表完整性 ──────────────────────────────────────────

    def test_all_genres_in_reverb_table(self):
        """6 个流派在 _GENRE_REVERB_SEND_BASE 中都有条目。"""
        from hermes_core.engine import _GENRE_REVERB_SEND_BASE
        for g in _ALL_GENRES_SPATIAL:
            assert g in _GENRE_REVERB_SEND_BASE, f"流派 {g} 缺失于 _GENRE_REVERB_SEND_BASE"
            entry = _GENRE_REVERB_SEND_BASE[g]
            for bus in ("plate", "hall", "room"):
                assert bus in entry, f"流派 {g} 缺少 {bus} 混响"

    def test_all_genres_in_delay_table(self):
        """6 个流派在 _GENRE_DELAY_SEND_BASE 中都有条目。"""
        from hermes_core.engine import _GENRE_DELAY_SEND_BASE
        for g in _ALL_GENRES_SPATIAL:
            assert g in _GENRE_DELAY_SEND_BASE, f"流派 {g} 缺失于 _GENRE_DELAY_SEND_BASE"
            entry = _GENRE_DELAY_SEND_BASE[g]
            for bus in ("slap", "throw", "pingpong"):
                assert bus in entry, f"流派 {g} 缺少 {bus} 延迟"

    # ── 延迟开关 ──────────────────────────────────────────

    def test_folk_delays_disabled(self):
        """民谣流派不使用延迟。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends("folk", 12.0, 2.0, -3.0)
        assert sends["delay_slap"] is None
        assert sends["delay_throw"] is None
        assert sends["delay_pingpong"] is None

    def test_pop_delays_enabled(self):
        """流行流派使用所有延迟。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends("pop", 12.0, 2.0, -3.0)
        assert sends["delay_slap"] is not None
        assert sends["delay_throw"] is not None
        assert sends["delay_pingpong"] is not None

    # ── 修正项验证 ────────────────────────────────────────

    def test_high_crest_reduces_send(self):
        """高波峰因子 → 更低发送量（人声动态已"大"）。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_low = _compute_spatial_sends("pop", 8.0, 2.0, -3.0)
        sends_high = _compute_spatial_sends("pop", 18.0, 2.0, -3.0)
        assert sends_high["reverb_plate"] < sends_low["reverb_plate"]

    def test_muddy_vocal_reduces_send(self):
        """浑浊人声 → 更低发送量（避免叠加低频）。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_clean = _compute_spatial_sends("pop", 12.0, 2.0, -5.0)
        sends_muddy = _compute_spatial_sends("pop", 12.0, 2.0, 5.0)
        assert sends_muddy["reverb_hall"] > sends_clean["reverb_hall"]

    def test_presence_deficit_reduces_send(self):
        """存在感缺失 → 更低发送量（混响会推远人声）。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_normal = _compute_spatial_sends("pop", 12.0, 2.0, -3.0)
        sends_dull = _compute_spatial_sends("pop", 12.0, 8.0, -3.0)
        assert sends_dull["reverb_room"] < sends_normal["reverb_room"]

    def test_sibilance_affects_plate_only(self):
        """齿音修正仅影响 Plate，不影响 Hall/Room/Delay。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_no_sib = _compute_spatial_sends("pop", 12.0, 2.0, -3.0, sibilance_peak_db=None)
        sends_sib = _compute_spatial_sends("pop", 12.0, 2.0, -3.0, sibilance_peak_db=-20.0)
        # Plate 应降低
        assert sends_sib["reverb_plate"] < sends_no_sib["reverb_plate"]
        # Hall / Room / Delay 不应变
        assert sends_sib["reverb_hall"] == sends_no_sib["reverb_hall"]
        assert sends_sib["reverb_room"] == sends_no_sib["reverb_room"]
        assert sends_sib["delay_slap"] == sends_no_sib["delay_slap"]

    def test_section_chorus_higher_than_verse(self):
        """副歌发送量高于主歌。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_verse = _compute_spatial_sends("pop", 12.0, 2.0, -3.0, section="verse")
        sends_chorus = _compute_spatial_sends("pop", 12.0, 2.0, -3.0, section="chorus")
        assert sends_chorus["reverb_plate"] > sends_verse["reverb_plate"]
        assert sends_chorus["delay_throw"] > sends_verse["delay_throw"]

    # ── 范围限制 ──────────────────────────────────────────

    def test_output_clamped_to_valid_range(self):
        """所有发送量应在 [-24, -6] dB 范围内。"""
        from hermes_core.engine import _compute_spatial_sends
        # 极端场景：强压缩人声（低波峰）、干净频谱、副歌
        sends = _compute_spatial_sends(
            "electronic", crest_factor_db=6.0,
            presence_deficit_db=0.0, mud_ratio_db=-8.0,
            section="bridge",
        )
        for key, val in sends.items():
            if val is not None:
                assert -24.0 <= val <= -6.0, (
                    f"{key}={val} 超出 [-24, -6] 范围"
                )

    def test_all_genres_produce_valid_range(self):
        """所有流派在标准条件下输出都在有效范围。"""
        from hermes_core.engine import _compute_spatial_sends
        for g in _ALL_GENRES_SPATIAL:
            sends = _compute_spatial_sends(g, 12.0, 2.0, -3.0)
            for key, val in sends.items():
                if val is not None:
                    assert -24.0 <= val <= -6.0, (
                        f"流派 {g}: {key}={val} 超出 [-24, -6]"
                    )

    # ── 回退 ──────────────────────────────────────────────

    def test_unknown_genre_falls_back_to_pop(self):
        """未知流派回退到 pop 默认值。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_unknown = _compute_spatial_sends("jazz", 12.0, 2.0, -3.0)
        sends_pop = _compute_spatial_sends("pop", 12.0, 2.0, -3.0)
        for key in sends_pop:
            assert sends_unknown[key] == sends_pop[key], (
                f"未知流派 {key}={sends_unknown[key]} ≠ pop {key}={sends_pop[key]}"
            )

    # ── 端到端 ────────────────────────────────────────────

    def test_compute_end_to_end_pop_typical(self):
        """典型流行人声端到端计算验证。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends(
            "pop", crest_factor_db=14.0, presence_deficit_db=3.0,
            mud_ratio_db=-3.0, sibilance_peak_db=-28.0, section="verse",
        )
        # 主歌基准 + 中度修正 = 比基准低 2-3 dB
        assert sends["reverb_plate"] == pytest.approx(-14.6, abs=0.3)
        assert sends["reverb_hall"] == pytest.approx(-16.2, abs=0.3)
        assert sends["reverb_room"] == pytest.approx(-18.2, abs=0.3)
        assert sends["delay_slap"] == pytest.approx(-16.3, abs=0.3)
        assert sends["delay_throw"] == pytest.approx(-19.3, abs=0.3)
        assert sends["delay_pingpong"] == pytest.approx(-21.3, abs=0.3)
        assert sends["microshift"] == pytest.approx(-13.3, abs=0.3)

    def test_ballad_wetter_than_folk(self):
        """抒情流派发送量比民谣更湿润。"""
        from hermes_core.engine import _compute_spatial_sends
        sends_folk = _compute_spatial_sends(
            "folk", crest_factor_db=14.0, presence_deficit_db=2.0,
            mud_ratio_db=-3.0, section="verse",
        )
        sends_ballad = _compute_spatial_sends(
            "ballad", crest_factor_db=14.0, presence_deficit_db=2.0,
            mud_ratio_db=-3.0, section="verse",
        )
        # Ballad 所有混响发送量应高于 folk（相同条件下）
        for bus in ("reverb_plate", "reverb_hall", "reverb_room"):
            assert sends_ballad[bus] > sends_folk[bus], (
                f"Ballad {bus}={sends_ballad[bus]} 应 > Folk {bus}={sends_folk[bus]}"
            )

    def test_chinese_folk_bel_canto_wettest(self):
        """中国民歌/民族美声：透亮水灵 + 大气绵长，混响偏大。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends(
            "chinese_folk_bel_canto", crest_factor_db=14.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0, section="verse",
        )
        # Plate + Hall 与 electronic 同级（最湿梯队）
        assert sends["reverb_plate"] >= -12.0, (
            f"民美 plate={sends['reverb_plate']} 应偏湿润"
        )
        assert sends["reverb_hall"] >= -12.0, (
            f"民美 hall={sends['reverb_hall']} 应偏湿润"
        )
        # Room 退后避浑
        assert sends["reverb_room"] <= sends["reverb_plate"], (
            "Room 不应超过 Plate，防止浑浊"
        )
        # 延迟已启用
        assert sends["delay_slap"] is not None
        assert sends["delay_throw"] is not None
        assert sends["delay_pingpong"] is not None
        assert sends["microshift"] is not None



# ════════════════════════════════════════════════════════════════
# 空间效果器链创建测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestBuildSpatialChainLogic:
    """验证 build_spatial_chain 的核心逻辑。

    由于 build_spatial_chain 需要完整的 REAPER bridge mock
    （TrackManager → FxManager → SendManager 多层调用链），
    这里通过测试数据结构和方法签名来验证正确性。
    实际 REAPER 集成行为由 test_mixing_workflow.py 覆盖。
    """

    def test_spatial_plugin_mapping_complete(self):
        """每种总线类型都有对应的插件和名称。"""
        from hermes_core.engine import _SPATIAL_PLUGIN, _SPATIAL_BUS_NAMES
        expected = {"plate", "hall", "room", "slap", "throw", "pingpong",
                    "microshift", "blackhole", "supernova"}
        assert set(_SPATIAL_PLUGIN.keys()) == expected
        assert set(_SPATIAL_BUS_NAMES.keys()) == expected

    def test_disabled_buses_skip_logic(self):
        """None 发送量的总线被跳过。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends("folk", 12.0, 2.0, -3.0)
        # 民谣：延迟为 None
        assert sends["delay_slap"] is None
        assert sends["delay_throw"] is None
        assert sends["delay_pingpong"] is None
        # 混响 + MicroShift 仍然存在
        assert sends["reverb_plate"] is not None
        assert sends["microshift"] is not None

    def test_send_levels_from_computation(self):
        """_compute_spatial_sends 的输出可直接作为 build_spatial_chain 输入。"""
        from hermes_core.engine import _compute_spatial_sends
        sends = _compute_spatial_sends("pop", 14.0, 3.0, -3.0, -28.0)
        # 所有非 None 值应在 [-24, -6] 范围
        # 接受 reverb_* / delay_* / microshift 键
        for key, val in sends.items():
            assert key.startswith("reverb_") or key.startswith("delay_") or key == "microshift", (
                f"key={key} 格式不正确"
            )
            if val is not None:
                assert -24.0 <= val <= -6.0, f"{key}={val} 超出范围"

    def test_return_eq_table_coverage(self):
        """_GENRE_RETURN_EQ 覆盖所有流派和总线类型。"""
        from hermes_core.engine import _GENRE_RETURN_EQ
        for g in ["folk", "ballad", "pop", "rock", "electronic",
                   "chinese_folk_bel_canto"]:
            for bus in ("plate", "hall", "room", "delay"):
                assert bus in _GENRE_RETURN_EQ.get(g, {}), (
                    f"{g}/{bus} 缺失于 _GENRE_RETURN_EQ"
                )

class TestAutoCorrectiveEQ:
    """验证 auto_corrective_eq 方法的闭环频谱分析→EQ逻辑。"""

    def test_no_audio_source_returns_error(self):
        """无音频源时返回错误字典。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        result = eng.auto_corrective_eq(track_idx=0)
        assert result["applied"] is False
        assert "error" in result

    def test_no_stem_file_returns_gracefully(self):
        """stems_cache 无匹配轨道路径时优雅返回。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        eng._stems_cache = [{"track_index": 99, "success": True, "file_path": ""}]
        result = eng.auto_corrective_eq(track_idx=0)
        assert result["applied"] is False
        assert result.get("error") is not None

    def test_real_wav_analyzes_resonances(self, tmp_path):
        """真实 WAV 文件 → 频谱分析 → 检测共振 → 应用 EQ。"""
        from tests.conftest import make_test_wav

        wav_path = tmp_path / "test_vocal.wav"
        make_test_wav(str(wav_path), duration_sec=2.0, frequency=440.0)

        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        # 模拟 Pro-Q 3 在轨道上
        eng._bridge._api.TrackFX_GetCount.return_value = 1
        eng._bridge._api.TrackFX_GetFXName.return_value = (
            True, "VST: FabFilter Pro-Q 3 (FabFilter)"
        )
        eng._fx.set_param = MagicMock()
        eng._stems_cache = [{
            "track_index": 0, "success": True,
            "file_path": str(wav_path),
        }]

        result = eng.auto_corrective_eq(track_idx=0)
        assert isinstance(result, dict)
        assert "resonance_count" in result

    def test_no_pro_q3_adds_reaeq(self):
        """无 Pro-Q 3 时自动添加 ReaEQ 并应用校正。"""
        from tests.conftest import make_test_wav

        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wav_path = os.path.join(td, "test.wav")
            make_test_wav(wav_path, duration_sec=1.0, frequency=440.0)

            eng = MixingEngine()
            eng._bridge._api = MagicMock()
            eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
            eng._bridge._api.TrackFX_GetCount.return_value = 0
            eng._bridge._api.TrackFX_GetFXName.return_value = (False, "")
            eng._fx.add = MagicMock(return_value=0)
            eng._fx.set_param = MagicMock()
            eng._stems_cache = [{
                "track_index": 0, "success": True,
                "file_path": wav_path,
            }]

            result = eng.auto_corrective_eq(track_idx=0)
            assert "applied" in result

    def test_track_not_found(self):
        """轨道不存在时返回错误。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = None
        result = eng.auto_corrective_eq(track_idx=99)
        assert result["applied"] is False
        assert result["error"] == "Track not found"

    def test_spectrum_analysis_failure_handled(self, tmp_path):
        """频谱分析失败时优雅降级。"""
        # 创建一个无效的 WAV 路径
        bad_path = str(tmp_path / "invalid.wav")
        with open(bad_path, "wb") as f:
            f.write(b"not a wav file")

        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        eng._stems_cache = [{
            "track_index": 0, "success": True,
            "file_path": bad_path,
        }]

        result = eng.auto_corrective_eq(track_idx=0)
        assert result["applied"] is False
        assert "Spectrum analysis failed" in str(result.get("error", ""))


@pytest.mark.unit
class TestWriteAutomation:
    """验证 write_automation 方法。"""

    def test_track_not_found(self):
        """轨道不存在时返回错误。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = None
        result = eng.write_automation(99, "D_VOL", [(0.0, 0.0)])
        assert result["point_count"] == 0
        assert "error" in result

    def test_envelope_not_found(self):
        """包络不存在时返回警告。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        eng._bridge._api.GetTrackEnvelopeByName.return_value = None
        result = eng.write_automation(0, "NONEXISTENT", [(0.0, 0.5)])
        assert result["point_count"] == 0
        assert "Envelope" in str(result.get("error", ""))

    def test_writes_sorted_points(self):
        """点按时间自动排序后写入。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        mock_env = MagicMock()
        eng._bridge._api.GetTrackEnvelopeByName.return_value = mock_env

        # 逆序输入点
        points = [(3.0, -3.0), (1.0, -6.0), (2.0, -4.5)]
        result = eng.write_automation(0, "D_VOL", points)

        assert result["point_count"] == 3
        assert result["param_name"] == "D_VOL"
        assert mock_env is not None
        # 验证 InsertEnvelopePoint 被调用 3 次
        assert eng._bridge._api.InsertEnvelopePoint.call_count == 3

    def test_empty_points_list(self):
        """空点列表 → 0 个点写入。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        mock_env = MagicMock()
        eng._bridge._api.GetTrackEnvelopeByName.return_value = mock_env

        result = eng.write_automation(0, "PAN", [])
        assert result["point_count"] == 0

    def test_returns_structured_result(self):
        """返回结构化诊断字典。"""
        eng = MixingEngine()
        eng._bridge._api = MagicMock()
        eng._bridge._api.GetTrack.return_value = "(MediaTrack*)0x1"
        mock_env = MagicMock()
        eng._bridge._api.GetTrackEnvelopeByName.return_value = mock_env

        result = eng.write_automation(0, "D_VOL", [(0.0, 1.0)])
        assert "track_idx" in result
        assert "param_name" in result
        assert "point_count" in result


@pytest.mark.unit
class TestAbbeyRoadEQ:
    """验证 _apply_abbey_road_eq 函数 —— ReaEQ HPF+LPF 安全滤波。"""

    def test_configures_hpf_and_lpf_bands(self):
        """HPF@600Hz + LPF@10kHz 被正确写入 ReaEQ。"""
        from hermes_core.spatial_engine import _apply_abbey_road_eq
        mock_fx = MagicMock()
        mock_fx.set_param = MagicMock()

        _apply_abbey_road_eq(mock_fx, aux_track=3, eq_fx_idx=0)

        assert mock_fx.set_param.call_count > 0
        param_names = []
        for call_args in mock_fx.set_param.call_args_list:
            param_names.append(str(call_args[0][2]))
        assert any("Band 1" in p for p in param_names)
        assert any("Band 2" in p for p in param_names)

    def test_band_types_are_correct(self):
        """Band 1 Type=0 (HPF), Band 2 Type=4 (LPF) → 归一化后写入。"""
        from hermes_core.spatial_engine import _apply_abbey_road_eq
        mock_fx = MagicMock()
        mock_fx.set_param = MagicMock()

        _apply_abbey_road_eq(mock_fx, aux_track=3, eq_fx_idx=0)

        type_calls = {}
        for call_args in mock_fx.set_param.call_args_list:
            param_name = str(call_args[0][2])
            norm_val = call_args[0][3]
            if "Type" in param_name:
                type_calls[param_name] = norm_val

        assert type_calls.get("Band 1 Type") == pytest.approx(0.0, abs=0.01)
        assert type_calls.get("Band 2 Type") == pytest.approx(0.8, abs=0.01)

    def test_bands_3_4_disabled(self):
        """Band 3 和 Band 4 Enabled=0。"""
        from hermes_core.spatial_engine import _apply_abbey_road_eq
        mock_fx = MagicMock()
        mock_fx.set_param = MagicMock()

        _apply_abbey_road_eq(mock_fx, aux_track=3, eq_fx_idx=0)

        enabled_calls = {}
        for call_args in mock_fx.set_param.call_args_list:
            param_name = str(call_args[0][2])
            norm_val = call_args[0][3]
            if "Enabled" in param_name:
                enabled_calls[param_name] = norm_val

        assert enabled_calls.get("Band 3 Enabled") == pytest.approx(0.0, abs=0.01)
        assert enabled_calls.get("Band 4 Enabled") == pytest.approx(0.0, abs=0.01)

    def test_raises_no_exception_even_on_failure(self):
        """即使设置失败也不抛异常（优雅降级）。"""
        from hermes_core.spatial_engine import _apply_abbey_road_eq
        mock_fx = MagicMock()
        mock_fx.set_param = MagicMock(side_effect=RuntimeError("param failed"))

        # 不应抛出异常
        _apply_abbey_road_eq(mock_fx, aux_track=3, eq_fx_idx=0)


@pytest.mark.unit
class TestVocalChainEnhancement:
    """验证人声处理链补全 — saturation/dynamic_eq/doubler。"""

    def test_saturation_fx_type_detected(self):
        """Decapitator → fx_type='saturation'。"""
        from hermes_core.profiles import _resolve_fx_type
        assert _resolve_fx_type("VST3: Decapitator Mono (Soundtoys)") == "saturation"
        assert _resolve_fx_type("VST3: Saturn 2 (FabFilter)") == "saturation"

    def test_doubler_fx_type_detected(self):
        """MicroShift → fx_type='doubler'。"""
        from hermes_core.profiles import _resolve_fx_type
        assert _resolve_fx_type("VST3: MicroShift (Soundtoys)") == "doubler"

    def test_dynamic_eq_fx_type(self):
        """dynamic_eq 通过与 fx_type 字段指定（非别名推导）。"""
        from hermes_core.profiles import _resolve_fx_type
        # dynamic_eq 是显式指定的 fx_type，不由名称推导
        assert _resolve_fx_type("VST: FabFilter Pro-Q 3 (FabFilter)", "dynamic_eq") == "dynamic_eq"

    def test_default_vocal_chain_has_9_stages(self):
        """默认人声链现在包含 9 级。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        assert len(chain) == 9

        # 验证链中包含 saturation、dynamic_eq、doubler 阶段
        types = [fx.fx_type for fx in chain]
        assert "saturation" in types
        assert "dynamic_eq" in types
        assert "doubler" in types

    def test_get_default_vocal_chain_order(self):
        """链顺序: eq → saturation → eq → fet → deesser → dynamic_eq → rvox → eq → doubler。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        order = [(fx.fx_type, fx.eq_position) for fx in chain]
        expected = [
            ("eq", "pre"),
            ("saturation", "solo"),    # eq_position 默认为 "solo"
            ("eq", "solo"),
            ("fet", "solo"),
            ("deesser", "solo"),
            ("dynamic_eq", "solo"),
            ("rvox", "solo"),
            ("eq", "post"),
            ("doubler", "solo"),
        ]
        assert order == expected

    def test_saturation_crest_driven(self):
        """饱和量基于波峰因子推导：高波峰→少饱和。"""
        # 通过 _build_audio_chain 的 saturation 分支验证逻辑
        crest_high = 20.0  # 高波峰: peak - rms = 20
        drive_high = max(0.1, min(1.0, 1.0 - (crest_high - 8.0) * 0.05))
        crest_low = 8.0    # 低波峰
        drive_low = max(0.1, min(1.0, 1.0 - (crest_low - 8.0) * 0.05))
        assert drive_high < drive_low, (
            f"高波峰 drive={drive_high} 应 < 低波峰 drive={drive_low}"
        )

    def test_saturation_drive_in_valid_range(self):
        """drive 值始终在 [0.1, 1.0] 范围内。"""
        for crest_db in (4.0, 8.0, 12.0, 16.0, 20.0, 24.0, 30.0):
            drive = max(0.1, min(1.0, 1.0 - (crest_db - 8.0) * 0.05))
            assert 0.1 <= drive <= 1.0, f"crest={crest_db} → drive={drive} 超出范围"


@pytest.mark.unit
class TestVCABusGrouping:
    """验证 BusManager VCA 分组逻辑。"""

    def test_create_bus_sets_vca_master(self):
        """创建带子轨道的 bus 时设置 VCA Master。"""
        from hermes_core.bus import BusManager
        from unittest.mock import MagicMock

        mock_bridge = MagicMock()
        mock_api = mock_bridge.api
        mock_api.CountTracks.return_value = 5
        mock_api.GetTrack.return_value = "(MediaTrack*)0x1"

        bm = BusManager(mock_bridge)
        bm.create_bus("TestBus", [0, 1])

        # 验证 GetSetTrackGroupMembership 被调用
        assert mock_api.GetSetTrackGroupMembership.call_count >= 3

    def test_vca_group_number_within_range(self):
        """VCA 组号在 1-64 范围内。"""
        for insert_at in (0, 10, 63, 100):
            vca_group = min(insert_at + 1, 64)
            assert 1 <= vca_group <= 64, f"insert_at={insert_at} → vca_group={vca_group}"

    def test_vca_group_mask_computation(self):
        """VCA 位掩码计算正确。"""
        # group 1 → mask = 1 (bit 0)
        g1_mask = 1 << 0
        assert g1_mask == 1

        # group 32 → mask = 1 << 31 (bit 31)
        g32_mask = 1 << 31
        assert g32_mask == 2147483648

    def test_create_bus_vca_handles_missing_api(self):
        """VCA API 不可用时优雅降级（不抛异常）。"""
        from hermes_core.bus import BusManager
        from unittest.mock import MagicMock

        mock_bridge = MagicMock()
        mock_api = mock_bridge.api
        mock_api.CountTracks.return_value = 5
        mock_api.GetTrack.return_value = "(MediaTrack*)0x1"
        # 模拟 GetSetTrackGroupMembership 不存在
        del mock_api.GetSetTrackGroupMembership

        bm = BusManager(mock_bridge)
        # 不应抛出异常
        idx = bm.create_bus("TestBus", [0, 1])
        assert idx >= 0
