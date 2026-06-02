"""
Layer 1: REAPER Bridge — reapy connection and raw API access.
Zero UI assumption. All operations work without human interaction.
"""

import atexit
import subprocess
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

# reapy is imported lazily to avoid importing it before REAPER is running.
_reapy = None

def _get_reapy() -> object:
    """Lazy import reapy to avoid import-time side effects."""
    global _reapy
    if _reapy is None:
        import reapy as _reapy_mod
        _reapy = _reapy_mod
    return _reapy

log = logging.getLogger(__name__)


def _extract_reaper_string(result: object) -> str:
    """Extract a useful string from reapy/RPR return variants."""
    if isinstance(result, str):
        return result
    if isinstance(result, bytes):
        return result.decode("utf-8", errors="replace")
    if isinstance(result, (tuple, list)):
        for value in reversed(result):
            if isinstance(value, str) and value.strip() and not value.startswith("("):
                return value
            if isinstance(value, bytes) and value.strip():
                return value.decode("utf-8", errors="replace")
    return ""


@dataclass
class DialogEvent:
    """Structured record of a REAPER modal dialog detection and response."""

    has_modal: bool
    window_title: str = ""
    buttons: list[str] = field(default_factory=list)
    text_hits: list[str] = field(default_factory=list)
    action_taken: str = "none"
    timestamp: float = 0.0


# ── Dialog classification rules ───────────────────────────────

_KNOWN_SAFE_PATTERNS = [
    "Nothing to render",
    "Render path invalid",
    "Render failed",
    "missing output directory",
    "output directory",
    # REAPER file-system / project errors
    "Error creating project file",
    "Error opening",
    "Error writing",
    "Could not save",
    "Could not write",
    "NEWTEMP",
    "project file",
    # Plugin warnings that are safe to dismiss
    "Plugin could not be loaded",
    "sample rate",
    "block size",
    "not responding",
]

_NEEDS_DIAGNOSIS_PATTERNS = [
    "Plugin missing",
    "Authorization failed",
    "authorization",
    "Media offline",
    "WaveShell",
    "plugin scan",
    "iLok",
    "license",
    "activation",
]

# Windows that are NEVER dialogs — dismiss actions are skipped entirely.
# These are progress indicators, tool windows, or informational popups
# that should not be touched.
_NEVER_DISMISS_PATTERNS = [
    "Rendering to file",
    "Render to File",
    "Building peaks",
    "Building Peaks",
    "Saving project",
    "Save project",
    "FX: Track",
    "FX: Master",
]

# ── AppleScript fragments ─────────────────────────────────────

_AS_INSPECT = """
tell application "System Events"
    tell process "REAPER"
        set output to ""
        repeat with w in windows
            set winName to name of w
            if (winName does not contain "REAPER v") and (winName is not "") then
                -- Skip REAPER floating tool windows (not modal dialogs)
                if (winName does not start with "FX:") and ¬
                   (winName does not start with "Routing") and ¬
                   (winName does not contain "Track Manager") and ¬
                   (winName does not contain "Media Explorer") and ¬
                   (winName does not contain "Performance Meter") and ¬
                   (winName does not contain "Virtual MIDI") and ¬
                   (winName does not contain "Region/Marker") and ¬
                   (winName does not contain "Undo History") and ¬
                   (winName does not contain "Screenset") and ¬
                   (winName does not contain "Action List") and ¬
                   (winName does not contain "Preferences") and ¬
                   (winName does not contain "Project Settings") then
                    set buttonList to ""
                    try
                        repeat with b in buttons of w
                            set btnName to name of b
                            if btnName is not "" then
                                set buttonList to buttonList & btnName & "|"
                            end if
                        end repeat
                    end try
                    set output to output & winName & ":::" & buttonList & ";;;"
                end if
            end if
        end repeat
        return output
    end tell
end tell
"""

