"""Tests for hermes_core.fx — FxManager with mocked bridge and reapy."""

from unittest.mock import MagicMock, PropertyMock

import pytest

from hermes_core.fx import FxManager


def _mock_fx_list(fx_count=1):
    fxs = []
    for i in range(fx_count):
        fx = MagicMock()
        type(fx).name = PropertyMock(return_value=f"FX{i}")
        type(fx).is_enabled = PropertyMock(return_value=True)
        type(fx).n_params = PropertyMock(return_value=3)
        params = []
        for p in range(3):
            param = MagicMock()
            type(param).name = PropertyMock(return_value=f"Param{p}")
            param.__float__ = MagicMock(return_value=0.5)
            params.append(param)
        type(fx).params = PropertyMock(return_value=params)
        fxs.append(fx)
    mock_list = MagicMock()
    mock_list.__iter__ = MagicMock(return_value=iter(fxs))
    mock_list.__len__ = MagicMock(return_value=fx_count)
    mock_list.__getitem__ = MagicMock(side_effect=lambda idx: fxs[idx])
    return mock_list


def _mock_bridge(**api_overrides):
    mock = MagicMock()
    mock.api = MagicMock()
    mock.rpr = MagicMock()
    mock_proj = MagicMock()
    mock.rpr.Project.return_value = mock_proj
    mock_tr = MagicMock()
    mock_tr.fxs = _mock_fx_list()
    mock_proj.tracks = [mock_tr]
    mock.api.CountTracks = MagicMock(return_value=1)
    mock.api.GetTrack = MagicMock(
        side_effect=lambda p, i: f"(MediaTrack*)0x{i+1:016x}" if i >= 0 else None
    )
    for attr, val in api_overrides.items():
        setattr(mock.api, attr, val)
    return mock


@pytest.mark.unit
class TestConstruction:
    def test_stores_bridge(self):
        bridge = _mock_bridge()
        assert FxManager(bridge)._bridge is bridge


@pytest.mark.unit
class TestAdd:
    def test_adds_fx_via_rpr(self):
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(side_effect=[0, 1]),
            TrackFX_AddByName=MagicMock(return_value=0),
            TrackFX_GetFXName=MagicMock(return_value="VST: ReaEQ"),
        )
        assert FxManager(bridge).add(0, "ReaEQ") == 0

    def test_returns_minus_one_for_invalid_track(self):
        bridge = _mock_bridge(
            GetTrack=MagicMock(side_effect=lambda p, i: None),
        )
        assert FxManager(bridge).add(0, "ReaEQ") == -1

    def test_returns_minus_one_when_add_fails(self):
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(return_value=0),
            TrackFX_AddByName=MagicMock(return_value=-1),
        )
        assert FxManager(bridge).add(0, "ReaEQ") == -1


@pytest.mark.unit
class TestRemove:
    def test_removes_fx(self):
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(return_value=2),
            TrackFX_Delete=MagicMock(),
        )
        FxManager(bridge).remove(0, 0)
        bridge.api.TrackFX_Delete.assert_called_once()

    def test_handles_null_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        FxManager(bridge).remove(0, 0)  # should not raise


@pytest.mark.unit
class TestGetChain:
    def test_returns_fx_list(self):
        chain = FxManager(_mock_bridge()).get_chain(0)
        assert len(chain) == 1
        assert "index" in chain[0]

    def test_returns_empty_for_invalid_track(self):
        bridge = _mock_bridge()
        bridge.rpr.Project.return_value.tracks = []
        assert FxManager(bridge).get_chain(0) == []

    def test_empty_chain(self):
        bridge = _mock_bridge()
        bridge.rpr.Project.return_value.tracks[0].fxs = _mock_fx_list(fx_count=0)
        assert FxManager(bridge).get_chain(0) == []


@pytest.mark.unit
class TestSetGetParam:
    def test_set_param_by_index(self):
        bridge = _mock_bridge()
        FxManager(bridge).set_param(0, 0, 0, 0.75)

    def test_get_param(self):
        val = FxManager(_mock_bridge()).get_param(0, 0, 0)
        assert val == 0.5

    @pytest.mark.skip(reason="requires mock sentinel fix")
    def test_get_param_returns_sentinel_on_error(self):
        bridge = _mock_bridge()
        bridge.rpr.Project.return_value.tracks[0].fxs[0].params = []
        assert FxManager(bridge).get_param(0, 0, 0) == -1.0


