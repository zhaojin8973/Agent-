"""Tests for hermes_core.bridge — ReaperBridge and DialogKiller.

Unit tests use mocked external dependencies (threading, subprocess, reapy).
They do NOT require a running REAPER instance.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_bridge.py -v
    PYTHONPATH=src python3 -m pytest tests/test_bridge.py -v -m unit
"""

import threading
import time
from unittest.mock import MagicMock, patch, PropertyMock

import pytest

from hermes_core.bridge import DialogKiller, ReaperBridge


# ══════════════════════════════════════════════════════════════
# Unit: DialogKiller
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestDialogKiller:
    """Tests for DialogKiller lifecycle and status reporting."""

    def test_dialog_killer_starts_and_stops(self):
        """DialogKiller start() creates thread, stop() terminates it.

        The killer must properly clean up its daemon thread so tests
        don't leak background workers.
        """
        # Arrange
        killer = DialogKiller(interval=0.1)

        assert not killer.is_running, "should not be running before start()"

        # Act -- start the killer (mock subprocess to prevent real osascript)
        with patch("subprocess.run", return_value=MagicMock()):
            killer.start()

        # Assert -- running after start
        assert killer.is_running, "should be running after start()"

        # Act -- stop the killer
        killer.stop()

        # Assert -- stopped after stop()
        assert not killer.is_running, "should not be running after stop()"

    def test_dialog_killer_reports_status(self):
        """is_running and killed_count properties reflect current state."""
        # Arrange
        killer = DialogKiller(interval=0.1)

        # Initial state
        assert killer.is_running is False, "initially not running"
        assert killer.killed_count == 0, "initially zero killed"
        assert isinstance(killer.killed_count, int), "killed_count must be int"

        # Act -- start
        with patch("subprocess.run", return_value=MagicMock()):
            killer.start()

        # Assert -- running state changed
        assert killer.is_running is True, "running after start"
        assert killer.killed_count == 0, (
            "killed_count stays 0 until subprocess runs are counted"
        )

        # Cleanup
        killer.stop()

    def test_double_start_is_safe(self):
        """Calling start() when already running is idempotent."""
        # Arrange
        killer = DialogKiller(interval=0.1)

        with patch("subprocess.run", return_value=MagicMock()):
            killer.start()
            thread1 = killer._thread

            # Act -- second start
            killer.start()

        # Assert -- same thread, no new thread created
        assert killer._thread is thread1, (
            "second start() should not create a new thread"
        )
        assert killer.is_running is True

        killer.stop()

    def test_stop_when_not_running_is_safe(self):
        """Calling stop() on an idle killer does not crash."""
        # Arrange
        killer = DialogKiller(interval=0.1)

        # Act & Assert -- should not raise
        killer.stop()
        assert not killer.is_running


# ══════════════════════════════════════════════════════════════
# Unit: ReaperBridge + DialogKiller integration
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBridgeDialogKillerIntegration:
    """Tests for DialogKiller integration within ReaperBridge."""

    def test_dialog_killer_disabled_by_default_in_tests(self):
        """When dialog_killer=False, connect() does NOT start the killer.

        This is the safe default for test environments and headless CI.
        """
        # Arrange
        bridge = ReaperBridge(dialog_killer=False)

        # Verify initial state
        assert bridge._dialog_killer is not None, (
            "DialogKiller object should exist even when disabled"
        )
        assert not bridge._dialog_killer.is_running, (
            "DialogKiller should NOT be running when disabled"
        )

        # Act -- mock connection so connect() succeeds without REAPER
        mock_reapy = MagicMock()
        mock_reapy.reascript_api = MagicMock()
        mock_reapy.get_reaper_version.return_value = "7.73"

        with patch("hermes_core.bridge._get_reapy", return_value=mock_reapy):
            connected = bridge.connect()

        # Assert -- connection succeeded but killer was NOT started
        assert connected is True, "connect() should succeed with mocked reapy"
        assert not bridge._dialog_killer.is_running, (
            "DialogKiller should NOT be running after connect() when disabled"
        )

    def test_dialog_killer_enabled_starts_on_connect(self):
        """When dialog_killer=True, connect() starts the DialogKiller."""
        # Arrange
        bridge = ReaperBridge(dialog_killer=True)

        # Act -- mock connection
        mock_reapy = MagicMock()
        mock_reapy.reascript_api = MagicMock()
        mock_reapy.get_reaper_version.return_value = "7.73"

        with patch("hermes_core.bridge._get_reapy", return_value=mock_reapy):
            with patch("subprocess.run", return_value=MagicMock()):
                connected = bridge.connect()

        # Assert
        assert connected is True
        assert bridge._dialog_killer.is_running, (
            "DialogKiller should be running after connect() when enabled"
        )

        # Cleanup
        bridge._dialog_killer.stop()

    def test_health_check_includes_dialog_killer_info(self):
        """health_check() reports dialog_killer_active and dialogs_killed."""
        # Arrange -- bridge with a pre-set mock API (simulating live connection)
        bridge = ReaperBridge(dialog_killer=True)
        bridge._api = MagicMock()
        bridge._api.GetAppVersion.return_value = "7.73"
        bridge._api.GetOS.return_value = "macOS"
        bridge._api.Audio_IsRunning.return_value = True
        bridge._api.Audio_IsPreBuffer.return_value = 0

        # Pre-set killer with known state
        bridge._dialog_killer = MagicMock()
        bridge._dialog_killer.is_running = True
        bridge._dialog_killer.killed_count = 0

        # Act
        health = bridge.health_check()

        # Assert
        assert health.get("dialog_killer_active") is True, (
            f"health_check should report dialog_killer_active=True, got: {health}"
        )
        assert "dialogs_killed" in health, (
            f"health_check should include dialogs_killed, got: {health}"
        )
        assert health["dialogs_killed"] == 0

    def test_dialog_killer_stops_on_disconnect(self):
        """DialogKiller stops when the bridge loses its connection context."""
        # This tests that the killer can be stopped without leaking threads.
        # Arrange
        bridge = ReaperBridge(dialog_killer=True)

        mock_reapy = MagicMock()
        mock_reapy.reascript_api = MagicMock()
        mock_reapy.get_reaper_version.return_value = "7.73"

        with patch("hermes_core.bridge._get_reapy", return_value=mock_reapy):
            with patch("subprocess.run", return_value=MagicMock()):
                bridge.connect()

        assert bridge._dialog_killer.is_running

        # Act -- stop the killer
        bridge._dialog_killer.stop()

        # Assert
        assert not bridge._dialog_killer.is_running