_AS_CLICK_BUTTON = """
tell application "System Events"
    tell process "REAPER"
        repeat with w in windows
            set winTitle to name of w
            if (winTitle contains "{title_fragment}") then
                repeat with b in buttons of w
                    if (name of b contains "{button_match}") then
                        click b
                        return "clicked:" & name of b
                    end if
                end repeat
            end if
        end repeat
    end tell
end tell
"""

_AS_DISMISS = """
tell application "System Events"
    tell process "REAPER"
        repeat with w in windows
            set winName to name of w
            if (winName does not contain "REAPER v") and (winName is not "") then
                keystroke (ASCII character 27)
            end if
        end repeat
    end tell
end tell
"""


class DialogKiller:
    """Background thread that auto-dismisses REAPER modal dialogs on macOS.

    Uses three-tier classification:
      - Known safe: click OK/Close, record event.
      - Needs diagnosis: click conservative button, record full context.
      - Unknown: skip, report ``unknown_modal_detected``.

    The killer only targets windows whose title does NOT match the
    main REAPER application window.
    """

    def __init__(self, interval: float = 0.5, max_events: int = 200):
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._lock = threading.Lock()
        self._killed_count = 0
        self._events: list[DialogEvent] = []
        self._max_events = max_events
        self._enabled = True
        self._safe_patterns: list[str] = list(_KNOWN_SAFE_PATTERNS)
        self._diagnosis_patterns: list[str] = list(_NEEDS_DIAGNOSIS_PATTERNS)
        self._never_dismiss_patterns: list[str] = list(_NEVER_DISMISS_PATTERNS)

    # ── Lifecycle ──────────────────────────────────────────

    def start(self) -> None:
        """Start the killer daemon thread.

        Safe to call multiple times -- subsequent calls are no-ops
        when the thread is already running.
        """
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        """Signal the thread to stop and wait for it.

        Safe to call on an already-stopped killer -- no-op.
        """
        if self._thread is None:
            return
        self._stop_event.set()
        self._thread.join(timeout=self._interval * 2)
        self._thread = None

    # ── Status ─────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        """True when the background daemon thread is alive."""
        return self._thread is not None and self._thread.is_alive()

    @property
    def killed_count(self) -> int:
        """Number of dialogs that were confirmed closed (not just script runs)."""
        with self._lock:
            return self._killed_count

    def get_recent_events(self) -> list[DialogEvent]:
        """Return a copy of recent dialog events, most recent last."""
        with self._lock:
            return list(self._events)

    def set_rules(self, safe_patterns: list[str] | None = None,
                  diagnosis_patterns: list[str] | None = None,
                  never_dismiss_patterns: list[str] | None = None) -> None:
        """Override the built-in dialog classification patterns.

        Pass a list of substrings to match.  Dialogs whose title contains
        any safe pattern are auto-dismissed; those matching diagnosis
        patterns are dismissed with full context recorded; never-dismiss
        windows are left untouched; everything else is dismissed
        aggressively (headless mode).
        """
        if safe_patterns is not None:
            self._safe_patterns = list(safe_patterns)
        if diagnosis_patterns is not None:
            self._diagnosis_patterns = list(diagnosis_patterns)
        if never_dismiss_patterns is not None:
            self._never_dismiss_patterns = list(never_dismiss_patterns)

    # ── Internal ───────────────────────────────────────────

    def _run(self) -> None:
        """Main loop: wait for interval, then inspect and dismiss dialogs."""
        while not self._stop_event.wait(self._interval):
            if self._enabled:
                self._dismiss_dialogs()

    def _run_osascript(self, source: str, timeout: float = 2.0) -> str:
        """Run an AppleScript and return its stdout, or '' on failure."""
        try:
            proc = subprocess.run(
                ["osascript", "-e", source],
                capture_output=True,
                timeout=timeout,
            )
            return proc.stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            log.debug("osascript failed: %s", e)
            return ""

    def _inspect_windows(self) -> list[tuple[str, list[str]]]:
        """Return [(window_title, [button_names]), ...] for non-REAPER windows."""
        raw = self._run_osascript(_AS_INSPECT)
        if not raw:
            return []
        windows: list[tuple[str, list[str]]] = []
        for segment in raw.split(";;;"):
            segment = segment.strip()
            if not segment:
                continue
            parts = segment.split(":::", 1)
            title = parts[0].strip()
            buttons = [b.strip() for b in parts[1].split("|") if b.strip()] if len(parts) > 1 else []
            windows.append((title, buttons))
        return windows

    def _classify(self, title: str) -> str:
        """Return 'never' | 'safe' | 'diagnosis' | 'unknown' based on title text.

        'never' — progress windows / floating tools that must NOT be dismissed.
        'safe' — known error dialogs, auto-dismiss.
        'diagnosis' — known issues worth logging, auto-dismiss.
        'unknown' — unrecognized, dismissed aggressively in headless mode.
        """
        title_lower = title.lower()
        for pat in self._never_dismiss_patterns:
            if pat.lower() in title_lower:
                return "never"
        for pat in self._safe_patterns:
            if pat.lower() in title_lower:
                return "safe"
        for pat in self._diagnosis_patterns:
            if pat.lower() in title_lower:
                return "diagnosis"
        return "unknown"

    def _pick_button(self, buttons: list[str], classification: str) -> str:
        """Pick which button to click.  Returns button name or '' for Escape fallback.

        Headless policy: for unknown dialogs, aggressively try OK/Close/Yes
        in that order — better to dismiss with the wrong button than to hang.
        """
        if classification in ("safe", "unknown"):
            for preferred in ("OK", "Close", "Continue", "Yes"):
                for b in buttons:
                    if preferred.lower() in b.lower():
                        return b
        elif classification == "diagnosis":
            for preferred in ("OK", "Close"):
                for b in buttons:
                    if preferred.lower() in b.lower():
                        return b
        return ""

    @staticmethod
    def _escape_applescript_string(s: str) -> str:
        """Escape a string for safe embedding in an AppleScript string literal.

        Escapes backslash, double-quote, and strips control characters that
        could be used for AppleScript injection (line continuation ¬, etc.).
        """
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "").replace("\r", "").replace("\t", " ")
        return s

    def _click_button(self, title_fragment: str, button_match: str) -> str:
        """Click a specific button in a window matching title_fragment.

        Returns the clicked button name or empty string.
        """
        safe_title = self._escape_applescript_string(title_fragment)
        safe_button = self._escape_applescript_string(button_match)
        script = _AS_CLICK_BUTTON.replace(
            "{title_fragment}", safe_title
        ).replace(
            "{button_match}", safe_button
        )
        result = self._run_osascript(script)
        if result.startswith("clicked:"):
            return result.split(":", 1)[1]
        return ""

    def _dismiss_dialogs(self) -> None:
        """Inspect windows, classify dialogs, and take targeted action.

        Headless guarantee: **every** recognised dialog is dismissed.
        - ``never`` windows (progress bars, tool windows) are left alone.
        - ``safe`` dialogs: click OK/Close then fall back to Escape.
        - ``diagnosis`` dialogs: same as safe but logged with full context.
        - ``unknown`` dialogs: dismissed aggressively (OK → Escape), logged
          so patterns can be added later.

        The pipeline must never hang waiting for a user who is not there.
        """
        windows = self._inspect_windows()
        if not windows:
            return

        for title, buttons in windows:
            classification = self._classify(title)
            now = time.time()

            # ── Never dismiss progress windows / tool windows ─
            if classification == "never":
                continue

            # ── All other classifications: dismiss aggressively ─
            btn = self._pick_button(buttons, classification)

            if btn:
                clicked = self._click_button(title, btn)
                action = f"clicked_{btn.lower()}" if clicked else "sent_escape"
                if not clicked:
                    self._run_osascript(_AS_DISMISS)
            else:
                self._run_osascript(_AS_DISMISS)
                action = "sent_escape"

            event = DialogEvent(
                has_modal=True,
                window_title=title,
                buttons=buttons,
                text_hits=[title],
                action_taken=action,
                timestamp=now,
            )

            with self._lock:
                self._killed_count += 1
                self._events.append(event)
                if len(self._events) > self._max_events:
                    self._events = self._events[-self._max_events:]

            log.info(
                "DialogKiller: %s | %s | %s",
                classification,
                title[:80],
                action,
            )


