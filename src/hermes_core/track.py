"""
Track management — CRUD, properties, and media import.
Depends only on bridge.py.
"""

import os
import logging
import wave
from dataclasses import dataclass
from typing import Optional

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)


@dataclass
class TrackInfo:
    index: int
    name: str
    volume_db: float
    pan: float
    mute: bool
    solo: bool
    fx_count: int
    depth: int
    item_count: int
    selected: bool

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "volume_db": round(self.volume_db, 1),
            "pan": round(self.pan, 2),
            "mute": self.mute,
            "solo": self.solo,
            "fx_count": self.fx_count,
            "depth": self.depth,
            "item_count": self.item_count,
            "selected": self.selected,
        }


class TrackManager:
    """Track CRUD, property management, and media import."""

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge

    # ── CRUD ───────────────────────────────────────────────

    def create(self, index: int = -1, name: str = "") -> int:
        """Insert a new track. Returns track index."""
        api = self._bridge.api
        actual = index if index >= 0 else api.CountTracks(0)
        api.InsertTrackAtIndex(actual, True)
        if name:
            track = api.GetTrack(0, actual)
            api.GetSetMediaTrackInfo_String(track, "P_NAME", name, True)
        log.info("Track created at %d: %s", actual, name)
        return actual

    def delete(self, index: int):
        """Delete a track by index."""
        api = self._bridge.api
        track = api.GetTrack(0, index)
        if track:
            api.DeleteTrack(track)

    # ── Properties ──────────────────────────────────────────

    def set_name(self, index: int, name: str):
        track = self._bridge.api.GetTrack(0, index)
        if track:
            self._bridge.api.GetSetMediaTrackInfo_String(track, "P_NAME", name, True)

    def set_volume(self, index: int, db: float):
        """Set track fader volume in dB. 0dB = unity."""
        track = self._bridge.api.GetTrack(0, index)
        if track:
            self._bridge.api.SetMediaTrackInfo_Value(track, "D_VOL", self._db_to_norm(db))

    def set_pan(self, index: int, pan: float):
        """Set track pan. 0=center, -1=left, 1=right."""
        track = self._bridge.api.GetTrack(0, index)
        if track:
            self._bridge.api.SetMediaTrackInfo_Value(track, "D_PAN", pan)

    def set_mute(self, index: int, mute: bool):
        track = self._bridge.api.GetTrack(0, index)
        if track:
            self._bridge.api.SetMediaTrackInfo_Value(track, "B_MUTE", 1.0 if mute else 0.0)

    def set_folder_depth(self, index: int, depth: int):
        """Set folder depth. 0=normal, 1=parent, -1=last child."""
        track = self._bridge.api.GetTrack(0, index)
        if track:
            self._bridge.api.SetMediaTrackInfo_Value(track, "I_FOLDERDEPTH", depth)

    # ── Query ────────────────────────────────────────────────

    def count(self) -> int:
        """Return total number of tracks."""
        return self._bridge.api.CountTracks(0)

    def get(self, index: int) -> Optional[TrackInfo]:
        """Return TrackInfo for the track at the given index, or None."""
        api = self._bridge.api
        track = api.GetTrack(0, index)
        if track is None:
            return None

        try:
            _, _, name, _ = api.GetTrackName(track, "", 256)
            name = name or ""

            return TrackInfo(
                index=index,
                name=name.strip(),
                volume_db=self._norm_to_db(float(api.GetMediaTrackInfo_Value(track, "D_VOL"))),
                pan=float(api.GetMediaTrackInfo_Value(track, "D_PAN")),
                mute=bool(api.GetMediaTrackInfo_Value(track, "B_MUTE")),
                solo=bool(api.GetMediaTrackInfo_Value(track, "I_SOLO")),
                fx_count=api.TrackFX_GetCount(track),
                depth=int(api.GetMediaTrackInfo_Value(track, "I_FOLDERDEPTH")),
                item_count=api.CountTrackMediaItems(track),
                selected=bool(api.IsTrackSelected(track)),
            )
        except Exception:
            return None

    def list_all(self) -> list[TrackInfo]:
        """Return TrackInfo for all tracks."""
        result = []
        for i in range(self.count()):
            info = self.get(i)
            if info is not None:
                result.append(info)
        return result

    # ── Media Import ────────────────────────────────────────

    def import_media(self, track_index: int, file_path: str,
                     position: float = 0.0) -> bool:
        """Import an audio file onto a track at the given position (seconds).
        Uses high-level reapy API to bypass ARM64 RPR_InsertMedia bug.
        Returns True on success.
        """
        if not os.path.isfile(file_path):
            return False
        try:
            with wave.open(file_path, "rb") as wf:
                duration = wf.getnframes() / wf.getframerate() if wf.getframerate() > 0 else 1.0
            rpr = self._bridge.rpr
            api = rpr.reascript_api
            # Create a real PCM source from the file
            pcm_source = api.PCM_Source_CreateFromFile(file_path)
            proj = rpr.Project()
            tr = proj.tracks[track_index]
            item = tr.add_item(start=position, length=duration)
            take = item.add_take()
            api.SetMediaItemTake_Source(take.id, pcm_source)
            return True
        except Exception as e:
            log.warning("import_media failed for %s: %s", file_path, e)
            return False

    def import_stems(self, stem_map: dict[str, str],
                     position: float = 0.0) -> list[dict]:
        """Import multiple stems, creating one track per stem.

        stem_map: {track_name: file_path}
        Returns list of {name, track_index, file_path, success}.
        """
        results = []
        for name, path in stem_map.items():
            idx = self.create(name=name)
            ok = self.import_media(idx, path, position)
            results.append({
                "name": name,
                "track_index": idx,
                "file_path": path,
                "success": ok,
            })
        return results

    def get_item_position(self, track_index: int,
                          item_index: int = 0) -> float:
        """Return D_POSITION (seconds) of the specified item."""
        api = self._bridge.api
        track = api.GetTrack(0, track_index)
        if track is None:
            return 0.0
        item = api.GetTrackMediaItem(track, item_index)
        if item is None:
            return 0.0
        try:
            return float(api.GetMediaItemInfo_Value(item, "D_POSITION"))
        except Exception:
            return 0.0

    # ── Internal ──────────────────────────────────────────────

    @staticmethod
    def _db_to_norm(db: float) -> float:
        if db <= -150:
            return 0.0
        return 10.0 ** (db / 20.0)

    @staticmethod
    def _norm_to_db(norm: float) -> float:
        if norm <= 0.0:
            return -150.0
        import math
        return 20.0 * math.log10(norm)