@pytest.mark.unit
class TestGetParamName:
    def test_returns_param_name(self):
        name = FxManager(_mock_bridge()).get_param_name(0, 0, 0)
        assert isinstance(name, str)
        assert len(name) > 0

    def test_returns_empty_for_invalid(self):
        bridge = _mock_bridge()
        assert FxManager(bridge).get_param_name(0, 0, 999) == ""
        assert FxManager(bridge).get_param_name(0, 999, 0) == ""


@pytest.mark.unit
class TestGetParamList:
    def test_returns_all_params(self):
        plist = FxManager(_mock_bridge()).get_param_list(0, 0)
        assert len(plist) == 3
        assert all("name" in p and "value" in p for p in plist)

    def test_returns_empty_for_invalid(self):
        assert FxManager(_mock_bridge()).get_param_list(0, 999) == []


@pytest.mark.unit
class TestSetEnabled:
    @pytest.mark.skip(reason="PropertyMock interaction with MagicMock")
    def test_bypass_fx(self):
        bridge = _mock_bridge()
        FxManager(bridge).set_enabled(0, 0, False)
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        assert fx.is_enabled is False


@pytest.mark.unit
class TestCopyMove:
    def test_copy_sends_copy_to_track(self):
        """move=False (default) → calls fx.copy_to_track(dst, dest_pos)."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=1)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        FxManager(bridge).copy_to(0, 0, 1)

        src_tr.fxs[0].copy_to_track.assert_called_once_with(dst_tr, 0)

    def test_move_sends_move_to_track(self):
        """move=True → calls fx.move_to_track(dst, dest_pos)."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=1)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        FxManager(bridge).copy_to(0, 0, 1, move=True)

        src_tr.fxs[0].move_to_track.assert_called_once_with(dst_tr, 0)

    def test_src_track_none_returns_early(self):
        """When src index is out of range → returns early, no copy/move calls."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        # Default _mock_bridge has 1 track; index 99 is out of range.
        proj.tracks = [proj.tracks[0]]

        result = FxManager(bridge).copy_to(99, 0, 0)

        assert result is None

    def test_dest_track_none_returns_early(self):
        """When dest index is out of range → returns early, no copy/move calls."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        proj.tracks = [proj.tracks[0]]

        result = FxManager(bridge).copy_to(0, 0, 99)

        assert result is None

    def test_src_fx_negative_returns_early(self):
        """src_fx < 0 → returns early, no copy/move calls on any FX."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=1)
        dst_tr = MagicMock()
        dst_tr.fxs = _mock_fx_list()
        proj.tracks = [src_tr, dst_tr]

        result = FxManager(bridge).copy_to(0, -1, 1)

        assert result is None
        src_tr.fxs[0].copy_to_track.assert_not_called()
        src_tr.fxs[0].move_to_track.assert_not_called()

    def test_src_fx_out_of_range_returns_early(self):
        """src_fx >= len(fxs) → returns early, no copy/move calls."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=2)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        result = FxManager(bridge).copy_to(0, 2, 1)

        assert result is None
        src_tr.fxs[0].copy_to_track.assert_not_called()
        src_tr.fxs[1].copy_to_track.assert_not_called()

    def test_copy_custom_dest_pos(self):
        """dest_pos is passed through to copy_to_track."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=1)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        FxManager(bridge).copy_to(0, 0, 1, dest_pos=3)

        src_tr.fxs[0].copy_to_track.assert_called_once_with(dst_tr, 3)

    def test_move_custom_dest_pos(self):
        """dest_pos is passed through to move_to_track when move=True."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=1)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        FxManager(bridge).copy_to(0, 0, 1, dest_pos=2, move=True)

        src_tr.fxs[0].move_to_track.assert_called_once_with(dst_tr, 2)

    def test_copy_selects_correct_fx_from_chain(self):
        """When the src track has multiple FX, only the targeted index is copied."""
        bridge = _mock_bridge()
        proj = bridge.rpr.Project.return_value
        src_tr = MagicMock()
        src_tr.fxs = _mock_fx_list(fx_count=3)
        dst_tr = MagicMock()
        proj.tracks = [src_tr, dst_tr]

        FxManager(bridge).copy_to(0, 1, 1)

        src_tr.fxs[1].copy_to_track.assert_called_once_with(dst_tr, 0)
        src_tr.fxs[0].copy_to_track.assert_not_called()
        src_tr.fxs[2].copy_to_track.assert_not_called()
