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

from hermes_core.bridge import DialogEvent, DialogKiller, ReaperBridge, _extract_reaper_string


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


# ══════════════════════════════════════════════════════════════
# Unit: DialogKiller — classification logic
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestDialogKillerClassification:
    """Tests for the three-tier dialog classification system."""

    def test_classify_safe_known_patterns(self):
        """Titles matching known safe patterns return 'safe'."""
        killer = DialogKiller(interval=10.0)  # long interval, won't fire on its own
        assert killer._classify("Nothing to render") == "safe"
        assert killer._classify("Render path invalid!") == "safe"
        assert killer._classify("Render failed: some reason") == "safe"
        assert killer._classify("missing output directory") == "safe"

    def test_classify_diagnosis_patterns(self):
        """Titles matching diagnosis patterns return 'diagnosis'."""
        killer = DialogKiller(interval=10.0)
        assert killer._classify("Plugin missing: Waves RVox") == "diagnosis"
        assert killer._classify("Authorization failed") == "diagnosis"
        assert killer._classify("iLok license error") == "diagnosis"
        assert killer._classify("Media offline") == "diagnosis"
        assert killer._classify("WaveShell scan") == "diagnosis"

    def test_classify_unknown_dialog(self):
        """Titles matching no known pattern return 'unknown'."""
        killer = DialogKiller(interval=10.0)
        assert killer._classify("Some unexpected dialog") == "unknown"
        assert killer._classify("REAPER v7.73") == "unknown"

    def test_classify_case_insensitive(self):
        """Classification is case-insensitive."""
        killer = DialogKiller(interval=10.0)
        assert killer._classify("nothing TO render") == "safe"
        assert killer._classify("PLUGIN MISSING") == "diagnosis"

    def test_pick_button_safe_prefers_ok(self):
        """For safe dialogs, prefers OK > Close > Continue > Yes."""
        killer = DialogKiller(interval=10.0)
        assert killer._pick_button(["Cancel", "OK", "Help"], "safe") == "OK"
        assert killer._pick_button(["Close", "Help"], "safe") == "Close"
        assert killer._pick_button(["Continue"], "safe") == "Continue"
        assert killer._pick_button(["Yes", "No"], "safe") == "Yes"
        assert killer._pick_button(["Help"], "safe") == ""

    def test_pick_button_diagnosis_conservative(self):
        """For diagnosis dialogs, only picks OK or Close."""
        killer = DialogKiller(interval=10.0)
        assert killer._pick_button(["OK", "Cancel"], "diagnosis") == "OK"
        assert killer._pick_button(["Close"], "diagnosis") == "Close"
        assert killer._pick_button(["Continue", "Help"], "diagnosis") == ""

    def test_set_rules_override_patterns(self):
        """set_rules() allows custom safe/diagnosis patterns."""
        killer = DialogKiller(interval=10.0)
        killer.set_rules(
            safe_patterns=["custom safe msg"],
            diagnosis_patterns=["custom diag msg"],
        )
        assert killer._classify("custom safe msg here") == "safe"
        assert killer._classify("custom diag msg error") == "diagnosis"
        # Old patterns no longer match
        assert killer._classify("Nothing to render") == "unknown"


