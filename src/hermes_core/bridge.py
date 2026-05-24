"""
Layer 1: REAPER Bridge — reapy connection and raw API access.
Zero UI assumption. All operations work without human interaction.
"""

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


class ReaperBridge:
    """Provides a clean API over reapy for REAPER automation."""

    def __init__(self):
        self._api = None
        self._reapy_module = None
        self._ui_locked = False

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
