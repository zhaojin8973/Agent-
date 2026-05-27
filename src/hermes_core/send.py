"""
Layer 2: SendManager — track send/return management via raw RPR API.
Depends only on bridge.py. Does NOT import fx.py.
"""

import math
import logging
from typing import Optional

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)

_MODE_VALUES = {"post-fader": 0, "pre-fx": 1, "pre-fader": 3}


def _db_to_norm(db: float) -> float:
    """Convert dB to REAPER normalized send volume (0..1)."""
    if not math.isfinite(db) or db <= -150:
        return 0.0
    return 10 ** (db / 20)


class SendManager:
    """Track send CRUD. No create_aux_return — that lives in engine.py."""

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge

    @property
    def bridge(self):
        return self._bridge

    @property
    def api(self):
        return self._bridge.api

    # ── Create / Remove ───────────────────────────────────

    def create(
        self,
        src: int,
        dest: int,
        level_db: float = 0.0,
        mode: str = "post-fader",
        pan: float = 0.0,
    ) -> dict:
        """Create a send from src to dest. Returns {category, index}."""
        src_track = self._get_track_ptr(src)
        dest_track = self._get_track_ptr(dest)
        if src_track is None or dest_track is None:
            return {"category": -1, "index": -1}

        send_mode = _MODE_VALUES.get(mode, 0)
        level_db = min(level_db, 12.0)
        idx = self.api.CreateTrackSend(src_track, dest_track)

        if idx >= 0:
            vol_norm = _db_to_norm(level_db)
            self.api.SetTrackSendInfo_Value(
                src_track, send_mode, idx, "D_VOL", vol_norm
            )
            self.api.SetTrackSendInfo_Value(
                src_track, send_mode, idx, "D_PAN", pan
            )
            self.api.SetTrackSendInfo_Value(
                src_track, send_mode, idx, "I_SENDMODE", send_mode
            )

        return {"category": send_mode, "index": idx}

    def remove(self, src: int, send_idx: int, mode: str = "post-fader"):
        """Remove a send from a track."""
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        send_mode = _MODE_VALUES.get(mode, 0)
        self.api.RemoveTrackSend(src_track, send_mode, send_idx)

    # ── Properties ────────────────────────────────────────

    def set_level(
        self, src: int, send_idx: int, level_db: float, mode: str = "post-fader"
    ):
        """Set send level in dB."""
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        send_mode = _MODE_VALUES.get(mode, 0)
        self.api.SetTrackSendInfo_Value(
            src_track, send_mode, send_idx, "D_VOL", _db_to_norm(level_db)
        )

    def set_pan(
        self, src: int, send_idx: int, pan: float, mode: str = "post-fader"
    ):
        """Set send pan (-1.0 to 1.0)."""
        pan = max(-1.0, min(1.0, pan))
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        send_mode = _MODE_VALUES.get(mode, 0)
        self.api.SetTrackSendInfo_Value(
            src_track, send_mode, send_idx, "D_PAN", pan
        )

    def set_mute(
        self, src: int, send_idx: int, mute: bool, mode: str = "post-fader"
    ):
        """Mute or unmute a send."""
        src_track = self._get_track_ptr(src)
        if src_track is None:
            return
        send_mode = _MODE_VALUES.get(mode, 0)
        self.api.SetTrackSendInfo_Value(
            src_track, send_mode, send_idx, "B_MUTE", 1.0 if mute else 0.0
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
            for cat in (0, 3, 1):
                try:
                    n = self.api.GetTrackNumSends(src_track, cat)
                    if send_idx < n:
                        category = cat
                        break
                except Exception:
                    pass
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
        except Exception:
            return None

    def list_all(self, track_index: int) -> list[dict]:
        """List all sends from a track."""
        src_track = self._get_track_ptr(track_index)
        if src_track is None:
            return []
        result = []
        for category in (0, 3, 1):
            try:
                n = self.api.GetTrackNumSends(src_track, category)
            except Exception:
                n = 0
            for i in range(n):
                info = self.get_info(track_index, i, category)
                if info:
                    info["index"] = i
                    result.append(info)
        return result

    # ── Internal ──────────────────────────────────────────

    def _get_track_ptr(self, index: int):
        """Return a valid track pointer or None."""
        if index < 0:
            return None
        track = self.api.GetTrack(0, index)
        if track is None or "0x0000000000000000" in str(track):
            return None
        return track
