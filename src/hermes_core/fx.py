"""
Layer 2: FxManager — plugin-agnostic FX chain management via reapy high-level API.
Depends only on bridge.py. No ReaEQ-specific methods. No hardcoded band indices.
"""

import logging
from typing import Union

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)


def _extract_string(result) -> str:
    """Extract a string from REAPER API return variants."""
    if isinstance(result, str):
        return result
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    if isinstance(result, (tuple, list)):
        for value in reversed(result):
            if isinstance(value, str) and value.strip():
                return value
            if isinstance(value, bytes) and value.strip():
                return value.decode("utf-8", errors="replace")
    return ""


class FxManager:
    """FX chain CRUD using reapy's Track/FX/FXParamsList API.

    reapy internally handles ARM64 5-tuple unpacking, prefix stripping,
    and plugin name lookup — no raw RPR needed for FX operations.
    """

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge
        self._project = None

    @property
    def bridge(self):
        return self._bridge

    @property
    def rpr(self):
        return self._bridge.rpr

    def _proj(self):
        """Lazy reapy Project cache."""
        if self._project is None:
            self._project = self.rpr.Project()
        return self._project

    def _track(self, index: int):
        """Return reapy Track at index, or None (for param operations)."""
        proj = self._proj()
        try:
            n = len(proj.tracks)
        except Exception:
            return None
        if index < 0 or index >= n:
            return None
        try:
            return proj.tracks[index]
        except Exception:
            return None

    def _get_track_ptr(self, index: int):
        """Return raw RPR track pointer, or None (for add/remove)."""
        if index < 0:
            return None
        n = self._bridge.api.CountTracks(0)
        if index >= n:
            return None
        track = self._bridge.api.GetTrack(0, index)
        if track is None or "0x0000000000000000" in str(track):
            return None
        return track

    # ── CRUD ──────────────────────────────────────────────

    def _fx_exists_at_index(self, track_index: int, fx_index: int) -> bool:
        """Verify an FX actually exists at the given index via reapy.

        RPR TrackFX_GetFXName has an ARM64 tuple-unpacking bug
        ("too many values to unpack (expected 2)").  We use reapy's
        high-level Track.fxs API instead, which works correctly on
        both ARM64 and x86_64.
        """
        try:
            track = self._track(track_index)
            if track is None:
                return False
            if fx_index < 0 or fx_index >= len(track.fxs):
                return False
            name = track.fxs[fx_index].name
            if not name or name.strip() == "":
                return False
            if name.strip() == "(0)":
                return False
            return True
        except (OSError, RuntimeError, AttributeError):
            return False

    def add(self, track_index: int, fx_name: str, instantiate: bool = True) -> int:
        """Add an FX by name using raw RPR with reapy fallback.

        TrackFX_AddByName can return a valid-looking index even when the
        plugin failed to load (false positive).  After adding via RPR we
        verify the FX actually exists; if the check fails, we fall back
        to reapy's high-level Track.add_fx().
        """
        track = self._get_track_ptr(track_index)
        if track is None:
            return -1
        n_before = self._bridge.api.TrackFX_GetCount(track)
        idx = self._bridge.api.TrackFX_AddByName(
            track, fx_name, False, 1 if instantiate else 0
        )
        n_after = self._bridge.api.TrackFX_GetCount(track)
        if idx < 0 or n_after <= n_before:
            return -1

        # ── Defensive check: did the FX *really* load? ──────────
        if self._fx_exists_at_index(track_index, idx):
            return idx

        # ── RPR false positive — clean up zombie and try reapy ──
        self._bridge.api.TrackFX_Delete(track, idx)
        reapy_track = self._track(track_index)
        if reapy_track is not None:
            try:
                reapy_track.add_fx(fx_name)
                n_final = self._bridge.api.TrackFX_GetCount(track)
                if n_final > n_before:
                    return n_final - 1
            except Exception:
                log.warning(
                    "reapy fallback add_fx('%s') failed on track %d",
                    fx_name, track_index, exc_info=True,
                )
        return -1

    def remove(self, track_index: int, fx_index: int):
        """Remove an FX from a track."""
        track = self._get_track_ptr(track_index)
        if track is None:
            return
        n = self._bridge.api.TrackFX_GetCount(track)
        if fx_index < 0 or fx_index >= n:
            return
        self._bridge.api.TrackFX_Delete(track, fx_index)

    def get_chain(self, track_index: int) -> list[dict]:
        """Return [{index, name, enabled, param_count}, ...] for all FX."""
        track = self._track(track_index)
        if track is None:
            return []
        result = []
        for i, fx in enumerate(track.fxs):
            result.append({
                "index": i,
                "name": fx.name,
                "enabled": fx.is_enabled,
                "param_count": fx.n_params,
            })
        return result

    # ── Parameters (plugin-agnostic) ──────────────────────

    def set_param(
        self,
        track_index: int,
        fx_index: int,
        param: Union[int, str],
        normalized: float,
    ):
        """Set an FX parameter by index or name. Normalized value 0.0-1.0."""
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return
        normalized = max(0.0, min(1.0, normalized))
        track.fxs[fx_index].params[param] = normalized

    def get_param(
        self,
        track_index: int,
        fx_index: int,
        param: Union[int, str],
    ) -> float:
        """Get an FX parameter value by index or name."""
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return 0.0
        try:
            return float(track.fxs[fx_index].params[param])
        except (IndexError, KeyError, TypeError):
            return -1.0  # sentinel — valid normalized range is 0.0-1.0

    def get_param_name(
        self, track_index: int, fx_index: int, param_index: int
    ) -> str:
        """Return display name of a parameter."""
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return ""
        fx = track.fxs[fx_index]
        if param_index < 0 or param_index >= fx.n_params:
            return ""
        try:
            return fx.params[param_index].name
        except Exception:
            return ""

    def get_param_list(
        self, track_index: int, fx_index: int
    ) -> list[dict]:
        """Return [{name, value}, ...] — runtime-discovered all params."""
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return []
        result = []
        for p in track.fxs[fx_index].params:
            result.append({"name": p.name, "value": float(p)})
        return result

    # ── State ─────────────────────────────────────────────

    def set_enabled(self, track_index: int, fx_index: int, enabled: bool):
        """Enable or disable an FX."""
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return
        track.fxs[fx_index].is_enabled = enabled

    # ── Copy / Move ───────────────────────────────────────

    def copy_to(
        self,
        src_track: int,
        src_fx: int,
        dest_track: int,
        dest_pos: int = 0,
        move: bool = False,
    ):
        """Copy or move an FX to another track. dest_pos = insertion position."""
        src = self._track(src_track)
        dst = self._track(dest_track)
        if src is None or dst is None:
            return
        if src_fx < 0 or src_fx >= len(src.fxs):
            return
        fx = src.fxs[src_fx]
        if move:
            fx.move_to_track(dst, dest_pos)
        else:
            fx.copy_to_track(dst, dest_pos)
