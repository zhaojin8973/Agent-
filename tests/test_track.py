"""Tests for hermes_core.track — TrackManager with mocked bridge."""

import struct
from unittest.mock import MagicMock

import numpy as np
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


# ══════════════════════════════════════════════════════════════
# TrackInfo — additional dataclass coverage
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestTrackInfoAdditional:
    """Additional TrackInfo dataclass tests."""

    def test_all_fields_preserved(self):
        t = TrackInfo(
            index=7, name="GtrVerb", volume_db=-12.3, pan=-0.75,
            mute=True, solo=False, fx_count=3, depth=1,
            item_count=8, selected=True,
        )
        d = t.to_dict()
        assert d["index"] == 7
        assert d["name"] == "GtrVerb"
        assert d["volume_db"] == -12.3
        assert d["pan"] == -0.75
        assert d["mute"] is True
        assert d["solo"] is False
        assert d["fx_count"] == 3
        assert d["depth"] == 1
        assert d["item_count"] == 8
        assert d["selected"] is True

    def test_default_name_empty(self):
        t = TrackInfo(
            index=0, name="", volume_db=0.0, pan=0.0,
            mute=False, solo=False, fx_count=0, depth=0,
            item_count=0, selected=False,
        )
        d = t.to_dict()
        assert d["name"] == ""

    def test_to_dict_rounding(self):
        t = TrackInfo(
            index=0, name="X", volume_db=-3.456, pan=0.789,
            mute=False, solo=False, fx_count=0, depth=0,
            item_count=0, selected=False,
        )
        d = t.to_dict()
        assert d["volume_db"] == -3.5  # round to 1 decimal
        assert d["pan"] == 0.79  # round to 2 decimals


# ══════════════════════════════════════════════════════════════
# Null-track paths (_get_track_ptr, _get_prop, _set_prop, setters)
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetTrackPtrNullPaths:
    """Test _get_track_ptr return-None paths."""

    def test_negative_index_returns_none(self):
        """Negative index → return None."""
        bridge = _mock_bridge()
        mgr = TrackManager(bridge)
        result = mgr._get_track_ptr(-1)
        assert result is None

    def test_api_returns_null_track(self):
        """API returns None for a valid index."""
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = TrackManager(bridge)
        result = mgr._get_track_ptr(0)
        assert result is None


@pytest.mark.unit
class TestGetPropNullTrack:
    """Test _get_prop with null track."""

    def test_null_track_returns_zero(self):
        """_get_prop on null track returns 0.0."""
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = TrackManager(bridge)
        result = mgr._get_prop(0, "D_VOL")
        assert result == 0.0

    def test_valid_track_returns_value(self):
        """_get_prop on valid track returns the API value."""
        bridge = _mock_bridge(GetMediaTrackInfo_Value=MagicMock(return_value=0.5))
        mgr = TrackManager(bridge)
        result = mgr._get_prop(0, "D_VOL")
        assert result == 0.5
        bridge.api.GetMediaTrackInfo_Value.assert_called_once()


