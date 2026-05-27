"""
RenderManager — REAPER project rendering via Main_OnCommand.
Depends only on bridge.py.
"""

import logging
import os
import time

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)

_VALID_BOUNDS = ("entire_project", "time_selection")
_VALID_FORMATS = ("wav", "flac", "mp3")

# 4-byte sink codes from reapy docs (format name reversed)
_SINK_CODES = {"wav": "evaw", "flac": "calf", "mp3": "3pm "}

_BOUNDS_FLAGS = {"entire_project": 1, "time_selection": 2}


class RenderManager:
    """REAPER project rendering via the non-modal render command (42230)."""

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge

    # ── Private helpers ───────────────────────────────────────

    def _can_render(self) -> bool:
        """Check project has at least one media item to render.

        Iterates all tracks and returns True if any track contains
        at least one media item.  Returns False for empty projects
        or projects with tracks that have no media items.
        """
        api = self._bridge.api
        n = api.CountTracks(0)
        for i in range(n):
            tr = api.GetTrack(0, i)
            if tr and api.CountTrackMediaItems(tr) > 0:
                return True
        return False

    @staticmethod
    def _check_output_writable(output_dir: str) -> bool:
        """Return True if output_dir is writable (exists or can be created)."""
        try:
            os.makedirs(output_dir, exist_ok=True)
            test_path = os.path.join(output_dir, ".hermes_write_test")
            with open(test_path, "w") as f:
                f.write("")
            os.remove(test_path)
            return True
        except OSError:
            return False

    def _preflight_check(self, bounds: str, fmt: str, output_dir: str) -> dict:
        """Run all render preflight checks.

        Returns {"passed": bool, "failures": [{"reason": str, "detail": str}, ...]}.
        """
        failures: list[dict] = []

        if bounds not in _VALID_BOUNDS:
            failures.append({
                "reason": "invalid_bounds",
                "detail": f"'{bounds}' not in {_VALID_BOUNDS}",
            })

        if fmt not in _VALID_FORMATS:
            failures.append({
                "reason": "invalid_format",
                "detail": f"'{fmt}' not in {_VALID_FORMATS}",
            })

        if not self._can_render():
            failures.append({
                "reason": "nothing_to_render",
                "detail": "Project has no media items on any track",
            })

        if bounds == "time_selection":
            start, end = self.get_time_selection_range()
            if end <= start:
                failures.append({
                    "reason": "nothing_to_render",
                    "detail": f"Time selection is zero-length (start={start}, end={end})",
                })

        if not self._check_output_writable(output_dir):
            failures.append({
                "reason": "output_not_writable",
                "detail": f"Cannot write to output directory: {output_dir}",
            })

        if failures:
            log.warning("Render preflight FAILED: %s", failures)
        return {"passed": len(failures) == 0, "failures": failures}

    # ── Public API ──────────────────────────────────────────

    def render_mix(
        self,
        output_dir: str,
        bounds: str = "entire_project",
        fmt: str = "wav",
        sample_rate: int = 0,
        timeout: float = 120.0,
    ) -> dict:
        """Render the project and return {output_path, error, ...}.

        Configures REAPER's render settings, triggers a non-modal render,
        and polls until the output file appears or timeout expires.
        """
        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

        # Unified preflight -- catches all known failure paths before render
        preflight = self._preflight_check(bounds, fmt, output_dir)
        if not preflight["passed"]:
            return {
                "output_path": None,
                "error": preflight["failures"][0]["reason"],
                "preflight": preflight,
            }

        api = self._bridge.api
        sink_code = _SINK_CODES[fmt]
        bounds_flag = _BOUNDS_FLAGS[bounds]

        # String config: format + output location
        api.GetSetProjectInfo_String(0, "RENDER_FORMAT", sink_code, True)
        api.GetSetProjectInfo_String(0, "RENDER_FILE", output_dir, True)
        api.GetSetProjectInfo_String(0, "RENDER_PATTERN", "render", True)

        # Numeric config: bounds, channels, settings, sample rate
        api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", bounds_flag, True)
        api.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)
        api.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, True)
        if sample_rate > 0:
            api.GetSetProjectInfo(0, "RENDER_SRATE", sample_rate, True)

        # Trigger non-modal render
        api.Main_OnCommand(42230, 0)

        # Poll for output file (extension varies by format)
        output_path = os.path.join(output_dir, f"render.{fmt}")
        start = time.time()
        while not os.path.exists(output_path):
            if time.time() - start > timeout:
                return {"error": "timeout", "output_path": None, "timed_out": True}
            time.sleep(0.1)

        return {"output_path": output_path}

    def set_time_selection(self, start: float, end: float):
        """Set REAPER's time selection loop range."""
        start = max(0.0, start)
        if end < start:
            return
        api = self._bridge.api
        api.GetSet_LoopTimeRange(True, False, start, end, False)

    def get_time_selection_range(self) -> tuple[float, float]:
        """Return (start, end) of the current time selection in seconds."""
        api = self._bridge.api
        _, _, start, end, _ = api.GetSet_LoopTimeRange(False, False, 0, 0, False)
        return (start, end)

    def get_render_settings(self) -> dict:
        """Return current REAPER render configuration as a dict."""
        api = self._bridge.api
        fmt = api.GetSetProjectInfo_String(0, "RENDER_FORMAT", "", False)
        bounds = api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 0, False)
        sr = api.GetSetProjectInfo(0, "RENDER_SRATE", 0, False)
        return {"format": fmt, "bounds": bounds, "sample_rate": sr}
