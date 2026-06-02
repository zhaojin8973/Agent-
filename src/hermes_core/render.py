"""
RenderManager — REAPER project rendering via Main_OnCommand.
Depends only on bridge.py.
"""

import base64
import logging
import os
import time
from enum import IntEnum

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)

# REAPER Main_OnCommand action ID for the non-modal render dialog.
_REAPER_RENDER_ACTION = 42230


class RenderFormat(IntEnum):
    """REAPER render output formats (sink codes)."""
    WAV = 0
    FLAC = 1
    MP3 = 2

    @classmethod
    def _missing_(cls, value):
        """Accept string lookups like ``RenderFormat("wav")``."""
        if isinstance(value, str):
            key = value.upper()
            if key in cls.__members__:
                return cls.__members__[key]
        return None


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

    @staticmethod
    def _check_disk_space(output_dir: str, required_mb: float = 500.0) -> dict:
        """Check that *output_dir* has at least *required_mb* free space.

        Returns ``{"ok": bool, "free_mb": float, "required_mb": float}``.
        """
        import shutil
        try:
            usage = shutil.disk_usage(output_dir)
            free_mb = usage.free / (1024 * 1024)
            return {
                "ok": free_mb >= required_mb,
                "free_mb": round(free_mb, 1),
                "required_mb": required_mb,
            }
        except OSError:
            return {"ok": False, "free_mb": 0.0, "required_mb": required_mb}

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

        disk = self._check_disk_space(output_dir)
        if not disk["ok"]:
            failures.append({
                "reason": "insufficient_disk_space",
                "detail": (
                    f"Only {disk['free_mb']:.0f} MB free, "
                    f"need at least {disk['required_mb']:.0f} MB"
                ),
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

        # RENDER_FORMAT expects base64(sink_code + b'\x18\x00\x01')
        fmt_encoded = base64.b64encode(
            sink_code.encode() + b"\x18\x00\x01"
        ).decode()
        api.GetSetProjectInfo_String(0, "RENDER_FORMAT", fmt_encoded, True)
        api.GetSetProjectInfo_String(0, "RENDER_FILE", output_dir, True)
        api.GetSetProjectInfo_String(0, "RENDER_PATTERN", "render", True)

        # Numeric config: bounds, channels, settings, sample rate
        api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", bounds_flag, True)
        api.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)
        api.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, True)
        if sample_rate > 0:
            api.GetSetProjectInfo(0, "RENDER_SRATE", sample_rate, True)

        # 聚焦 REAPER 窗口以防止渲染输出静音（macOS 专有）
        self._bridge.focus_reaper()
        time.sleep(0.3)

        # Trigger non-modal render
        api.Main_OnCommand(_REAPER_RENDER_ACTION, 0)

        # Poll for output file (extension varies by format)
        output_path = os.path.join(output_dir, f"render.{fmt}")
        start = time.time()
        while not os.path.exists(output_path):
            if time.time() - start > timeout:
                return {"error": "timeout", "output_path": None, "timed_out": True}
            time.sleep(0.1)

        return {"output_path": output_path}

    def render_with_retry(
        self,
        output_dir: str,
        bounds: str = "entire_project",
        fmt: str = "wav",
        sample_rate: int = 0,
        timeout: float = 120.0,
        max_retries: int = 3,
    ) -> dict:
        """Render with automatic retry on transient failures.

        Returns the first successful result, or the last error.
        """
        last_error = None
        for attempt in range(1, max_retries + 1):
            result = self.render_mix(
                output_dir=output_dir,
                bounds=bounds,
                fmt=fmt,
                sample_rate=sample_rate,
                timeout=timeout,
            )
            if result.get("output_path") is not None:
                if attempt > 1:
                    log.info("Render succeeded on attempt %d", attempt)
                return result
            last_error = result.get("error", "unknown")
            if attempt < max_retries:
                delay = 1.0 * (2 ** (attempt - 1))
                log.warning(
                    "Render attempt %d failed (%s) — retrying in %.1fs",
                    attempt, last_error, delay,
                )
                time.sleep(delay)

        log.error("Render failed after %d attempts: %s", max_retries, last_error)
        return {"output_path": None, "error": last_error, "retries_exhausted": True}

    def render_with_silence_retry(
        self,
        output_dir: str,
        bounds: str = "entire_project",
        fmt: str = "wav",
        sample_rate: int = 0,
        timeout: float = 120.0,
        silence_threshold_db: float = -80.0,
    ) -> dict:
        """Render with silence detection — retries once if output is near-silent.

        The silence bug occurs when REAPER window is not in focus: the render
        command succeeds (produces a WAV file) but the output is silent
        (LUFS ≈ -120, peak ≈ -99).

        On silence detection:
        1. Focus REAPER window via AppleScript
        2. Wait 0.5s for focus switch
        3. Re-render

        Returns the render result dict.  If the retry also produces silence,
        the silent result is returned with ``"silent": True`` so the caller
        can decide what to do.
        """
        # ── 第一次渲染（render_mix 内部已调用 focus_reaper） ──
        result = self.render_mix(
            output_dir=output_dir, bounds=bounds, fmt=fmt,
            sample_rate=sample_rate, timeout=timeout,
        )

        if result.get("output_path") is None:
            return result

        # ── 静音检测 ──
        if not self._is_output_silent(
            result["output_path"], silence_threshold_db,
        ):
            return result

        # ── 检测到静音 → 强制聚焦 + 重试 ──
        log.warning(
            "Render produced silent output — REAPER window may not be in focus. "
            "Re-focusing and retrying ..."
        )
        self._bridge.focus_reaper()
        time.sleep(0.5)

        # 清理静音文件
        import os
        try:
            os.remove(result["output_path"])
        except OSError:
            pass

        retry_result = self.render_mix(
            output_dir=output_dir, bounds=bounds, fmt=fmt,
            sample_rate=sample_rate, timeout=timeout,
        )

        if retry_result.get("output_path") and self._is_output_silent(
            retry_result["output_path"], silence_threshold_db,
        ):
            log.error(
                "Retry also produced silent output — please ensure REAPER "
                "window is visible and not minimized."
            )
            retry_result["silent"] = True

        return retry_result

    def _is_output_silent(
        self, file_path: str, threshold_db: float = -80.0,
    ) -> bool:
        """Check if a rendered WAV is near-silent.

        Uses lightweight peak detection (numpy) without importing the full
        SignalAnalyzer to avoid circular dependencies.
        """
        import os
        if not os.path.exists(file_path):
            return False

        try:
            import soundfile as sf
            import numpy as np

            data, _ = sf.read(file_path, dtype="float64")
            if data.size == 0:
                return True
            peak = float(np.max(np.abs(data)))
            peak_db = float(20.0 * np.log10(peak + 1e-12))
            is_silent = bool(peak_db < threshold_db)
            if is_silent:
                log.warning(
                    "Silence detected: peak=%.1f dB (threshold=%.1f dB) — %s",
                    peak_db, threshold_db, file_path,
                )
            return is_silent
        except Exception as exc:
            log.debug("_is_output_silent failed: %s", exc)
            return False

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
