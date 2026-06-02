"""
Layer 2: SendManager — track send/return management via raw RPR API.
Depends only on bridge.py. Does NOT import fx.py.
"""

import logging
from enum import IntEnum
from typing import Optional, Union

from hermes_core.bridge import ReaperBridge
from hermes_core.audio_utils import db_to_norm

log = logging.getLogger(__name__)


class SendMode(IntEnum):
    """REAPER send modes (I_SENDMODE)."""
    POST_FADER = 0
    PRE_FX = 1
    PRE_FADER = 3

    @classmethod
    def _missing_(cls, value):
        """Accept string lookups like ``SendMode("post-fader")``."""
        if isinstance(value, str):
            key = value.upper().replace("-", "_")
            if key in cls.__members__:
                return cls.__members__[key]
        return None


# REAPER API category constants for track sends/receives/hardware.
_CATEGORY_SENDS = 0      # track-to-track sends
_CATEGORY_RECEIVES = 1   # receives
_CATEGORY_HW_OUT = -1    # hardware outputs

_MODE_VALUES = {"post-fader": 0, "pre-fx": 1, "pre-fader": 3}


class SendManager:
    """Track send CRUD. No create_aux_return — that lives in engine.py."""

    def __init__(self, bridge: ReaperBridge) -> None:
        self._bridge = bridge

    @property
    def bridge(self) -> ReaperBridge:
        return self._bridge

    @property
    def api(self) -> object:
        return self._bridge.api

    # ── Create / Remove ───────────────────────────────────

    def create(
        self,
        src: int,
        dest: int,
        level_db: float = 0.0,
        mode: Union[str, SendMode] = SendMode.POST_FADER,
        pan: float = 0.0,
    ) -> dict:
        """Create a send from src to dest. Returns {category, index}.

        *mode* accepts ``SendMode`` enum values or legacy strings
        ("post-fader", "pre-fx", "pre-fader").
        """
        src_track = self._get_track_ptr(src)
        dest_track = self._get_track_ptr(dest)
        if src_track is None or dest_track is None:
            return {"category": -1, "index": -1}

        send_mode = SendMode(mode) if not isinstance(mode, SendMode) else mode
        level_db = min(level_db, 12.0)
        idx = self.api.CreateTrackSend(src_track, dest_track)

        if idx >= 0:
            vol_norm = db_to_norm(level_db)
            self.api.SetTrackSendInfo_Value(
                src_track, _CATEGORY_SENDS, idx, "D_VOL", vol_norm
            )
            self.api.SetTrackSendInfo_Value(
                src_track, _CATEGORY_SENDS, idx, "D_PAN", pan
            )
            self.api.SetTrackSendInfo_Value(
                src_track, _CATEGORY_SENDS, idx, "I_SENDMODE", int(send_mode)
            )

        return {"category": _CATEGORY_SENDS, "index": idx}

    def remove(self, src: int, send_idx: int, mode: Union[str, SendMode] = SendMode.POST_FADER) -> None:
        """Remove a send from a track.

        The *mode* parameter is accepted for API compatibility but is
        no longer used as the REAPER category — sends are always
        removed from category 0 (track-to-track sends).
        """
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        self.api.RemoveTrackSend(src_track, _CATEGORY_SENDS, send_idx)

    # ── Properties ────────────────────────────────────────

    def set_level(
        self, src: int, send_idx: int, level_db: float, mode: Union[str, SendMode] = SendMode.POST_FADER
    ) -> None:
        """Set send level in dB.

        The *mode* parameter is accepted for API compatibility;
        send category is always 0 (track-to-track sends).
        """
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        self.api.SetTrackSendInfo_Value(
            src_track, _CATEGORY_SENDS, send_idx, "D_VOL", db_to_norm(level_db)
        )

    def set_pan(
        self, src: int, send_idx: int, pan: float, mode: Union[str, SendMode] = SendMode.POST_FADER
    ) -> None:
        """Set send pan (-1.0 to 1.0).

        The *mode* parameter is accepted for API compatibility;
        send category is always 0 (track-to-track sends).
        """
        pan = max(-1.0, min(1.0, pan))
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        self.api.SetTrackSendInfo_Value(
            src_track, _CATEGORY_SENDS, send_idx, "D_PAN", pan
        )

    def set_mute(
        self, src: int, send_idx: int, mute: bool, mode: Union[str, SendMode] = SendMode.POST_FADER
    ) -> None:
        """Mute or unmute a send.

        The *mode* parameter is accepted for API compatibility;
        send category is always 0 (track-to-track sends).
        """
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        self.api.SetTrackSendInfo_Value(
            src_track, _CATEGORY_SENDS, send_idx, "B_MUTE", 1.0 if mute else 0.0
        )

    # ── Query ─────────────────────────────────────────────

    def get_info(
        self, src: int, send_idx: int, category: Optional[int] = None
    ) -> Optional[dict]:
        """Return dict with volume, pan, mute, mode for a send, or None."""
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return None

        if category is None:
            for cat in (_CATEGORY_SENDS, _CATEGORY_HW_OUT):
                try:
                    n = self.api.GetTrackNumSends(src_track, cat)
                    if send_idx < n:
                        category = cat
                        break
                except Exception as e:
                    log.debug("Failed to check send category %s: %s", cat, e)
            if category is None:
                return None

        try:
            vol = self.api.GetTrackSendInfo_Value(
                src_track, category, send_idx, "D_VOL"
            )
            pan = self.api.GetTrackSendInfo_Value(
                src_track, category, send_idx, "D_PAN"
            )
            mute = bool(
                self.api.GetTrackSendInfo_Value(
                    src_track, category, send_idx, "B_MUTE"
                )
            )
            mode_val = self.api.GetTrackSendInfo_Value(
                src_track, category, send_idx, "I_SENDMODE"
            )
            return {
                "volume_norm": float(vol),
                "pan": float(pan),
                "mute": mute,
                "mode": int(mode_val),
                "category": category,
            }
        except Exception as e:
            log.debug("Failed to get send info: %s", e)
            return None

    def list_all(self, track_index: int) -> list[dict]:
        """List all sends from a track."""
        src_track = self._get_track_ptr(track_index)
        if src_track is None:
            return []
        result = []
        for category in (_CATEGORY_SENDS, _CATEGORY_HW_OUT):
            try:
                n = self.api.GetTrackNumSends(src_track, category)
            except Exception as e:
                log.debug("Failed to get send count for category %s: %s", category, e)
                n = 0
            for i in range(n):
                info = self.get_info(track_index, i, category)
                if info:
                    info["index"] = i
                    result.append(info)
        return result

    # ── Internal ──────────────────────────────────────────

    def _get_track_ptr(self, index: int) -> Optional[object]:
        """Return a valid track pointer or None."""
        if index < 0:
            return None
        track = self.api.GetTrack(0, index)
        if track is None or "0x0000000000000000" in str(track):
            return None
        return track
