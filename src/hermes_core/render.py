"""
RenderManager — REAPER project rendering via Main_OnCommand.
Depends only on bridge.py.

Supports multi-format rendering (WAV/MP3/FLAC), post-render verification,
and exponential-backoff file polling for render completion detection.
"""

import base64
import logging
import math
import os
import struct
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

# MP3 bitrate mapping: kbps → REAPER internal index (LAME CBR)
_MP3_BITRATE_INDEX = {
    320: 0, 256: 1, 224: 2, 192: 3, 160: 4,
    128: 5, 112: 6, 96: 7, 80: 8, 64: 9,
    56: 10, 48: 11, 40: 12, 32: 13,
}

# Default file extension per format
_FORMAT_EXT = {"wav": ".wav", "flac": ".flac", "mp3": ".mp3"}


class RenderManager:
    """REAPER project rendering via the non-modal render command (42230)."""

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge

    # ── Private helpers ───────────────────────────────────────

    @staticmethod
    def _get_format_encoding(fmt: str,
                             mp3_bitrate_kbps: int = 320) -> str:
        """Build the base64-encoded RENDER_FORMAT string for *fmt*.

        REAPER stores the render format as a base64 blob::

            4 bytes  — sink ID (little-endian reversed, e.g. ``"evaw"``)
            4 bytes  — format flags (uint32 LE)

        For MP3 the flags encode CBR bitrate index (0=320 … 13=32).
        For WAV/FLAC the flags use ``0x00010018`` (24-bit PCM).

        Returns a base64-encoded string suitable for
        ``GetSetProjectInfo_String(0, "RENDER_FORMAT", ...)``.
        """
        sink = _SINK_CODES[fmt]
        if fmt == "mp3":
            idx = _MP3_BITRATE_INDEX.get(mp3_bitrate_kbps, 0)
            flags = idx  # CBR mode: bitrate index in low nibble
            return base64.b64encode(
                sink.encode() + struct.pack("<I", flags)
            ).decode()
        else:
            # WAV / FLAC: 24-bit PCM flag
            flags = 0x00010018
            return base64.b64encode(
                sink.encode() + struct.pack("<I", flags)
            ).decode()

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

    def _wait_for_render(self, output_path: str,
                         timeout: float = 300.0) -> bool:
        """Wait for render to complete with exponential-backoff polling.

        Prevents tight 100 ms loops by starting with a short interval
        and doubling it up to a maximum of 2 seconds.  Once the file
        appears, verifies it has stopped growing (stable size) before
        returning.

        Returns ``True`` when a stable output file is detected, or
        ``False`` on timeout.
        """
        if os.path.exists(output_path):
            return True

        start = time.time()
        interval = 0.1    # 100 ms initial poll
        max_interval = 2.0

        while True:
            elapsed = time.time() - start
            if elapsed > timeout:
                log.warning("_wait_for_render timed out after %.1fs", elapsed)
                return False

            if os.path.exists(output_path):
                # File appeared — check it has stopped growing
                try:
                    size_before = os.path.getsize(output_path)
                except OSError:
                    size_before = 0
                time.sleep(max(0.05, interval * 0.5))
                try:
                    if (os.path.exists(output_path)
                            and os.path.getsize(output_path) == size_before
                            and size_before > 0):
                        return True
                except OSError:
                    pass

            time.sleep(interval)
            interval = min(interval * 2, max_interval)

    def render_mix(
        self,
        output_dir: str,
        bounds: str = "entire_project",
        fmt: str = "wav",
        sample_rate: int = 0,
        timeout: float = 120.0,
        *,
        mp3_bitrate_kbps: int = 320,
        flac_compression: int = 5,
    ) -> dict:
        """Render the project and return {output_path, error, ...}.

        Configures REAPER's render settings, triggers a non-modal render,
        and polls until the output file appears or timeout expires.

        Parameters
        ----------
        output_dir : str
            Directory for the render output file.
        bounds : str
            ``"entire_project"`` or ``"time_selection"``.
        fmt : str
            ``"wav"``, ``"mp3"``, or ``"flac"``.
        sample_rate : int
            Target sample rate (0 = project default).
        timeout : float
            Maximum wait time in seconds.
        mp3_bitrate_kbps : int
            MP3 CBR bitrate (32–320, default 320).  Ignored for non-MP3.
        flac_compression : int
            FLAC compression level (0–8, default 5).  Ignored for non-FLAC.
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
        bounds_flag = _BOUNDS_FLAGS[bounds]

        # Build format-specific encoding
        fmt_encoded = self._get_format_encoding(fmt, mp3_bitrate_kbps)
        api.GetSetProjectInfo_String(0, "RENDER_FORMAT", fmt_encoded, True)
        api.GetSetProjectInfo_String(0, "RENDER_FILE", output_dir, True)
        api.GetSetProjectInfo_String(0, "RENDER_PATTERN", "render", True)

        # Numeric config: bounds, channels, settings, sample rate
        api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", bounds_flag, True)
        api.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)

        # RENDER_SETTINGS: encode FLAC compression or use default
        if fmt == "flac":
            # REAPER FLAC compression: 0–8 maps to settings bits
            render_settings = max(0, min(8, flac_compression))
        else:
            render_settings = 0
        api.GetSetProjectInfo(0, "RENDER_SETTINGS", render_settings, True)

        if sample_rate > 0:
            api.GetSetProjectInfo(0, "RENDER_SRATE", sample_rate, True)

        # 聚焦 REAPER 窗口以防止渲染输出静音（macOS 专有）
        self._bridge.focus_reaper()
        time.sleep(0.3)

        # Trigger non-modal render
        api.Main_OnCommand(_REAPER_RENDER_ACTION, 0)

        # Wait for output file with backoff polling
        output_path = os.path.join(output_dir, f"render.{fmt}")
        if not self._wait_for_render(output_path, timeout):
            return {"error": "timeout", "output_path": None, "timed_out": True}

        return {"output_path": output_path}

    def render_mp3(
        self,
        output_dir: str,
        bitrate_kbps: int = 320,
        bounds: str = "entire_project",
        sample_rate: int = 0,
        timeout: float = 120.0,
    ) -> dict:
        """Render the project as MP3 via REAPER's built-in LAME encoder.

        Parameters
        ----------
        output_dir : str
            Output directory.
        bitrate_kbps : int
            CBR bitrate in kbps (32, 40, 48, 56, 64, 80, 96, 112, 128,
            160, 192, 224, 256, 320).  Default 320.
        bounds : str
            ``"entire_project"`` or ``"time_selection"``.
        sample_rate : int
            Target sample rate (0 = project default).
        timeout : float
            Maximum wait time in seconds.
        """
        return self.render_mix(
            output_dir=output_dir,
            bounds=bounds,
            fmt="mp3",
            sample_rate=sample_rate,
            timeout=timeout,
            mp3_bitrate_kbps=bitrate_kbps,
        )

    def render_flac(
        self,
        output_dir: str,
        compression_level: int = 5,
        bounds: str = "entire_project",
        sample_rate: int = 0,
        timeout: float = 120.0,
    ) -> dict:
        """Render the project as FLAC (lossless).

        Parameters
        ----------
        output_dir : str
            Output directory.
        compression_level : int
            FLAC compression level 0–8 (0 = fastest, 8 = smallest).
            Default 5.
        bounds : str
            ``"entire_project"`` or ``"time_selection"``.
        sample_rate : int
            Target sample rate (0 = project default).
        timeout : float
            Maximum wait time in seconds.
        """
        return self.render_mix(
            output_dir=output_dir,
            bounds=bounds,
            fmt="flac",
            sample_rate=sample_rate,
            timeout=timeout,
            flac_compression=compression_level,
        )

    def render_with_retry(
        self,
        output_dir: str,
        bounds: str = "entire_project",
        fmt: str = "wav",
        sample_rate: int = 0,
        timeout: float = 120.0,
        max_retries: int = 3,
        *,
        mp3_bitrate_kbps: int = 320,
        flac_compression: int = 5,
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
                mp3_bitrate_kbps=mp3_bitrate_kbps,
                flac_compression=flac_compression,
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
        *,
        mp3_bitrate_kbps: int = 320,
        flac_compression: int = 5,
    ) -> dict:
        """Render with silence detection — retries once if output is near-silent.

        The silence bug occurs when REAPER window is not in focus: the render
        command succeeds (produces a file) but the output is silent
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
            mp3_bitrate_kbps=mp3_bitrate_kbps,
            flac_compression=flac_compression,
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
            mp3_bitrate_kbps=mp3_bitrate_kbps,
            flac_compression=flac_compression,
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
        """Check if a rendered file is near-silent.

        Uses lightweight peak detection (numpy/soundfile) without importing
        the full SignalAnalyzer to avoid circular dependencies.  Works with
        WAV, FLAC, and MP3 (via soundfile's libsndfile backend).
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


# ════════════════════════════════════════════════════════════════
# Standalone render verification
# ════════════════════════════════════════════════════════════════


def verify_render(
    output_path: str,
    expected_duration_sec: float,
    target_lufs: float = -12.0,
    ceiling_db: float = -1.0,
    tolerance: float = 0.5,
) -> dict:
    """Post-render verification of file integrity, loudness, and peaks.

    Runs a battery of checks on the rendered output:

    1. **File existence** — output file exists and size > 0.
    2. **Duration match** — measured duration within 1 % of expected.
    3. **True peak** — true peak below *ceiling_db*.
    4. **Integrated LUFS** — within *target_lufs* +/- *tolerance*.
    5. **Silence check** — peak above -80 dBFS.

    Parameters
    ----------
    output_path : str
        Path to the rendered audio file.
    expected_duration_sec : float
        Expected duration in seconds.
    target_lufs : float
        Target integrated LUFS value.
    ceiling_db : float
        Maximum allowed true-peak in dBTP.
    tolerance : float
        Allowed LUFS deviation (LU).

    Returns
    -------
    dict
        ``{passed, checks, warnings, measurements}`` where *checks* is
        a list of ``{name, passed, detail}`` dicts and *measurements*
        contains the measured values.
    """
    checks: list[dict] = []
    warnings: list[str] = []

    # ── 1. File existence & size ──
    if not os.path.exists(output_path):
        return {
            "passed": False,
            "checks": [{"name": "file_exists", "passed": False,
                        "detail": f"File not found: {output_path}"}],
            "warnings": [],
            "measurements": {},
        }
    try:
        file_size = os.path.getsize(output_path)
    except OSError:
        file_size = 0
    if file_size == 0:
        return {
            "passed": False,
            "checks": [{"name": "file_not_empty", "passed": False,
                        "detail": "File exists but is empty (0 bytes)"}],
            "warnings": [],
            "measurements": {"file_size_bytes": 0},
        }
    checks.append({"name": "file_not_empty", "passed": True,
                   "detail": f"{file_size} bytes"})

    # ── 2. Duration match ──
    duration_sec = 0.0
    try:
        import soundfile as sf
        info = sf.info(output_path)
        duration_sec = info.duration
        if expected_duration_sec > 0:
            dur_error = abs(duration_sec - expected_duration_sec) / expected_duration_sec
            dur_ok = dur_error < 0.01  # 1 % tolerance
            checks.append({
                "name": "duration_match",
                "passed": dur_ok,
                "detail": (
                    f"Expected {expected_duration_sec:.2f}s, "
                    f"got {duration_sec:.2f}s "
                    f"({dur_error * 100:.1f}% error)"
                ),
            })
            if not dur_ok:
                warnings.append(
                    f"Duration mismatch: {dur_error * 100:.1f}% off"
                )
    except Exception as exc:
        checks.append({"name": "duration_match", "passed": False,
                       "detail": f"Could not read duration: {exc}"})
        warnings.append(f"Duration check failed: {exc}")

    # ── 3. Peak & true-peak check ──
    peak_db = None
    true_peak_dbtp = None
    integrated_lufs = None
    try:
        import numpy as np
        import soundfile as sf
        data, sr = sf.read(output_path, dtype="float64")
        if data.size > 0:
            peak_linear = float(np.max(np.abs(data)))
            peak_db = float(20.0 * math.log10(max(peak_linear, 1e-12)))

            # True peak via simple 4x oversampling (lightweight approximation)
            try:
                from scipy import signal as scipy_signal
                upsampled = scipy_signal.resample_poly(data, 4, 1, axis=0)
                tp_linear = float(np.max(np.abs(upsampled)))
                true_peak_dbtp = float(20.0 * math.log10(max(tp_linear, 1e-12)))
            except ImportError:
                # scipy not available — use sample peak as approximation
                true_peak_dbtp = peak_db

            tp_ok = true_peak_dbtp <= ceiling_db
            checks.append({
                "name": "true_peak",
                "passed": tp_ok,
                "detail": (
                    f"True peak {true_peak_dbtp:.1f} dBTP "
                    f"(ceiling {ceiling_db:.1f} dBTP)"
                ),
            })
            if not tp_ok:
                warnings.append(
                    f"True peak {true_peak_dbtp:.1f} dBTP exceeds "
                    f"ceiling {ceiling_db:.1f} dBTP"
                )

            # ── 4. LUFS check ──
            try:
                import pyloudnorm as pyln
                meter = pyln.Meter(sr)
                # pyloudnorm expects (samples, channels) float64
                if data.ndim == 1:
                    data_2d = data.reshape(-1, 1)
                else:
                    data_2d = data
                integrated_lufs = float(meter.integrated_loudness(data_2d))
                lufs_ok = abs(integrated_lufs - target_lufs) <= tolerance
                checks.append({
                    "name": "lufs_target",
                    "passed": lufs_ok,
                    "detail": (
                        f"Integrated {integrated_lufs:.1f} LUFS "
                        f"(target {target_lufs:.1f} +/- {tolerance:.1f})"
                    ),
                })
                if not lufs_ok:
                    warnings.append(
                        f"LUFS off target: {integrated_lufs:.1f} vs "
                        f"{target_lufs:.1f} (delta={integrated_lufs - target_lufs:+.1f})"
                    )
            except ImportError:
                checks.append({"name": "lufs_target", "passed": True,
                               "detail": "pyloudnorm not available — skipped"})
            except Exception as exc:
                checks.append({"name": "lufs_target", "passed": False,
                               "detail": f"LUFS measurement failed: {exc}"})
                warnings.append(f"LUFS check error: {exc}")

            # ── 5. Silence check ──
            silence_ok = peak_db > -80.0
            checks.append({
                "name": "not_silent",
                "passed": silence_ok,
                "detail": f"Peak {peak_db:.1f} dBFS (threshold -80 dBFS)",
            })
            if not silence_ok:
                warnings.append("Output appears silent (peak < -80 dBFS)")
    except Exception as exc:
        checks.append({"name": "audio_analysis", "passed": False,
                       "detail": f"Could not analyze audio: {exc}"})
        warnings.append(f"Audio analysis failed: {exc}")

    # ── Aggregate result ──
    all_passed = all(c.get("passed", False) for c in checks)
    critical_checks = [c for c in checks
                       if c["name"] in ("file_exists", "file_not_empty", "not_silent")]
    critical_passed = all(c.get("passed", False) for c in critical_checks) if critical_checks else True

    measurements = {
        "file_size_bytes": file_size,
        "duration_sec": round(duration_sec, 3) if duration_sec else None,
        "peak_db": round(peak_db, 2) if peak_db is not None else None,
        "true_peak_dbtp": round(true_peak_dbtp, 2) if true_peak_dbtp is not None else None,
        "integrated_lufs": round(integrated_lufs, 1) if integrated_lufs is not None else None,
    }

    return {
        "passed": all_passed and critical_passed,
        "checks": checks,
        "warnings": warnings,
        "measurements": measurements,
    }
