"""
Track management — CRUD, properties, and media import.
Depends only on bridge.py.
"""

import os
import math
import struct
import tempfile
import logging
import wave

import numpy as np
from dataclasses import dataclass
from typing import Optional

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)


def _wav_duration_fallback(file_path: str) -> float:
    """Get duration from a WAV header when stdlib wave rejects it (e.g. float WAV).

    Raises ``ValueError`` when the file cannot be parsed so callers get a
    clear signal instead of a silent 300-second fallback that would create
    wrong-length media items on the timeline.
    """
    # Initialise to safe defaults in case the ``data`` chunk appears
    # before the ``fmt `` chunk (WAV spec allows any chunk order).
    channels = 1
    sr = 44100
    bits = 16

    with open(file_path, "rb") as fh:
        if fh.read(4) != b"RIFF":
            raise ValueError(f"Not a WAV file (missing RIFF header): {file_path}")
        fh.read(4)  # file size
        if fh.read(4) != b"WAVE":
            raise ValueError(f"Not a WAV file (missing WAVE id): {file_path}")
        while True:
            chunk_id = fh.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack("<I", fh.read(4))[0]
            if chunk_id == b"fmt ":
                fmt_data = fh.read(chunk_size)
                channels = struct.unpack_from("<H", fmt_data, 2)[0]
                sr = struct.unpack_from("<I", fmt_data, 4)[0]
                bits = struct.unpack_from("<H", fmt_data, 14)[0] if chunk_size >= 16 else 16
            elif chunk_id == b"data":
                nframes = chunk_size // max((channels * bits // 8), 1)
                return nframes / max(sr, 1)
            else:
                fh.read(chunk_size)
    raise ValueError(f"WAV data chunk not found in: {file_path}")


def _convert_to_pcm(file_path: str) -> str:
    """Convert a float WAV to 16-bit PCM temp file that REAPER can import."""
    from hermes_core.signal import _read_wav_manual
    sw, sr, channels, raw = _read_wav_manual(file_path)
    if sw == 4:
        pcm_f32 = np.frombuffer(raw, dtype=np.float32)
    elif sw == 3:
        padded = np.frombuffer(raw + b"\x00", dtype=np.uint8
            )[: len(raw) // 3 * 3].reshape(-1, 3)
        i32 = (padded[:, 0].astype(np.int32) + padded[:, 1].astype(np.int32) * 256
               + padded[:, 2].astype(np.int32) * 65536)
        i32[i32 >= 8388608] -= 16777216
        pcm_f32 = i32.astype(np.float64).astype(np.float32) / 8388608.0
    else:
        raise ValueError(f"Cannot convert WAV with sample width {sw}")

    pcm_f32 = pcm_f32.reshape(-1, channels)
    i16 = (pcm_f32 * 32767.0).clip(-32768, 32767).astype(np.int16)

    fd, tmp_path = tempfile.mkstemp(suffix=".wav", prefix="hermes_pcm_")
    os.close(fd)
    with wave.open(tmp_path, "wb") as wf:
        wf.setnchannels(channels)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(i16.tobytes())
    return tmp_path


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

    # ── Generic property accessors ──────────────────────────

    def _get_track_ptr(self, index: int):
        """Return a raw RPR track pointer or None."""
        if index < 0:
            return None
        track = self._bridge.api.GetTrack(0, index)
        if track is None:
            return None
        return track

    def _get_prop(self, index: int, key: str) -> float:
        """Read a track property via ``GetMediaTrackInfo_Value``."""
        tr = self._get_track_ptr(index)
        if tr is None:
            return 0.0
        return float(self._bridge.api.GetMediaTrackInfo_Value(tr, key))

    def _set_prop(self, index: int, key: str, value: float):
        """Write a track property via ``SetMediaTrackInfo_Value``."""
        tr = self._get_track_ptr(index)
        if tr is not None:
            self._bridge.api.SetMediaTrackInfo_Value(tr, key, value)

    # ── Properties ──────────────────────────────────────────

    def set_name(self, index: int, name: str):
        tr = self._get_track_ptr(index)
        if tr:
            self._bridge.api.GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)

    def set_volume(self, index: int, db: float):
        """Set track fader volume in dB. 0dB = unity."""
        self._set_prop(index, "D_VOL", self._db_to_norm(db))

    def set_pan(self, index: int, pan: float):
        """Set track pan. 0=center, -1=left, 1=right."""
        self._set_prop(index, "D_PAN", pan)

    def set_mute(self, index: int, mute: bool):
        self._set_prop(index, "B_MUTE", 1.0 if mute else 0.0)

    def set_solo(self, index: int, solo: bool):
        """Set track solo state."""
        self._set_prop(index, "I_SOLO", 1.0 if solo else 0.0)

    def set_folder_depth(self, index: int, depth: int):
        """Set folder depth. 0=normal, 1=parent, -1=last child."""
        self._set_prop(index, "I_FOLDERDEPTH", depth)

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
        import_path = os.path.abspath(file_path)
        is_temp = False
        try:
            try:
                with wave.open(file_path, "rb") as wf:
                    duration = wf.getnframes() / max(wf.getframerate(), 1)
            except wave.Error:
                # Float WAV — convert to 16-bit PCM temp file for import
                import_path = _convert_to_pcm(file_path)
                is_temp = True
                duration = _wav_duration_fallback(file_path)
            rpr = self._bridge.rpr
            api = rpr.reascript_api
            pcm_source = api.PCM_Source_CreateFromFile(import_path)
            if pcm_source is None:
                log.warning("import_media: PCM_Source_CreateFromFile returned None for %s", file_path)
                return False
            proj = rpr.Project()
            tr = proj.tracks[track_index]
            item = tr.add_item(start=position, length=duration)
            take = item.add_take()
            api.SetMediaItemTake_Source(take.id, pcm_source)
            return True
        except Exception as e:
            log.warning("import_media failed for %s: %s", file_path, e)
            return False
        finally:
            if is_temp and os.path.isfile(import_path):
                try:
                    os.unlink(import_path)
                except OSError:
                    pass

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

    def set_item_volume(self, track_index: int, db: float,
                        item_index: int = 0):
        """Set clip gain (item volume) on a media item. Pre-FX, pre-fader."""
        api = self._bridge.api
        track = api.GetTrack(0, track_index)
        if track is None:
            return
        item = api.GetTrackMediaItem(track, item_index)
        if item is None:
            return
        api.SetMediaItemInfo_Value(item, "D_VOL", self._db_to_norm(db))

    # ── Internal ──────────────────────────────────────────────

    @staticmethod
    def _db_to_norm(db: float) -> float:
        if not math.isfinite(db) or db <= -150:
            return 0.0
        return 10.0 ** (db / 20.0)

    @staticmethod
    def _norm_to_db(norm: float) -> float:
        if norm <= 0.0:
            return -150.0
        return 20.0 * math.log10(norm)
