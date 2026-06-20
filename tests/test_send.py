"""Tests for hermes_core.send — SendManager with mocked bridge."""

import math
from unittest.mock import MagicMock

import pytest

from hermes_core.send import SendManager
from hermes_core.audio_utils import db_to_norm
from hermes_core.track import TrackManager
from hermes_core.bridge import ReaperBridge


def _mock_bridge(**api_overrides):
    mock = MagicMock()
    mock.api = MagicMock()
    mock.api.GetTrack = MagicMock(
        side_effect=lambda p, i: f"(MediaTrack*)0x{i+1:016x}" if i >= 0 else None
    )
    for attr, val in api_overrides.items():
        setattr(mock.api, attr, val)
    return mock


@pytest.mark.unit
class TestDbToNorm:
    def test_unity(self):
        assert db_to_norm(0.0) == pytest.approx(1.0)

    def test_minus_6_db(self):
        assert db_to_norm(-6.0) == pytest.approx(0.5, abs=0.01)

    def test_minus_infinity(self):
        assert db_to_norm(-150.0) == 0.0

    def test_plus_6_db(self):
        assert db_to_norm(6.0) == pytest.approx(2.0, abs=0.05)


@pytest.mark.unit
class TestConstruction:
    def test_stores_bridge(self):
        bridge = _mock_bridge()
        mgr = SendManager(bridge)
        assert mgr._bridge is bridge


@pytest.mark.unit
class TestCreate:
    def test_creates_send_with_level_pan_mode(self):
        bridge = _mock_bridge(
            GetTrack=MagicMock(side_effect=lambda p, i: f"(MediaTrack*)0x{i+1:016x}"),
            CreateTrackSend=MagicMock(return_value=0),
            SetTrackSendInfo_Value=MagicMock(),
        )
        mgr = SendManager(bridge)
        result = mgr.create(src=0, dest=1, level_db=-6.0, mode="post-fader", pan=0.5)
        assert result == {"category": 0, "index": 0}
        bridge.api.CreateTrackSend.assert_called_once()
        assert bridge.api.SetTrackSendInfo_Value.call_count >= 3

    def test_create_with_pre_fx_mode(self):
        bridge = _mock_bridge(
            CreateTrackSend=MagicMock(return_value=0),
            SetTrackSendInfo_Value=MagicMock(),
        )
        mgr = SendManager(bridge)
        result = mgr.create(src=0, dest=1, level_db=0.0, mode="pre-fx")
        # Category is always 0 (sends), not send_mode.
        assert result["category"] == 0
        # I_SENDMODE should still be set to pre-fx (1).
        sendmode_calls = [
            c for c in bridge.api.SetTrackSendInfo_Value.call_args_list
            if c[0][3] == "I_SENDMODE"
        ]
        assert len(sendmode_calls) == 1
        assert sendmode_calls[0][0][4] == 1

    def test_create_rejects_invalid_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = SendManager(bridge)
        result = mgr.create(src=0, dest=1)
        assert result == {"category": -1, "index": -1}


@pytest.mark.unit
class TestRemove:
    def test_removes_send(self):
        bridge = _mock_bridge(
            RemoveTrackSend=MagicMock(),
        )
        mgr = SendManager(bridge)
        mgr.remove(src=0, send_idx=0)
        bridge.api.RemoveTrackSend.assert_called_once()

    def test_handles_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = SendManager(bridge)
        mgr.remove(src=0, send_idx=0)


@pytest.mark.unit
class TestSetProperties:
    def test_set_level(self):
        bridge = _mock_bridge(
            SetTrackSendInfo_Value=MagicMock(),
        )
        mgr = SendManager(bridge)
        mgr.set_level(0, 0, -12.0)
        bridge.api.SetTrackSendInfo_Value.assert_called()

    def test_set_pan_clamps(self):
        bridge = _mock_bridge()
        mgr = SendManager(bridge)
        mgr.set_pan(0, 0, 2.0)
        pan_calls = [
            c for c in bridge.api.SetTrackSendInfo_Value.call_args_list
            if c[0][3] == "D_PAN"
        ]
        assert len(pan_calls) == 1
        assert pan_calls[0][0][4] == 1.0  # clamped value

    def test_set_mute(self):
        bridge = _mock_bridge()
        mgr = SendManager(bridge)
        mgr.set_mute(0, 0, True)
        mute_calls = [
            c for c in bridge.api.SetTrackSendInfo_Value.call_args_list
            if c[0][3] == "B_MUTE"
        ]
        assert len(mute_calls) == 1


@pytest.mark.unit
class TestGetInfo:
    def test_returns_send_info(self):
        def _mock_get(track, cat, idx, param):
            return {"D_VOL": 0.5, "D_PAN": 0.0, "B_MUTE": 0.0, "I_SENDMODE": 0}.get(param, 0.0)

        bridge = _mock_bridge(
            GetTrackNumSends=MagicMock(return_value=2),
            GetTrackSendInfo_Value=MagicMock(side_effect=_mock_get),
        )
        mgr = SendManager(bridge)
        info = mgr.get_info(0, 0)
        assert info is not None
        assert info["volume_norm"] == 0.5
        assert info["mode"] == 0

    def test_returns_none_for_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = SendManager(bridge)
        assert mgr.get_info(0, 0) is None


@pytest.mark.unit
class TestListAll:
    def test_lists_all_sends(self):
        def _mock_get(track, cat, idx, param):
            return 0.5

        bridge = _mock_bridge(
            GetTrackNumSends=MagicMock(return_value=1),
            GetTrackSendInfo_Value=MagicMock(side_effect=_mock_get),
        )
        mgr = SendManager(bridge)
        result = mgr.list_all(0)
        # Now iterates over 2 categories (sends + HW outputs) not 3.
        assert len(result) == 2
        assert all("volume_norm" in r for r in result)
        assert all("index" in r for r in result)

    def test_handles_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        mgr = SendManager(bridge)
        assert mgr.list_all(0) == []


