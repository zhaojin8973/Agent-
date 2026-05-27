"""Tests for hermes_core.bus — BusManager with mocked bridge."""

from unittest.mock import MagicMock

import pytest

from hermes_core.bus import BusManager, FolderInfo
from hermes_core.track import TrackManager
from hermes_core.bridge import ReaperBridge
from tests.conftest import require_reaper, clean_project


def _mock_bridge(**api_overrides):
    mock = MagicMock()
    mock.api = MagicMock()
    if "CountTracks" not in api_overrides:
        mock.api.CountTracks = MagicMock(return_value=5)
    for attr, val in api_overrides.items():
        setattr(mock.api, attr, val)
    return mock


@pytest.mark.unit
class TestFolderInfo:
    def test_construction(self):
        child = FolderInfo(index=1, name="Child", depth=0, children=[])
        parent = FolderInfo(index=0, name="Bus", depth=0, children=[child])
        assert parent.name == "Bus"
        assert len(parent.children) == 1

    def test_to_dict(self):
        child = FolderInfo(index=1, name="Child", depth=0, children=[])
        parent = FolderInfo(index=0, name="Bus", depth=0, children=[child])
        d = parent.to_dict()
        assert d["name"] == "Bus"
        assert len(d["children"]) == 1


@pytest.mark.unit
class TestConstruction:
    def test_stores_bridge(self):
        bridge = _mock_bridge()
        assert BusManager(bridge)._bridge is bridge


@pytest.mark.unit
class TestCreateBus:
    def test_creates_empty_bus(self):
        bridge = _mock_bridge(
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
            SetMediaTrackInfo_Value=MagicMock(),
        )
        idx = BusManager(bridge).create_bus(name="FX Bus", child_indices=[])
        assert idx >= 0
        depth_calls = [
            c for c in bridge.api.SetMediaTrackInfo_Value.call_args_list
            if c[0][1] == "I_FOLDERDEPTH" and c[0][2] == 1
        ]
        assert len(depth_calls) == 1

    def test_creates_bus_with_children(self):
        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=5),
            GetTrack=MagicMock(side_effect=lambda p, i: f"(MediaTrack*)0x{i:016x}"),
            InsertTrackAtIndex=MagicMock(),
            GetSetMediaTrackInfo_String=MagicMock(),
            SetMediaTrackInfo_Value=MagicMock(),
        )
        idx = BusManager(bridge).create_bus(name="Drum Bus", child_indices=[2, 3])
        assert idx == 2
        depth_calls = bridge.api.SetMediaTrackInfo_Value.call_args_list
        last_depth = [c for c in depth_calls if c[0][2] == -1]
        assert len(last_depth) == 1

    def test_rejects_out_of_range_child(self):
        bridge = _mock_bridge(CountTracks=MagicMock(return_value=3))
        with pytest.raises(ValueError, match="out of range"):
            BusManager(bridge).create_bus(name="Bad", child_indices=[0, 5])


