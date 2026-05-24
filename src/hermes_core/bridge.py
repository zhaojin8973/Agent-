"""
Layer 1: REAPER Bridge — reapy connection and raw API access.
Zero UI assumption. All operations work without human interaction.
"""

import subprocess
import threading
import time
import logging
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


_AS_DISMISS = """
tell application "System Events"
    tell process "REAPER"
        repeat with w in windows
            if (name of w does not contain "REAPER v") then
                keystroke (ASCII character 27)
            end if
        end repeat
    end tell
end tell
"""


class DialogKiller:
    """Background thread that auto-dismisses REAPER modal dialogs on macOS.

    Polls for non-main REAPER windows and presses Escape to dismiss
    error dialogs, warnings, and other popups that would otherwise
    block remote API calls until a human clicks OK.

    The killer only targets windows whose title does NOT match the
    main REAPER application window, so it never accidentally closes
    the primary DAW window.
    """

    def __init__(self, interval: float = 0.5):
        self._interval = interval
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self._killed_count = 0

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
        """Number of times the dismissal script has been run."""
        return self._killed_count

    # ── Internal ───────────────────────────────────────────

    def _run(self):
        """Main loop: wait for interval, then dismiss dialogs."""
        while not self._stop_event.wait(self._interval):
            self._dismiss_dialogs()

    def _dismiss_dialogs(self):
        """Run the AppleScript dismissal command."""
        try:
            subprocess.run(
                ["osascript", "-e", _AS_DISMISS],
                capture_output=True,
                timeout=2,
            )
            self._killed_count += 1
        except Exception:
            pass


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