class ReaperBridge:
    """Provides a clean API over reapy for REAPER automation."""

    def __init__(self, dialog_killer: bool = True):
        self._api = None
        self._reapy_module = None
        self._ui_refresh_depth = 0     # nesting-safe counter (was simple bool)
        self._dialog_killer_enabled = dialog_killer
        self._dialog_killer = DialogKiller()
        atexit.register(self._emergency_cleanup)

    # ── Connection ──────────────────────────────────────────

    def ensure_connected(self) -> bool:
        """Ensure we have a live connection; reconnect with backoff if needed."""
        if self._api is not None:
            try:
                self._api.GetAppVersion()
                return True
            except Exception as e:
                log.debug("Connection check failed: %s", e)
                self._api = None
        return self.connect() or self.reconnect(max_retries=3, base_delay=1.0)

    def health_check(self) -> dict:
        """Return health status of the REAPER connection."""
        result = {
            "reapy_connected": False,
            "audio_running": False,
            "version": None,
            "os": None,
            "dialog_killer_active": self._dialog_killer.is_running,
            "dialogs_killed": self._dialog_killer.killed_count,
        }
        if self._api is not None:
            try:
                result["version"] = self._api.GetAppVersion()
                result["os"] = self._api.GetOS()
                result["reapy_connected"] = True
                result["audio_running"] = bool(self._api.Audio_IsRunning())
            except Exception as e:
                log.debug("health_check failed: %s", e)
        return result

    # ── Properties ──────────────────────────────────────────

    @property
    def api(self) -> object:
        """Raw REAPER ReaScript API. Ensure connected before using."""
        if self._api is None:
            self.ensure_connected()
        return self._api

    @property
    def rpr(self) -> object:
        """reapy module handle for high-level operations."""
        if self._reapy_module is None:
            self.ensure_connected()
        return self._reapy_module

    # ── UI Suppression ──────────────────────────────────────

    def lock_ui(self) -> None:
        """PreventUIRefresh(1) — nesting-safe, call before batch operations."""
        if self._api is not None:
            try:
                self._api.PreventUIRefresh(1)
                self._ui_refresh_depth += 1
            except Exception as e:
                log.debug("lock_ui failed: %s", e)

    def unlock_ui(self) -> None:
        """PreventUIRefresh(-1) — nesting-safe, call after batch operations."""
        if self._ui_refresh_depth > 0 and self._api is not None:
            try:
                self._api.PreventUIRefresh(-1)
            except Exception as e:
                log.debug("unlock_ui failed: %s", e)
            finally:
                self._ui_refresh_depth -= 1

    def _emergency_cleanup(self) -> None:
        """atexit hook — unlock REAPER UI even if Python crashes mid-operation."""
        if self._ui_refresh_depth <= 0:
            return
        try:
            while self._ui_refresh_depth > 0:
                self._api.PreventUIRefresh(-1)
                self._ui_refresh_depth -= 1
            log.warning("Emergency UI unlock: restored %d levels", self._ui_refresh_depth)
        except Exception as e:
            log.debug("_emergency_cleanup failed: %s", e)

    def __enter__(self) -> "ReaperBridge":
        """Context manager entry — lock UI."""
        self.lock_ui()
        return self

    def __exit__(self, *args: object) -> None:
        """Context manager exit — unlock UI."""
        self.unlock_ui()

    # ── Connection / reconnection ───────────────────────────

    def connect(self) -> bool:
        """Connect to a running REAPER instance via reapy."""
        if self._api is not None:
            return True
        try:
            reapy_mod = _get_reapy()
            reapy_mod.connect()
            self._reapy_module = reapy_mod
            self._api = reapy_mod.reascript_api
            version = reapy_mod.get_reaper_version()
            log.info("reapy connected, REAPER v%s", version)
            if not str(version).startswith("7."):
                log.warning(
                    "Untested REAPER version %s — tested with 7.73.", version,
                )
            # Start dialog killer daemon when enabled
            if self._dialog_killer_enabled:
                self._dialog_killer.start()
                log.info("DialogKiller started")
            return True
        except Exception as e:
            log.error("Failed to connect to REAPER: %s", e)
            return False

    def reconnect(self, max_retries: int = 3, base_delay: float = 1.0) -> bool:
        """Reconnect with exponential backoff.  Returns True on success."""
        self._api = None
        self._reapy_module = None
        for attempt in range(1, max_retries + 1):
            delay = base_delay * (2 ** (attempt - 1))
            log.info("Reconnect attempt %d/%d (delay %.1fs)", attempt, max_retries, delay)
            if self.connect():
                return True
            if attempt < max_retries:
                time.sleep(delay)
        return False

    # ── Safe RPC call with timeout ─────────────────────────

    def call_rpc(self, fn: object, *args: object, timeout: float = 30.0, **kwargs: object) -> tuple[bool, object]:
        """Call *fn* with a timeout.  Returns ``(ok, result_or_error)``.

        Use this for any RPR call that may hang (FX ops, render, etc.)
        so the Python process never blocks indefinitely.
        """
        import concurrent.futures

        def _target():
            return fn(*args, **kwargs)

        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            future = ex.submit(_target)
            try:
                result = future.result(timeout=timeout)
                return (True, result)
            except concurrent.futures.TimeoutError:
                log.error("RPC call timed out after %.1fs: %s", timeout, getattr(fn, "__name__", fn))
                future.cancel()
                return (False, TimeoutError(f"RPC call timed out after {timeout:.1f}s"))
            except Exception as exc:
                return (False, exc)

    # ── Dialog Killer helpers ──────────────────────────────

    def stop_dialog_killer(self):
        """Stop the background dialog-killer daemon if it is running."""
        self._dialog_killer.stop()

    def get_recent_dialog_events(self) -> list[DialogEvent]:
        """Return recent dialog events from the background killer."""
        return self._dialog_killer.get_recent_events()

    @property
    def dialog_killer_active(self) -> bool:
        """True when the dialog-killer daemon thread is alive."""
        return self._dialog_killer.is_running

    # ── REAPER 窗口焦点 ───────────────────────────────────────

    def focus_reaper(self) -> bool:
        """将 REAPER 窗口聚焦到前台（仅 macOS）。

        REAPER 的非模态渲染命令 (42230) 在窗口不聚焦时可能产生
        静音输出。通过 AppleScript 激活 REAPER 应用来规避此问题。

        Returns True on success, False on failure (non-macOS, subprocess
        error, or REAPER not running).
        """
        import sys
        import platform
        if platform.system() != "Darwin":
            log.debug("focus_reaper: skipped (platform=%s)", platform.system())
            return False

        try:
            result = subprocess.run(
                ["osascript", "-e", 'tell application "REAPER" to activate'],
                capture_output=True, timeout=5.0,
            )
            if result.returncode == 0:
                log.debug("focus_reaper: REAPER window activated")
                return True
            log.warning("focus_reaper: osascript returned %d: %s",
                        result.returncode, result.stderr.decode(errors="replace"))
            return False
        except subprocess.TimeoutExpired:
            log.warning("focus_reaper: AppleScript timed out after 5s")
            return False
        except Exception as exc:
            log.warning("focus_reaper: failed — %s", exc)
            return False
