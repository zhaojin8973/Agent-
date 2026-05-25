"""
Layer 1: REAPER Bridge — reapy connection and raw API access.
Zero UI assumption. All operations work without human interaction.
"""

import subprocess
import threading
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

# reapy is imported lazily to avoid importing it before REAPER is running.
_reapy = None

def _get_reapy():
    global _reapy
    if _reapy is None:
        import reapy as _reapy_mod
        _reapy = _reapy_mod
    return _reapy

log = logging.getLogger(__name__)


def _extract_reaper_string(result) -> str:
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

# ── AppleScript fragments ─────────────────────────────────────

_AS_INSPECT = """
tell application "System Events"
    tell process "REAPER"
        set output to ""
        repeat with w in windows
            set winName to name of w
            if (winName does not contain "REAPER v") and (winName is not "") then
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
        self._killed_count = 0
        self._events: list[DialogEvent] = []
        self._max_events = max_events
        self._enabled = True
        self._safe_patterns: list[str] = list(_KNOWN_SAFE_PATTERNS)
        self._diagnosis_patterns: list[str] = list(_NEEDS_DIAGNOSIS_PATTERNS)

    # ── Lifecycle ──────────────────────────────────────────

    def start(self):
        """Start the killer daemon thread.

        Safe to call multiple times -- subsequent calls are no-ops
        when the thread is already running.
        """
        if self._thread is not None:
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
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
        return self._killed_count

    def get_recent_events(self) -> list[DialogEvent]:
        """Return a copy of recent dialog events, most recent last."""
        return list(self._events)

    def set_rules(self, safe_patterns=None, diagnosis_patterns=None):
        """Override the built-in dialog classification patterns.

        Pass a list of substrings to match.  Dialogs whose title contains
        any safe pattern are auto-dismissed; those matching diagnosis
        patterns are dismissed with full context recorded; everything
        else is treated as unknown.
        """
        if safe_patterns is not None:
            self._safe_patterns = list(safe_patterns)
        if diagnosis_patterns is not None:
            self._diagnosis_patterns = list(diagnosis_patterns)

    # ── Internal ───────────────────────────────────────────

    def _run(self):
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
        except Exception:
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
        """Return 'safe' | 'diagnosis' | 'unknown' based on title text."""
        title_lower = title.lower()
        for pat in self._safe_patterns:
            if pat.lower() in title_lower:
                return "safe"
        for pat in self._diagnosis_patterns:
            if pat.lower() in title_lower:
                return "diagnosis"
        return "unknown"

    def _pick_button(self, buttons: list[str], classification: str) -> str:
        """Pick which button to click.  Returns button name or '' for Escape fallback."""
        if classification == "safe":
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

    def _click_button(self, title_fragment: str, button_match: str) -> str:
        """Click a specific button in a window matching title_fragment.

        Returns the clicked button name or empty string.
        """
        safe_title = title_fragment.replace('"', '\\"')
        safe_button = button_match.replace('"', '\\"')
        script = _AS_CLICK_BUTTON.format(
            title_fragment=safe_title, button_match=safe_button
        )
        result = self._run_osascript(script)
        if result.startswith("clicked:"):
            return result.split(":", 1)[1]
        return ""

    def _dismiss_dialogs(self):
        """Inspect windows, classify dialogs, and take targeted action."""
        windows = self._inspect_windows()
        if not windows:
            return

        for title, buttons in windows:
            classification = self._classify(title)
            now = time.time()

            if classification == "safe":
                btn = self._pick_button(buttons, "safe")
                if btn:
                    clicked = self._click_button(title, btn)
                    action = f"clicked_{btn.lower()}" if clicked else "sent_escape"
                    if not clicked:
                        self._run_osascript(_AS_DISMISS)
                else:
                    self._run_osascript(_AS_DISMISS)
                    action = "sent_escape"

                self._killed_count += 1
                event = DialogEvent(
                    has_modal=True,
                    window_title=title,
                    buttons=buttons,
                    text_hits=[title],
                    action_taken=action,
                    timestamp=now,
                )

            elif classification == "diagnosis":
                btn = self._pick_button(buttons, "diagnosis")
                if btn:
                    clicked = self._click_button(title, btn)
                    action = f"clicked_{btn.lower()}" if clicked else "skipped_unknown"
                else:
                    action = "skipped_unknown"

                if "clicked" in action:
                    self._killed_count += 1

                event = DialogEvent(
                    has_modal=True,
                    window_title=title,
                    buttons=buttons,
                    text_hits=[title],
                    action_taken=action,
                    timestamp=now,
                )

            else:
                event = DialogEvent(
                    has_modal=True,
                    window_title=title,
                    buttons=buttons,
                    text_hits=[title],
                    action_taken="skipped_unknown",
                    timestamp=now,
                )

            self._events.append(event)
            if len(self._events) > self._max_events:
                self._events = self._events[-self._max_events:]

            log.info(
                "DialogKiller: %s | %s | %s",
                classification,
                title[:80],
                event.action_taken,
            )


class ReaperBridge:
    """Provides a clean API over reapy for REAPER automation."""

    def __init__(self, dialog_killer: bool = True):
        self._api = None
        self._reapy_module = None
        self._ui_locked = False
        self._dialog_killer_enabled = dialog_killer
        self._dialog_killer = DialogKiller()

    # ── Connection ──────────────────────────────────────────

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
            # Start dialog killer daemon when enabled
            if self._dialog_killer_enabled:
                self._dialog_killer.start()
                log.info("DialogKiller started")
            return True
        except Exception as e:
            log.error("Failed to connect to REAPER: %s", e)
            return False

    def ensure_connected(self) -> bool:
        """Ensure we have a live connection; connect if needed."""
        if self._api is not None:
            try:
                self._api.GetAppVersion()
                return True
            except Exception:
                self._api = None
        return self.connect()

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
            except Exception:
                pass
        elif self.connect():
            return self.health_check()
        return result

    # ── Properties ──────────────────────────────────────────

    @property
    def api(self):
        """Raw REAPER ReaScript API. Ensure connected before using."""
        if self._api is None:
            self.ensure_connected()
        return self._api

    @property
    def rpr(self):
        """reapy module handle for high-level operations."""
        if self._reapy_module is None:
            self.ensure_connected()
        return self._reapy_module

    # ── UI Suppression ──────────────────────────────────────

    def lock_ui(self):
        """PreventUIRefresh(1) — call before batch operations."""
        if not self._ui_locked and self._api is not None:
            try:
                self._api.PreventUIRefresh(1)
                self._ui_locked = True
            except Exception:
                pass

    def unlock_ui(self):
        """PreventUIRefresh(-1) — call after batch operations."""
        if self._ui_locked and self._api is not None:
            try:
                self._api.PreventUIRefresh(-1)
            except Exception:
                pass
            finally:
                self._ui_locked = False

    def __enter__(self):
        self.lock_ui()
        return self

    def __exit__(self, *args):
        self.unlock_ui()
