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
        if not bounds or bounds not in _VALID_BOUNDS:
            raise ValueError(
                f"Invalid bounds: '{bounds}'. Must be one of {_VALID_BOUNDS}"
            )
        if not fmt or fmt not in _VALID_FORMATS:
            raise ValueError(
                f"Invalid format: '{fmt}'. Must be one of {_VALID_FORMATS}"
            )

        if not os.path.isdir(output_dir):
            os.makedirs(output_dir)

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

        # Poll for output file
        output_path = os.path.join(output_dir, "render.wav")
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
        api.GetSetLoopTimeRange(True, False, start, end, False)

    def get_render_settings(self) -> dict:
        """Return current REAPER render configuration as a dict."""
        api = self._bridge.api
        fmt = api.GetSetProjectInfo_String(0, "RENDER_FORMAT", "", False)
        bounds = api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", 0, False)
        sr = api.GetSetProjectInfo(0, "RENDER_SRATE", 0, False)
        return {"format": fmt, "bounds": bounds, "sample_rate": sr}
