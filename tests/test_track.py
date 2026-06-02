"""Tests for hermes_core.track — TrackManager with mocked bridge."""

from unittest.mock import MagicMock

import pytest

from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bridge import ReaperBridge
from tests.conftest import require_reaper, clean_project, make_test_wav


def _mock_bridge(**api_overrides):
    mock = MagicMock()
    mock.api = MagicMock()
    for attr, val in api_overrides.items():
        setattr(mock.api, attr, val)
    if "GetTrack" not in api_overrides:
        mock.api.GetTrack = MagicMock(
            side_effect=lambda p, i: f"(MediaTrack*)0x{i+1:016x}"
        )
    if "CountTracks" not in api_overrides:
        mock.api.CountTracks = MagicMock(return_value=5)
    return mock


def _mock_track_name(track, buf_str, buf_size):
    return (True, track, "TestTrack", buf_size)


@pytest.mark.unit
class TestTrackInfo:
    def test_construction(self):
        t = TrackInfo(
            index=0, name="Kick", volume_db=-3.0, pan=0.5,
            mute=False, solo=True, fx_count=2, depth=0,
            item_count=1, selected=False,
        )
        assert t.index == 0
        assert t.solo is True

    def test_to_dict(self):
        t = TrackInfo(
            index=1, name="Bass", volume_db=0.0, pan=0.0,
            mute=False, solo=False, fx_count=0, depth=0,
            item_count=1, selected=False,
        )
        d = t.to_dict()
        assert d["index"] == 1
        assert d["name"] == "Bass"


@pytest.mark.unit
class TestConstruction:
    def test_stores_bridge(self):
        bridge = _mock_bridge()
        mgr = TrackManager(bridge)
        assert mgr._bridge is bridge