@pytest.mark.unit
class TestDissolveBus:
    def test_dissolve_resets_folder_depths(self):
        depths = {}
        def _get(tr, param):
            idx = int(tr.split("[")[1].split("]")[0]) if "[" in str(tr) else 1
            return depths.get(idx, 1)
        def _set(tr, param, val):
            depths[int(tr.split("[")[1].split("]")[0])] = int(val)

        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=3),
            GetTrack=MagicMock(side_effect=lambda p, i: f"Track[{i}]"),
            GetMediaTrackInfo_Value=MagicMock(side_effect=_get),
            SetMediaTrackInfo_Value=MagicMock(side_effect=_set),
        )
        depths[1] = 1  # bus parent
        BusManager(bridge).dissolve_bus(1)
        assert depths.get(1) == 0

    def test_raises_for_nonexistent_bus(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        with pytest.raises(ValueError, match="not found"):
            BusManager(bridge).dissolve_bus(99)


@pytest.mark.unit
class TestGetStructure:
    def test_flat_tracks(self):
        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=3),
            GetTrack=MagicMock(side_effect=lambda p, i: f"Track[{i}]"),
            GetTrackDepth=MagicMock(return_value=0),
            GetTrackName=MagicMock(
                side_effect=lambda tr, buf_str, buf_sz: (True, tr, "Track", buf_sz)
            ),
            GetMediaTrackInfo_Value=MagicMock(return_value=0.0),
        )
        tree = BusManager(bridge).get_structure()
        assert len(tree) == 3

    def test_nested_folders(self):
        tracks = {
            0: {"depth": 0, "folder_depth": 1.0, "name": "Parent"},
            1: {"depth": 1, "folder_depth": 0.0, "name": "Child1"},
            2: {"depth": 1, "folder_depth": -1.0, "name": "Child2"},
        }
        def _get_depth(tr):
            return tracks[int(tr.split("[")[1].split("]")[0])]["depth"]
        def _get_fd(tr, param):
            return tracks[int(tr.split("[")[1].split("]")[0])]["folder_depth"]
        def _get_name(tr, buf_str, buf_sz):
            return (True, tr, tracks[int(tr.split("[")[1].split("]")[0])]["name"], buf_sz)

        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=3),
            GetTrack=MagicMock(side_effect=lambda p, i: f"Track[{i}]"),
            GetTrackDepth=MagicMock(side_effect=_get_depth),
            GetMediaTrackInfo_Value=MagicMock(side_effect=_get_fd),
            GetTrackName=MagicMock(side_effect=_get_name),
        )
        tree = BusManager(bridge).get_structure()
        assert len(tree) == 1
        assert tree[0].name == "Parent"
        assert len(tree[0].children) == 2


@pytest.mark.unit
class TestValidate:
    def test_valid_bus(self):
        def _get_fd(track, param):
            return {"0": 1.0, "1": 0.0, "2": -1.0}.get(
                track.split("[")[1].split("]")[0], 0.0
            )

        bridge = _mock_bridge(
            CountTracks=MagicMock(return_value=5),
            GetTrack=MagicMock(side_effect=lambda p, i: f"Track[{i}]"),
            GetMediaTrackInfo_Value=MagicMock(side_effect=_get_fd),
        )
        result = BusManager(bridge).validate(0)
        assert result["valid"] is True

    def test_missing_track(self):
        bridge = _mock_bridge(GetTrack=MagicMock(return_value=None))
        result = BusManager(bridge).validate(99)
        assert result["valid"] is False


@pytest.mark.integration
class TestBusIntegration:
    def test_create_and_validate_bus(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        tm = TrackManager(bridge)
        bm = BusManager(bridge)

        c1 = tm.create(name="Child1")
        c2 = tm.create(name="Child2")
        bus_idx = bm.create_bus("TestBus", [c1, c2])
        assert bus_idx == c1  # bus inserted before first child

        result = bm.validate(bus_idx)
        assert result["valid"] is True
        assert result["child_count"] == 2

    def test_dissolve_bus(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        tm = TrackManager(bridge)
        bm = BusManager(bridge)

        c1 = tm.create(name="C1")
        c2 = tm.create(name="C2")
        bus_idx = bm.create_bus("TempBus", [c1, c2])
        bm.dissolve_bus(bus_idx)

        result = bm.validate(bus_idx)
        assert result["valid"] is False

    def test_get_structure_flat(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        tm = TrackManager(bridge)
        bm = BusManager(bridge)

        tm.create(name="A")
        tm.create(name="B")
        tree = bm.get_structure()
        assert len(tree) == 2

    def test_create_bus_empty_children(self):
        require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        clean_project(bridge)
        bm = BusManager(bridge)

        idx = bm.create_bus("EmptyBus", [])
        result = bm.validate(idx)
        assert result["valid"] is False  # no children