@pytest.mark.unit
class TestSetPropNullTrack:
    """Test _set_prop with null track (should not call API)."""

    def test_null_track_skips_api_call(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        # null track → _set_prop should not call SetMediaTrackInfo_Value
        mgr._set_prop(-1, "D_VOL", 0.5)
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()


@pytest.mark.unit
class TestSettersWithNullTrack:
    """Test property setters when _get_track_ptr returns None."""

    def test_set_name_null_track(self):
        bridge = _mock_bridge(
            GetTrack=MagicMock(return_value=None),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.set_name(-1, "Ghost")  # negative index → null track
        bridge.api.GetSetMediaTrackInfo_String.assert_not_called()

    def test_set_volume_null_track(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_volume(-1, 0.0)  # negative index → null track
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()

    def test_set_pan_null_track(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_pan(-1, 0.0)
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()

    def test_set_mute_null_track(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_mute(-1, True)
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()

    def test_set_solo_null_track(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_solo(-1, True)
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()

    def test_set_folder_depth_null_track(self):
        bridge = _mock_bridge(SetMediaTrackInfo_Value=MagicMock())
        mgr = TrackManager(bridge)
        mgr.set_folder_depth(-1, 1)
        bridge.api.SetMediaTrackInfo_Value.assert_not_called()


# ══════════════════════════════════════════════════════════════
# set_item_volume tests
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSetItemVolume:
    """Test set_item_volume method."""

    def test_sets_item_volume(self):
        bridge = _mock_bridge(
            GetTrackMediaItem=MagicMock(return_value="(MediaItem*)0x1"),
            SetMediaItemInfo_Value=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.set_item_volume(0, -6.0)
        bridge.api.SetMediaItemInfo_Value.assert_called_once()

    def test_null_track_skips(self):
        bridge = _mock_bridge(
            GetTrack=MagicMock(return_value=None),
            SetMediaItemInfo_Value=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.set_item_volume(0, -6.0)
        bridge.api.SetMediaItemInfo_Value.assert_not_called()

    def test_null_item_skips(self):
        bridge = _mock_bridge(
            GetTrackMediaItem=MagicMock(return_value=None),
            SetMediaItemInfo_Value=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.set_item_volume(0, -6.0)
        bridge.api.SetMediaItemInfo_Value.assert_not_called()

    def test_custom_item_index(self):
        bridge = _mock_bridge(
            GetTrackMediaItem=MagicMock(return_value="(MediaItem*)0x2"),
            SetMediaItemInfo_Value=MagicMock(),
        )
        mgr = TrackManager(bridge)
        mgr.set_item_volume(0, 3.0, item_index=2)
        bridge.api.GetTrackMediaItem.assert_called()


# ══════════════════════════════════════════════════════════════
# get_item_position — error path
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetItemPositionErrorPath:
    """Test get_item_position exception handling."""

    def test_api_exception_returns_zero(self):
        bridge = _mock_bridge(
            GetTrackMediaItem=MagicMock(return_value="(MediaItem*)0x1"),
            GetMediaItemInfo_Value=MagicMock(side_effect=RuntimeError("boom")),
        )
        result = TrackManager(bridge).get_item_position(0, 0)
        assert result == 0.0


# ══════════════════════════════════════════════════════════════
# import_media unit tests
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestImportMediaUnit:
    """Unit tests for import_media with mocked bridge."""

    def test_file_not_found(self):
        """Non-existent file returns False."""
        bridge = _mock_bridge()
        mgr = TrackManager(bridge)
        ok = mgr.import_media(0, "/tmp/nonexistent_hermes_test.wav")
        assert ok is False

    def test_success_path(self, tmp_path):
        """Valid WAV file with mocked bridge returns True."""
        from tests.conftest import make_test_wav
        wav_path = make_test_wav(tmp_path / "unit_import.wav", duration_sec=0.5)
        bridge = _mock_bridge()
        mgr = TrackManager(bridge)
        ok = mgr.import_media(0, str(wav_path))
        assert ok is True

    def test_pcm_source_create_returns_none(self, tmp_path):
        """PCM_Source_CreateFromFile returns None → import fails."""
        from tests.conftest import make_test_wav
        wav_path = make_test_wav(tmp_path / "pcm_fail.wav", duration_sec=0.5)
        bridge = _mock_bridge()
        # Make PCM_Source_CreateFromFile return None
        bridge.rpr.reascript_api.PCM_Source_CreateFromFile.return_value = None
        mgr = TrackManager(bridge)
        ok = mgr.import_media(0, str(wav_path))
        assert ok is False

    def test_general_exception_returns_false(self, tmp_path):
        """Exception during import returns False."""
        from tests.conftest import make_test_wav
        wav_path = make_test_wav(tmp_path / "exc.wav", duration_sec=0.5)
        bridge = _mock_bridge()
        # Make rpr.Project() raise an exception
        bridge.rpr.Project.side_effect = RuntimeError("project access failed")
        mgr = TrackManager(bridge)
        ok = mgr.import_media(0, str(wav_path))
        assert ok is False

    def test_float_wav_conversion_path(self, tmp_path):
        """Float WAV triggers _convert_to_pcm → import succeeds."""
        sr = 48000
        dur = 0.2
        t = np.arange(int(sr * dur)) / sr
        sig = (0.5 * np.sin(2.0 * np.pi * 440 * t)).astype(np.float32)
        wav_bytes = _build_float_wav(sig, sample_rate=sr)
        float_path = tmp_path / "float_import.wav"
        float_path.write_bytes(wav_bytes)

        bridge = _mock_bridge()
        mgr = TrackManager(bridge)
        ok = mgr.import_media(0, str(float_path))
        assert ok is True
        # Temp file should be cleaned up
        # (verification is implicit — no leaked temp files)


# ══════════════════════════════════════════════════════════════
# create — edge cases
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCreateEdgeCases:
    """Test TrackManager.create edge cases."""

    def test_create_without_name(self):
        """Creating track without name does not call set-name API."""
        bridge = _mock_bridge(
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
        )
        mgr = TrackManager(bridge)
        idx = mgr.create(index=0, name="")
        assert idx == 0
        bridge.api.GetSetMediaTrackInfo_String.assert_not_called()


# ══════════════════════════════════════════════════════════════
# _wav_duration_fallback tests
# ══════════════════════════════════════════════════════════════

def _build_wav_bytes(data_frames, sample_rate=44100, channels=1,
                     bits_per_sample=16, fmt_first=True):
    """Build a minimal WAV file as bytes for header testing.

    If *fmt_first* is True, the ``fmt `` chunk comes before ``data``
    (standard order).  Otherwise ``data`` comes first (tests reorder
    resilience of ``_wav_duration_fallback``).
    """
    fmt_tag = 1 if bits_per_sample != 32 else 3
    block_align = channels * bits_per_sample // 8
    byte_rate = sample_rate * block_align
    fmt_data = struct.pack(
        "<HHIIHH", fmt_tag, channels, sample_rate,
        byte_rate, block_align, bits_per_sample,
    )
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_data)) + fmt_data

    raw = np.asarray(data_frames, dtype=np.int16).tobytes()
    data_chunk = b"data" + struct.pack("<I", len(raw)) + raw

    if fmt_first:
        body = fmt_chunk + data_chunk
    else:
        body = data_chunk + fmt_chunk

    riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE"
    return riff + body


@pytest.mark.unit
class TestWavDurationFallback:
    """Test _wav_duration_fallback with constructed WAV headers."""

    def test_valid_standard_wav(self, tmp_path):
        """Standard PCM WAV returns correct duration."""
        from hermes_core.track import _wav_duration_fallback
        sr = 48000
        n_samples = 48000  # 1 second
        wav_bytes = _build_wav_bytes(
            np.zeros(n_samples, dtype=np.int16),
            sample_rate=sr,
        )
        p = tmp_path / "std.wav"
        p.write_bytes(wav_bytes)
        duration = _wav_duration_fallback(str(p))
        assert duration == pytest.approx(1.0, abs=0.01)

    def test_data_before_fmt_chunk(self, tmp_path):
        """WAV with data chunk before fmt chunk still parses correctly."""
        from hermes_core.track import _wav_duration_fallback
        sr = 44100
        n_samples = 22050  # 0.5 seconds
        wav_bytes = _build_wav_bytes(
            np.zeros(n_samples, dtype=np.int16),
            sample_rate=sr,
            fmt_first=False,
        )
        p = tmp_path / "data_first.wav"
        p.write_bytes(wav_bytes)
        duration = _wav_duration_fallback(str(p))
        assert duration == pytest.approx(0.5, abs=0.01)

    def test_not_a_wav_missing_riff(self, tmp_path):
        """File without RIFF header raises ValueError."""
        from hermes_core.track import _wav_duration_fallback
        p = tmp_path / "not_wav.bin"
        p.write_bytes(b"XXXXsome garbage data here")
        with pytest.raises(ValueError, match="Not a WAV file"):
            _wav_duration_fallback(str(p))

    def test_not_a_wav_missing_wave(self, tmp_path):
        """File with RIFF but no WAVE id raises ValueError."""
        from hermes_core.track import _wav_duration_fallback
        bad = b"RIFF" + struct.pack("<I", 4) + b"XXXX"
        p = tmp_path / "no_wave.wav"
        p.write_bytes(bad)
        with pytest.raises(ValueError, match="Not a WAV file"):
            _wav_duration_fallback(str(p))

    def test_no_data_chunk_raises(self, tmp_path):
        """WAV without data chunk raises ValueError."""
        from hermes_core.track import _wav_duration_fallback
        # Build WAV with fmt but NO data chunk.
        # Put a junk chunk with full header to avoid struct.unpack errors.
        sr = 44100
        fmt_chunk = b"fmt " + struct.pack("<I", 16) + struct.pack(
            "<HHIIHH", 1, 1, sr, sr * 2, 2, 16,
        )
        junk_chunk = b"junk" + struct.pack("<I", 8) + b"XXXXXXXX"
        body = fmt_chunk + junk_chunk
        riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE"
        p = tmp_path / "no_data.wav"
        p.write_bytes(riff + body)
        with pytest.raises(ValueError, match="data chunk not found"):
            _wav_duration_fallback(str(p))


# ══════════════════════════════════════════════════════════════
# _convert_to_pcm tests
# ══════════════════════════════════════════════════════════════

def _build_float_wav(samples, sample_rate=48000, channels=1):
    """Build a 32-bit IEEE float WAV as bytes."""
    data = np.asarray(samples, dtype=np.float32).tobytes()
    block_align = channels * 4
    byte_rate = sample_rate * block_align
    fmt_data = struct.pack(
        "<HHIIHH",
        3,          # WAVE_FORMAT_IEEE_FLOAT
        channels,
        sample_rate,
        byte_rate,
        block_align,
        32,         # bits_per_sample
    )
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt_data)) + fmt_data
    data_chunk = b"data" + struct.pack("<I", len(data)) + data
    body = fmt_chunk + data_chunk
    riff = b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE"
    return riff + body


@pytest.mark.unit
class TestConvertToPcm:
    """Test _convert_to_pcm with float WAV files."""

    def test_converts_float_wav_to_pcm(self, tmp_path):
        """32-bit float WAV is converted to 16-bit PCM temp file."""
        from hermes_core.track import _convert_to_pcm
        sr = 48000
        dur = 0.2
        t = np.arange(int(sr * dur)) / sr
        sig = (0.8 * np.sin(2.0 * np.pi * 440 * t)).astype(np.float32)

        wav_bytes = _build_float_wav(sig, sample_rate=sr)
        float_path = tmp_path / "float.wav"
        float_path.write_bytes(wav_bytes)

        pcm_path = _convert_to_pcm(str(float_path))
        assert pcm_path.endswith(".wav")
        # Check it's readable by stdlib wave
        import wave
        with wave.open(pcm_path, "rb") as wf:
            assert wf.getsampwidth() == 2
            assert wf.getframerate() == sr
            assert wf.getnchannels() == 1

    def test_fallback_duration_on_float_wav(self, tmp_path):
        """_wav_duration_fallback works on float WAV files."""
        from hermes_core.track import _wav_duration_fallback
        sr = 44100
        n = 44100  # 1 second
        sig = np.sin(2.0 * np.pi * 440 * np.arange(n) / sr).astype(np.float32)
        wav_bytes = _build_float_wav(sig, sample_rate=sr)
        p = tmp_path / "float_dur.wav"
        p.write_bytes(wav_bytes)
        dur = _wav_duration_fallback(str(p))
        assert dur == pytest.approx(1.0, abs=0.02)

    def test_non_wav_file_raises(self, tmp_path):
        """Non-WAV file passed to _convert_to_pcm raises ValueError."""
        from hermes_core.track import _convert_to_pcm
        p = tmp_path / "garbage.wav"
        p.write_bytes(b"not a real WAV file at all")
        with pytest.raises(ValueError):
            _convert_to_pcm(str(p))