@pytest.mark.unit
class TestCreate:
    def test_creates_at_specified_index(self):
        bridge = _mock_bridge(
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        idx = mgr.create(index=3, name="Test")
        bridge.api.InsertTrackAtIndex.assert_called_once_with(3, True)
        assert idx == 3

    def test_creates_at_end_when_index_negative(self):
        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=10),
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        idx = mgr.create(index=-1, name="End")
        bridge.api.InsertTrackAtIndex.assert_called_once_with(10, True)

    def test_sets_name_when_provided(self):
        bridge = _mock_bridge(
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.create(index=0, name="Named")
        bridge.api.GetSetMediaTrackInfo_String.assert_called()


@pytest.mark.unit
class TestDelete:
    def test_deletes_existing_track(self):
        bridge = _mock_bridge(DeleteTrack=MagicMock())
        mgr = TrackManager(bridge)
        mgr.delete(0)
        bridge.api.DeleteTrack.assert_called_once()

    def test_handles_null_track(self):
        bridge = _mock_bridge(
            GetTrack=MagicMock(return_value=None),
            DeleteTrack=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.delete(0)
        bridge.api.DeleteTrack.assert_not_called()


@pytest.mark.unit
class TestSetProperties:
    def test_set_name(self):
        bridge = _mock_bridge(GetSetMediaTrackInfo_String=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_name(0, "NewName")
        bridge.api.GetSetMediaTrackInfo_String.assert_called_once()

    def test_set_volume(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_volume(0, -6.0)
        bridge.api.SetMediaTrackInfo_Value.assert_called()

    def test_set_pan(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_pan(0, 0.5)
        bridge.api.SetMediaTrackInfo_Value.assert_called()

    def test_set_mute(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_mute(0, True)
        bridge.api.SetMediaTrackInfo_Value.assert_called()

    def test_set_folder_depth(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_folder_depth(0, 1)
        bridge.api.SetMediaTrackInfo_Value.assert_called()

    def test_set_solo(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_solo(0, True)
        bridge.api.SetMediaTrackInfo_Value.assert_called()


@pytest.mark.unit
class TestQuery:
    def test_count(self):
        bridge = _mock_bridge(CountTracks=MagicMock(return_value=5))
        assert TrackManager(bridge).count() == 5

    def test_get_returns_track_info(self):
        bridge = _mock_bridge(
            GetTrackName=MagicMock(side_effect=_mock_track_name),
            GetMediaTrackInfo_Value=MagicMock(return_value=1.0),
            TrackFX_GetCount=MagicMock(return_value=0),
            CountTrackMediaItems=MagicMock(return_value=1),
            IsTrackSelected=MagicMock(return_value=False),
        )
        info = TrackManager(bridge).get(0)
        assert info is not None
        assert info.name == "TestTrack"

    def test_get_returns_none_for_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        assert TrackManager(bridge).get(0) is None

    def test_get_returns_none_on_error(self):
        bridge = _mock_bridge(
            GetTrackName=MagicMock(side_effect=RuntimeError("boom")),
        )
        assert TrackManager(bridge).get(0) is None

    def test_list_all(self):
        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=2),
            GetTrackName=MagicMock(side_effect=_mock_track_name),
            GetMediaTrackInfo_Value=MagicMock(return_value=1.0),
            TrackFX_GetCount=MagicMock(return_value=0),
            CountTrackMediaItems=MagicMock(return_value=1),
            IsTrackSelected=MagicMock(return_value=False),
        )
        result = TrackManager(bridge).list_all()
        assert len(result) == 2

    def test_list_all_skips_none_tracks(self):
        call_count = [0]
        def _mixed_track(proj, idx):
            call_count[0] += 1
            return None if call_count[0] % 2 == 0 else f"(MediaTrack*)0x{idx:016x}"

        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=4),
            GetTrack=MagicMock(side_effect=_mixed_track),
            GetTrackName=MagicMock(side_effect=_mock_track_name),
            GetMediaTrackInfo_Value=MagicMock(return_value=1.0),
            TrackFX_GetCount=MagicMock(return_value=0),
            CountTrackMediaItems=MagicMock(return_value=1),
            IsTrackSelected=MagicMock(return_value=False),
        )
        result = TrackManager(bridge).list_all()
        assert len(result) == 2


@pytest.mark.unit
class TestImportStems:
    def test_creates_track_per_file(self):
        bridge = _mock_bridge(
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        result = mgr.import_stems({"Kick": "/tmp/kick.wav", "Snare": "/tmp/snare.wav"})
        assert len(result) == 2
        assert bridge.api.InsertTrackAtIndex.call_count == 2


@pytest.mark.unit
class TestGetItemPosition:
    def test_returns_position(self):
        bridge = _mock_bridge(
            GetTrackMediaItem=MagicMock(return_value="(MediaItem*)0x1"),
            GetMediaItemInfo_Value=MagicMock(return_value=1.5),
        )
        assert TrackManager(bridge).get_item_position(0, 0) == 1.5

    def test_returns_zero_for_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        assert TrackManager(bridge).get_item_position(0, 0) == 0.0

    def test_returns_zero_for_null_item(self):
        bridge = _mock_bridge(GetTrackMediaItem=MagicMock(return_value=None))
        assert TrackManager(bridge).get_item_position(0, 0) == 0.0


@pytest.mark.unit
class TestDbConversion:
    def test_round_trip(self):
        from hermes_core.audio_utils import db_to_norm, norm_to_db
        for db in [-12, -6, -3, 0, 3, 6]:
            norm = db_to_norm(db)
            back = norm_to_db(norm)
            assert back == pytest.approx(db, abs=0.01)

    def test_norm_to_db_handles_zero(self):
        from hermes_core.audio_utils import norm_to_db
        assert norm_to_db(0.0) == -150.0


# ══════════════════════════════════════════════════════════════
# Integration tests (require running REAPER)
# ══════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestTrackIntegration:
    def test_create_and_get_track(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create(name="IntTest")
        info = mgr.get(idx)
        assert info is not None
        assert info.name == "IntTest"
        assert info.fx_count >= 0

    def test_set_volume_and_pan(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create(name="VolTest")
        mgr.set_volume(idx, -6.0)
        mgr.set_pan(idx, 0.5)
        info = mgr.get(idx)
        assert info is not None
        assert abs(info.volume_db - (-6.0)) < 1.0
        assert abs(info.pan - 0.5) < 0.1

    def test_set_mute_and_solo(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create(name="MuteTest")
        mgr.set_mute(idx, True)
        info = mgr.get(idx)
        assert info is not None
        assert info.mute is True
        mgr.set_mute(idx, False)
        assert mgr.get(idx).mute is False

    def test_import_media(self, tmp_path):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create(name="MediaTest")
        wav = make_test_wav(tmp_path / "test.wav", duration_sec=0.5)
        ok = mgr.import_media(idx, str(wav))
        assert ok is True
        info = mgr.get(idx)
        assert info is not None
        assert info.item_count >= 1

    def test_list_all_tracks(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        mgr.create(name="A")
        mgr.create(name="B")
        tracks = mgr.list_all()
        assert len(tracks) == 2

    def test_count_tracks(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        assert mgr.count() == 0
        mgr.create()
        assert mgr.count() == 1

    def test_delete_track(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create(name="DelMe")
        assert mgr.count() == 1
        mgr.delete(idx)
        assert mgr.count() == 0

    def test_import_nonexistent_file(self, tmp_path):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        mgr = TrackManager(bridge)

        idx = mgr.create()
        ok = mgr.import_media(idx, str(tmp_path / "nonexistent.wav"))
        assert ok is False
