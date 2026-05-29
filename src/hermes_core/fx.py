"""
Layer 2: FxManager — plugin-agnostic FX chain management via reapy high-level API.
Depends only on bridge.py. No ReaEQ-specific methods. No hardcoded band indices.
"""

import logging
from typing import Union

from hermes_core.bridge import ReaperBridge, _extract_reaper_string

log = logging.getLogger(__name__)


def _resolve_param_index(api, track_ptr, fx_index: int, param,
                        cache: dict[str, int] | None = None) -> int:
    """Resolve a param name or int to a param index. Returns -1 if not found.

    When *cache* is provided (a dict keyed by lower-case param name) the
    lookup is served from cache on cache hits, and the cache is populated
    on misses after a full scan.
    """
    if isinstance(param, int):
        return param
    name_lower = param.lower()

    # Cache hit — avoid scanning all parameters
    if cache is not None and name_lower in cache:
        return cache[name_lower]

    n = api.TrackFX_GetNumParams(track_ptr, fx_index)
    for i in range(n):
        raw = api.TrackFX_GetParamName(
            track_ptr, fx_index, i, "", 256
        )
        if isinstance(raw, (tuple, list)):
            ok = bool(raw[0]) if len(raw) > 0 else False
            name_buf = raw[4] if len(raw) > 4 else ""
        else:
            ok, name_buf = True, raw
        resolved = _extract_reaper_string(name_buf) if ok else ""
        if cache is not None and resolved:
            cache[resolved.lower()] = i
        if resolved.lower() == name_lower:
            return i
    return -1


class FxManager:
    """FX chain CRUD using reapy's Track/FX/FXParamsList API.

    reapy internally handles ARM64 5-tuple unpacking, prefix stripping,
    and plugin name lookup — no raw RPR needed for FX operations.
    """

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge
        self._project = None
        self._param_cache: dict[tuple[int, int], dict[str, int]] = {}

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
        """Return raw RPR track pointer, or None (for add/remove).

        Pass ``-1`` for the master track.
        """
        if index == -1:
            track = self._bridge.api.GetMasterTrack(0)
            if track is None or "0x0000000000000000" in str(track):
                return None
            return track
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

        # Invalidate param cache — FX chain indices shifted.
        self._param_cache.pop((track_index, idx), None)

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

    def add_master(self, fx_name: str, instantiate: bool = True) -> int:
        """Add an FX to the master track. Returns FX index or -1 on failure."""
        track = self._get_track_ptr(-1)
        if track is None:
            return -1
        n_before = self._bridge.api.TrackFX_GetCount(track)
        idx = self._bridge.api.TrackFX_AddByName(
            track, fx_name, False, 1 if instantiate else 0
        )
        n_after = self._bridge.api.TrackFX_GetCount(track)
        if idx < 0 or n_after <= n_before:
            return -1
        self._param_cache.pop((-1, idx), None)
        return idx

    def remove(self, track_index: int, fx_index: int):
        """Remove an FX from a track."""
        track = self._get_track_ptr(track_index)
        if track is None:
            return
        n = self._bridge.api.TrackFX_GetCount(track)
        if fx_index < 0 or fx_index >= n:
            return
        self._bridge.api.TrackFX_Delete(track, fx_index)
        self._param_cache.pop((track_index, fx_index), None)

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
    ) -> bool:
        """Set an FX parameter by index or name. Normalized value 0.0-1.0.

        Master track (index=-1) uses raw RPR; regular tracks use reapy.
        Returns True if the parameter was set, False if the track or
        parameter name/index could not be resolved.
        """
        normalized = max(0.0, min(1.0, normalized))

        # Master track: use raw RPR API
        if track_index == -1:
            track = self._get_track_ptr(-1)
            if track is None:
                return False
            cache_key = (-1, fx_index)
            if cache_key not in self._param_cache:
                self._param_cache[cache_key] = {}
            param_idx = _resolve_param_index(
                self._bridge.api, track, fx_index, param,
                cache=self._param_cache[cache_key],
            )
            if param_idx < 0:
                return False
            self._bridge.api.TrackFX_SetParam(
                track, fx_index, param_idx, normalized
            )
            return True

        # Regular tracks: use reapy
        track = self._track(track_index)
        if track is None or fx_index < 0 or fx_index >= len(track.fxs):
            return False
        track.fxs[fx_index].params[param] = normalized
        return True

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
        """Return [{name, value}, ...] — runtime-discovered all params.

        Uses raw RPR API so master track (index=-1) is supported.
        """
        if track_index == -1:
            track = self._get_track_ptr(-1)
        else:
            track = self._get_track_ptr(track_index)
        if track is None:
            return []
        n_params = self._bridge.api.TrackFX_GetNumParams(track, fx_index)
        if n_params <= 0:
            return []
        result = []
        for i in range(n_params):
            raw_gpn = self._bridge.api.TrackFX_GetParamName(
                track, fx_index, i, "", 256
            )
            if isinstance(raw_gpn, (tuple, list)):
                ok = bool(raw_gpn[0]) if len(raw_gpn) > 0 else False
                name_buf = raw_gpn[4] if len(raw_gpn) > 4 else ""
            else:
                ok, name_buf = True, raw_gpn
            name = _extract_reaper_string(name_buf) if ok else f"param_{i}"

            raw_gp = self._bridge.api.TrackFX_GetParam(track, fx_index, i, 0.0, 0.0)
            if isinstance(raw_gp, (tuple, list)):
                ok_v = bool(raw_gp[0]) if len(raw_gp) > 0 else False
                val = raw_gp[0] if len(raw_gp) > 0 else 0.0
            else:
                ok_v, val = True, raw_gp
            value = float(val) if ok_v else 0.0
            result.append({"name": name, "value": value})
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