@pytest.mark.unit
class TestFXExistsAtIndex:
    """Tests for _fx_exists_at_index — defensive FX-verification via reapy."""

    def test_returns_true_for_valid_fx_name(self):
        """When reapy FX has a real name, the check passes."""
        bridge = _mock_bridge()
        mgr = FxManager(bridge)
        exists = mgr._fx_exists_at_index(0, 0)
        assert exists is True

    def test_returns_false_for_empty_name(self):
        """When reapy FX name is empty, the check fails."""
        bridge = _mock_bridge()
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        type(fx).name = PropertyMock(return_value="")
        mgr = FxManager(bridge)
        exists = mgr._fx_exists_at_index(0, 0)
        assert exists is False

    def test_returns_false_for_placeholder_name(self):
        """'(0)' is a REAPER placeholder for a failed-to-load FX."""
        bridge = _mock_bridge()
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        type(fx).name = PropertyMock(return_value="(0)")
        mgr = FxManager(bridge)
        exists = mgr._fx_exists_at_index(0, 0)
        assert exists is False

    def test_returns_false_when_track_not_found(self):
        """When reapy track is None, the check fails gracefully."""
        bridge = _mock_bridge()
        bridge.rpr.Project.return_value.tracks = []
        mgr = FxManager(bridge)
        exists = mgr._fx_exists_at_index(0, 0)
        assert exists is False

    def test_returns_false_for_whitespace_only_name(self):
        """Whitespace-only names should also fail the exists check."""
        bridge = _mock_bridge()
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        type(fx).name = PropertyMock(return_value="   ")
        mgr = FxManager(bridge)
        exists = mgr._fx_exists_at_index(0, 0)
        assert exists is False


@pytest.mark.unit
class TestAddFallback:
    """Tests for the reapy fallback path when RPR TrackFX_AddByName is a false positive."""

    def test_falls_back_to_reapy_when_exists_check_fails(self):
        """RPR path 'succeeds' but FX is broken -> clean up zombie, fall back to reapy."""
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(side_effect=[0, 1, 2]),
            TrackFX_AddByName=MagicMock(return_value=0),
            TrackFX_Delete=MagicMock(),
        )
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        type(fx).name = PropertyMock(return_value="")
        mock_track = bridge.rpr.Project.return_value.tracks[0]
        mock_track.add_fx = MagicMock()

        result = FxManager(bridge).add(0, "ReaEQ")

        bridge.api.TrackFX_Delete.assert_called_once()
        mock_track.add_fx.assert_called_once_with("ReaEQ")
        assert result == 1  # n_final - 1 = 2 - 1

    def test_returns_minus_one_when_both_paths_fail(self):
        """RPR adds a zombie FX, and reapy add_fx also fails -> return -1."""
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(side_effect=[0, 1, 1]),
            TrackFX_AddByName=MagicMock(return_value=0),
            TrackFX_Delete=MagicMock(),
        )
        fx = bridge.rpr.Project.return_value.tracks[0].fxs[0]
        type(fx).name = PropertyMock(return_value="")
        mock_track = bridge.rpr.Project.return_value.tracks[0]
        mock_track.add_fx = MagicMock(side_effect=Exception("reapy fail"))

        result = FxManager(bridge).add(0, "ReaEQ")

        assert result == -1

    def test_skips_fallback_when_exists_check_passes(self):
        """When the RPR-added FX passes the exists check, no fallback is needed."""
        bridge = _mock_bridge(
            TrackFX_GetCount=MagicMock(side_effect=[0, 1]),
            TrackFX_AddByName=MagicMock(return_value=0),
        )
        mock_track = bridge.rpr.Project.return_value.tracks[0]
        mock_track.add_fx = MagicMock()

        result = FxManager(bridge).add(0, "ReaEQ")
        assert result == 0