# ══════════════════════════════════════════════════════════════
# Unit: DialogKiller — events & lifecycle
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestDialogKillerEvents:
    """Tests for DialogEvent recording and retrieval."""

    def test_dialog_event_defaults(self):
        """DialogEvent has sensible defaults."""
        event = DialogEvent(has_modal=False)
        assert event.has_modal is False
        assert event.window_title == ""
        assert event.buttons == []
        assert event.text_hits == []
        assert event.action_taken == "none"
        assert event.timestamp == 0.0

    def test_dialog_event_full_record(self):
        """DialogEvent stores all fields correctly."""
        event = DialogEvent(
            has_modal=True,
            window_title="Nothing to render",
            buttons=["OK"],
            text_hits=["Nothing to render"],
            action_taken="clicked_ok",
            timestamp=1716600000.0,
        )
        assert event.has_modal is True
        assert event.window_title == "Nothing to render"
        assert event.action_taken == "clicked_ok"

    def test_get_recent_events_initially_empty(self):
        """get_recent_events() returns empty list when no events recorded."""
        killer = DialogKiller(interval=10.0)
        assert killer.get_recent_events() == []

    def test_dialog_killer_inspect_windows_no_output(self):
        """When AppleScript returns empty, inspect returns empty list."""
        killer = DialogKiller(interval=10.0)
        with patch.object(
            killer, "_run_osascript", return_value=""
        ):
            windows = killer._inspect_windows()
            assert windows == []

    def test_dialog_killer_inspect_windows_parses_output(self):
        """Correctly parses AppleScript output into (title, buttons) tuples."""
        killer = DialogKiller(interval=10.0)
        fake_output = "Nothing to render:::OK|Close|;;;Plugin missing:::OK|;;;"
        with patch.object(
            killer, "_run_osascript", return_value=fake_output
        ):
            windows = killer._inspect_windows()
            assert len(windows) == 2
            assert windows[0] == ("Nothing to render", ["OK", "Close"])
            assert windows[1] == ("Plugin missing", ["OK"])

    def test_inspect_skips_main_reaper_window(self):
        """Main REAPER window (title contains 'REAPER v') is never reported.

        The AppleScript already filters these, so inspect_windows only
        sees non-main dialogs.
        """
        killer = DialogKiller(interval=10.0)
        # Simulate AS output where only non-main windows remain
        fake_output = "Error Dialog:::OK|;;;"
        with patch.object(
            killer, "_run_osascript", return_value=fake_output
        ):
            windows = killer._inspect_windows()
            assert len(windows) == 1
            assert "REAPER v" not in windows[0][0]

    def test_dismiss_safe_dialog_records_event(self):
        """Dismissing a safe dialog records an event and increments count."""
        killer = DialogKiller(interval=10.0)
        fake_output = "Nothing to render:::OK|Close|;;;"
        with patch.object(
            killer, "_run_osascript", side_effect=[
                fake_output,  # _inspect_windows
                "",           # _click_button (failed)
                "",           # _AS_DISMISS fallback
            ]
        ):
            killer._dismiss_dialogs()
            events = killer.get_recent_events()
            assert len(events) == 1
            assert events[0].window_title == "Nothing to render"
            assert events[0].has_modal is True
            assert killer.killed_count == 1

    def test_dismiss_unknown_dialog_aggressively(self):
        """Headless mode: unknown dialogs ARE dismissed aggressively (NOT skipped)."""
        killer = DialogKiller(interval=10.0)
        # side_effect: [inspect_output, click_result]
        with patch.object(
            killer, "_run_osascript",
            side_effect=[
                "Bizarre Unknown Popup:::Yes|No|;;;",  # _inspect_windows
                "clicked:Yes",                          # _click_button
            ]
        ):
            killer._dismiss_dialogs()
            events = killer.get_recent_events()
            assert len(events) == 1
            # Headless: never "skipped_unknown" — always dismissed
            assert events[0].action_taken == "clicked_yes"
            assert killer.killed_count == 1

    def test_killed_count_increments_for_all_dialogs(self):
        """Headless mode: killed_count increments for ALL dismissed dialogs
        (safe, diagnosis, AND unknown)."""
        killer = DialogKiller(interval=10.0)

        # Unknown dialog — now increments (headless aggressive dismiss)
        with patch.object(
            killer, "_run_osascript",
            return_value="Unknown Title:::OK|;;;"
        ):
            killer._dismiss_dialogs()
            assert killer.killed_count == 1

        # Safe dialog with click — SHOULD increment
        with patch.object(
            killer, "_run_osascript", side_effect=[
                "Nothing to render:::OK|;;;",  # _inspect_windows
                "clicked:OK",                  # _click_button
            ]
        ):
            killer._dismiss_dialogs()
            assert killer.killed_count == 2

    def test_event_buffer_does_not_grow_unbounded(self):
        """Events list is capped at max_events."""
        killer = DialogKiller(interval=10.0, max_events=3)
        for i in range(5):
            output = f"Error {i}:::OK|;;;"
            with patch.object(
                killer, "_run_osascript", return_value=output
            ):
                killer._dismiss_dialogs()
        assert len(killer._events) <= 3

    def test_set_rules_partial_override(self):
        """set_rules with only safe_patterns leaves diagnosis intact, and vice versa."""
        killer = DialogKiller(interval=10.0)
        killer.set_rules(safe_patterns=["custom only"])
        assert killer._classify("custom only dialog") == "safe"
        assert killer._classify("Nothing to render") == "unknown"  # old safe gone
        assert killer._classify("Plugin missing") == "diagnosis"   # diag unchanged


# ══════════════════════════════════════════════════════════════
# Unit: _extract_reaper_string
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestExtractReaperString:
    def test_returns_str_directly(self):
        assert _extract_reaper_string("hello") == "hello"

    def test_decodes_bytes(self):
        assert _extract_reaper_string(b"world") == "world"

    def test_skips_parenthetical_reaper_strings(self):
        """REAPER returns strings like '(0)' as placeholders — skip them."""
        assert _extract_reaper_string(("(0)", "", "VST: ReaEQ")) == "VST: ReaEQ"

    def test_returns_last_valid_str_from_tuple(self):
        """reversed iteration: returns last non-parenthetical str from tuple."""
        # reversed(("(0)", "first", "last")) → "last" matches first
        assert _extract_reaper_string(("(0)", "first", "last")) == "last"

    def test_returns_empty_when_only_parenthetical(self):
        assert _extract_reaper_string(("(0)", "(1)", "")) == ""

    def test_decodes_bytes_in_tuple(self):
        assert _extract_reaper_string(("(0)", b"\x00", b"FX Name")) == "FX Name"

    def test_returns_empty_for_unrecognized(self):
        assert _extract_reaper_string(42) == ""
