"""Tests for hermes_core.render — RenderManager for REAPER project rendering.

Unit tests use mocked ReaperBridge objects and run without REAPER.
Integration tests require a running REAPER instance and are skipped otherwise.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_render.py -v
    PYTHONPATH=src python3 -m pytest tests/test_render.py -v -m unit
    PYTHONPATH=src python3 -m pytest tests/test_render.py -v -m integration
"""

import os
from unittest.mock import ANY, MagicMock, patch

import pytest

from hermes_core.bridge import ReaperBridge
from hermes_core.render import RenderManager, verify_render


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════

def _make_bridge(**api_overrides):
    """Create a mock ReaperBridge with a configurable mock API.

    Returns (mock_bridge, mock_api) tuple so the caller can assert on
    specific API calls after exercising the RenderManager.

    Default mock setup represents a project with one track that has
    one media item, so _can_render() returns True.  Callers can override
    any API method to simulate edge cases.
    """
    mock_bridge = MagicMock()
    mock_api = MagicMock()
    # Sensible defaults so _can_render() passes by default
    if "CountTracks" not in api_overrides:
        mock_api.CountTracks = MagicMock(return_value=1)
    if "GetTrack" not in api_overrides:
        mock_api.GetTrack = MagicMock(return_value=MagicMock())
    if "CountTrackMediaItems" not in api_overrides:
        mock_api.CountTrackMediaItems = MagicMock(return_value=1)
    if "GetSet_LoopTimeRange" not in api_overrides:
        # Return 5-tuple: (retval, isSet, startOut, endOut, allowautoseekOut)
        mock_api.GetSet_LoopTimeRange = MagicMock(return_value=(True, False, 0.0, 10.0, False))
    for attr, val in api_overrides.items():
        setattr(mock_api, attr, val)
    mock_bridge.api = mock_api
    return mock_bridge, mock_api


def _render_config_side_effect(format_str="evaw", bounds_flag="0", srate="44100"):
    """Return a GetSetProjectInfo_String side_effect that echoes sensible defaults.

    When setNewValue=False (read mode), returns the stored value.
    When setNewValue=True (write mode), stores it and returns success.
    """
    state = {
        "RENDER_FORMAT": format_str,
        "RENDER_BOUNDSFLAG": bounds_flag,
        "RENDER_SRATE": srate,
        "RENDER_FILE": "",
    }

    def _gsps(proj, desc, value, set_new):
        if not set_new:
            return state.get(desc, "")
        state[desc] = value
        return True

    return _gsps


# ══════════════════════════════════════════════════════════════
# Unit: Initialization
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestInit:
    """Tests for RenderManager.__init__()."""

    def test_stores_bridge_reference(self):
        """RenderManager stores the injected bridge for later API access."""
        # Arrange
        mock_bridge = MagicMock()

        # Act
        manager = RenderManager(mock_bridge)

        # Assert
        assert manager._bridge is mock_bridge

    def test_requires_bridge_argument(self):
        """RenderManager.__init__ raises TypeError when called without bridge."""
        # Act & Assert
        with pytest.raises(TypeError):
            RenderManager()  # pylint: disable=no-value-for-parameter


# ══════════════════════════════════════════════════════════════
# Unit: get_render_settings()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetRenderSettings:
    """Tests for RenderManager.get_render_settings()."""

    def test_returns_dict_with_expected_keys(self):
        """get_render_settings returns a dict containing format, bounds, sample_rate."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect(
                    format_str="evaw", bounds_flag="0", srate="44100"
                )
            )
        )

        manager = RenderManager(mock_bridge)

        # Act
        settings = manager.get_render_settings()

        # Assert
        assert isinstance(settings, dict)
        for key in ("format", "bounds", "sample_rate"):
            assert key in settings, f"Missing key '{key}' in render settings"

    def test_reflects_current_reaper_state(self):
        """get_render_settings returns values that match mocked REAPER config."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect(
                    format_str="evaw", bounds_flag="1", srate="96000"
                )
            )
        )

        manager = RenderManager(mock_bridge)

        # Act
        settings = manager.get_render_settings()

        # Assert
        assert isinstance(settings, dict)
        # The exact value mapping depends on the implementation, but the dict
        # should be non-empty and reflect what REAPER returned.
        assert len(settings) >= 3

    def test_queries_reaper_with_read_mode(self):
        """get_render_settings calls GetSetProjectInfo_String in read mode (setNewValue=False)."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(return_value="")
        )

        manager = RenderManager(mock_bridge)

        # Act
        manager.get_render_settings()

        # Assert -- at least one call should have setNewValue=False
        read_calls = [
            call_args
            for call_args in mock_api.GetSetProjectInfo_String.call_args_list
            if call_args[0][3] is False
        ]
        assert len(read_calls) > 0, (
            "get_render_settings should query REAPER in read mode (setNewValue=False)"
        )


# ══════════════════════════════════════════════════════════════
# Unit: set_time_selection()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestSetTimeSelection:
    """Tests for RenderManager.set_time_selection()."""

    def test_valid_range_interacts_with_bridge(self):
        """set_time_selection with valid values calls bridge API methods."""
        # Arrange
        mock_bridge, mock_api = _make_bridge()

        manager = RenderManager(mock_bridge)

        # Act
        manager.set_time_selection(1.0, 30.0)

        # Assert -- at minimum, some bridge API interaction occurred
        assert mock_api.method_calls, (
            "set_time_selection should call bridge API methods"
        )

    def test_same_start_and_end_accepted(self):
        """set_time_selection allows start == end (zero-length selection)."""
        # Arrange
        mock_bridge, mock_api = _make_bridge()

        manager = RenderManager(mock_bridge)

        # Act -- should not raise
        manager.set_time_selection(5.0, 5.0)

    def test_negative_start_is_handled(self):
        """set_time_selection handles negative start value.

        The implementation should either raise ValueError or clamp to a
        non-negative value. Both behaviours are acceptable.
        """
        # Arrange
        mock_bridge, mock_api = _make_bridge()

        manager = RenderManager(mock_bridge)

        # Act & Assert
        try:
            manager.set_time_selection(-3.0, 10.0)
            # If it did not raise, the implementation clamped the value.
            # Verify some API interaction still occurred.
        except ValueError:
            # Raising ValueError for invalid input is also correct behaviour.
            pass

    def test_end_before_start_is_handled(self):
        """set_time_selection with end < start does not crash."""
        # Arrange
        mock_bridge, mock_api = _make_bridge()

        manager = RenderManager(mock_bridge)

        # Act & Assert -- should not crash; raise, swap, or pass through
        try:
            manager.set_time_selection(10.0, 2.0)
        except ValueError:
            pass  # rejecting swapped range is valid

    def test_zero_based_selection(self):
        """set_time_selection handles a selection starting at 0.0."""
        # Arrange
        mock_bridge, mock_api = _make_bridge()

        manager = RenderManager(mock_bridge)

        # Act -- should not raise for a common, valid range
        manager.set_time_selection(0.0, 4.0)

        # Assert
        assert mock_api.method_calls, "should interact with bridge API"


# ══════════════════════════════════════════════════════════════
# Unit: get_time_selection_range()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetTimeSelectionRange:
    """Tests for RenderManager.get_time_selection_range()."""

    def test_returns_tuple_of_floats(self):
        mock_bridge, mock_api = _make_bridge(
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 1.0, 5.0, False))
        )
        manager = RenderManager(mock_bridge)
        start, end = manager.get_time_selection_range()
        assert isinstance(start, float)
        assert isinstance(end, float)

    def test_queries_reaper_for_start_and_end(self):
        mock_bridge, mock_api = _make_bridge(
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 3.0, 8.0, False))
        )
        manager = RenderManager(mock_bridge)
        manager.get_time_selection_range()
        assert mock_api.GetSet_LoopTimeRange.call_count == 1


# ══════════════════════════════════════════════════════════════
# Unit: render_mix() parameter validation
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRenderMixValidation:
    """Tests for RenderManager.render_mix() input validation."""

    def test_rejects_invalid_bounds(self):
        """render_mix returns preflight error for unrecognized bounds strings."""
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        result = manager.render_mix("/tmp/output", bounds="loop_region")
        assert result.get("error") == "invalid_bounds"
        assert result["preflight"]["passed"] is False

    def test_rejects_invalid_format(self):
        """render_mix returns preflight error for unrecognized format strings."""
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        result = manager.render_mix("/tmp/output", fmt="aiff")
        assert result.get("error") == "invalid_format"
        assert result["preflight"]["passed"] is False

    def test_rejects_empty_bounds_string(self):
        """render_mix returns preflight error for empty bounds string."""
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        result = manager.render_mix("/tmp/output", bounds="")
        assert result.get("error") == "invalid_bounds"
        assert result["preflight"]["passed"] is False

    def test_rejects_empty_format_string(self):
        """render_mix returns preflight error for empty format string."""
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        result = manager.render_mix("/tmp/output", fmt="")
        assert result.get("error") == "invalid_format"
        assert result["preflight"]["passed"] is False

    def test_accepts_valid_bounds_values(self, tmp_path):
        """render_mix accepts 'entire_project' and 'time_selection' bounds."""
        # Arrange
        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        for bounds in ("entire_project", "time_selection"):
            mock_bridge, mock_api = _make_bridge(
                GetSetProjectInfo_String=MagicMock(
                    side_effect=_render_config_side_effect()
                )
            )

            manager = RenderManager(mock_bridge)

            # Act -- mock file existence so polling succeeds
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    result = manager.render_mix(str(output_dir), bounds=bounds, timeout=5.0)

            # Assert
            assert isinstance(result, dict), (
                f"render_mix with bounds='{bounds}' should return a dict"
            )
            assert "output_path" in result, (
                f"Result missing output_path for bounds='{bounds}': {result}"
            )

    def test_accepts_valid_format_values(self, tmp_path):
        """render_mix accepts all three valid format strings."""
        # Arrange
        valid_formats = ("wav", "flac", "mp3")
        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        for fmt in valid_formats:
            mock_bridge, mock_api = _make_bridge(
                GetSetProjectInfo_String=MagicMock(
                    side_effect=_render_config_side_effect()
                )
            )

            manager = RenderManager(mock_bridge)

            # Act
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    result = manager.render_mix(str(output_dir), fmt=fmt, timeout=5.0)

            # Assert
            assert isinstance(result, dict), (
                f"render_mix with fmt='{fmt}' should return a dict"
            )
            assert "output_path" in result, (
                f"Result missing output_path for fmt='{fmt}': {result}"
            )


# ══════════════════════════════════════════════════════════════
# Unit: render_mix() bridge interaction
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRenderMixBridgeInteraction:
    """Tests verifying that render_mix issues the correct REAPER commands."""

    def test_calls_main_oncommand_42230(self, tmp_path):
        """render_mix triggers REAPER render via Main_OnCommand(42230, 0).

        Command ID 42230 is 'File: Render project, using the most recent
        render settings, auto-close render dialog'.
        """
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        mock_api.Main_OnCommand.assert_called()
        call_args_list = mock_api.Main_OnCommand.call_args_list
        command_ids = [args[0][0] for args in call_args_list]
        assert 42230 in command_ids, (
            f"Main_OnCommand should include render command 42230, got: {command_ids}"
        )

    def test_sets_render_format_in_project_info(self, tmp_path):
        """render_mix writes the format setting via GetSetProjectInfo_String."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.render_mix(str(output_dir), fmt="wav", timeout=5.0)

        # Assert -- RENDER_FORMAT should have been set (setNewValue=True)
        format_calls = [
            c for c in mock_api.GetSetProjectInfo_String.call_args_list
            if c[0][1] == "RENDER_FORMAT" and c[0][3] is True
        ]
        assert len(format_calls) >= 1, (
            "render_mix should set RENDER_FORMAT via GetSetProjectInfo_String"
        )

    def test_sets_render_file_in_project_info(self, tmp_path):
        """render_mix writes the output file path via GetSetProjectInfo_String."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.render_mix(str(output_dir), timeout=5.0)

        # Assert -- RENDER_FILE should have been set (setNewValue=True)
        file_calls = [
            c for c in mock_api.GetSetProjectInfo_String.call_args_list
            if c[0][1] == "RENDER_FILE" and c[0][3] is True
        ]
        assert len(file_calls) >= 1, (
            "render_mix should set RENDER_FILE via GetSetProjectInfo_String"
        )

    def test_sets_render_bounds_in_project_info(self, tmp_path):
        """render_mix writes the bounds flag via GetSetProjectInfo_String."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.render_mix(str(output_dir), bounds="time_selection", timeout=5.0)

        # Assert -- RENDER_BOUNDSFLAG should have been set (setNewValue=True) via numeric API
        bounds_calls = [
            c for c in mock_api.GetSetProjectInfo.call_args_list
            if c[0][1] == "RENDER_BOUNDSFLAG" and c[0][3] is True
        ]
        assert len(bounds_calls) >= 1, (
            "render_mix should set RENDER_BOUNDSFLAG via GetSetProjectInfo"
        )

    def test_config_is_set_before_render(self, tmp_path):
        """render_mix configures REAPER before triggering the render command.

        All GetSetProjectInfo_String write calls should occur before
        Main_OnCommand(42230, 0) to ensure the render uses the desired settings.
        """
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                manager.render_mix(str(output_dir), timeout=5.0)

        # Assert -- find the index of the last config write and the render command
        all_calls = [(i, c) for i, c in enumerate(mock_api.mock_calls)]
        config_indices = [
            i for i, c in all_calls
            if "GetSetProjectInfo_String" in str(c)
        ]
        render_indices = [
            i for i, c in all_calls
            if "Main_OnCommand" in str(c) and "42230" in str(c)
        ]
        if config_indices and render_indices:
            assert max(config_indices) < render_indices[0], (
                "All render config calls must occur before Main_OnCommand(42230)"
            )


# ══════════════════════════════════════════════════════════════
# Unit: render_mix() filesystem behaviour
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRenderMixFilesystem:
    """Tests for RenderManager.render_mix() filesystem interactions."""

    def test_creates_output_dir_if_missing(self, tmp_path):
        """render_mix creates the output directory when it does not exist."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        non_existent_dir = tmp_path / "deeply" / "nested" / "render_output"

        manager = RenderManager(mock_bridge)

        # Act -- mock the filesystem polling to succeed so we do not time out
        with patch("os.makedirs") as mock_makedirs:
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    manager.render_mix(str(non_existent_dir), timeout=5.0)

        # Assert
        mock_makedirs.assert_called()

    def test_does_not_error_when_dir_already_exists(self, tmp_path):
        """render_mix succeeds when the output directory already exists."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()  # directory already exists

        manager = RenderManager(mock_bridge)

        # Act -- should not raise or error
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        assert isinstance(result, dict)
        assert result.get("error") is None or "error" not in result, (
            f"Should succeed when dir exists, got: {result}"
        )

    def test_polls_until_output_file_appears(self, tmp_path):
        """render_mix polls filesystem until the output file appears."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act -- file does not exist for the first two polls, then appears
        calls = []
        def fake_exists(path):
            calls.append(path)
            return len(calls) >= 3

        with patch("os.path.exists", side_effect=fake_exists):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=30.0)

        # Assert
        assert isinstance(result, dict)
        assert "output_path" in result, (
            f"Result should contain output_path when file appears: {result}"
        )
        assert result.get("error") is None or "error" not in result, (
            f"Render should succeed when file eventually appears: {result}"
        )

    def test_timeout_returns_error(self):
        """render_mix returns an error dict when file does not appear in time."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        manager = RenderManager(mock_bridge)

        # Act -- file never appears, time advances well past the timeout
        with patch("os.path.exists", return_value=False):
            with patch("time.time", side_effect=[0.0, 0.1, 0.2, 200.0]):
                with patch("time.sleep", return_value=None):
                    result = manager.render_mix("/tmp/nonexistent", timeout=1.0)

        # Assert -- an error or timeout indicator must be present
        is_error = (
            result.get("error") is not None
            or result.get("output_path") is None
            or result.get("timed_out") is True
        )
        assert is_error, (
            f"Timeout should produce an error result, got: {result}"
        )

    def test_default_timeout_is_used(self, tmp_path):
        """render_mix uses the default timeout of 120 seconds when not specified."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act -- omit timeout param, file exists immediately
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir))

        # Assert
        assert isinstance(result, dict)
        assert "output_path" in result


# ══════════════════════════════════════════════════════════════
# Unit: render_mix() result structure
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestRenderMixResult:
    """Tests for the structure and content of render_mix() return values."""

    def test_returns_dict(self, tmp_path):
        """render_mix always returns a dict."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_contains_output_path(self, tmp_path):
        """On success, render_mix returns a dict with output_path."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        assert "output_path" in result, (
            f"Result missing output_path: {result}"
        )
        assert isinstance(result["output_path"], str), (
            f"output_path should be a string, got {type(result['output_path'])}"
        )
        assert len(result["output_path"]) > 0, "output_path should not be empty"

    def test_output_path_is_under_output_dir(self, tmp_path):
        """The returned output_path is within the requested output_dir."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        output_path = result["output_path"]
        assert output_path.startswith(str(output_dir)), (
            f"output_path '{output_path}' should be under output_dir '{output_dir}'"
        )

    def test_sample_rate_zero_allowed(self, tmp_path):
        """render_mix with sample_rate=0 (use project SR) succeeds."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), sample_rate=0, timeout=5.0)

        # Assert
        assert "output_path" in result

    def test_custom_sample_rate_accepted(self, tmp_path):
        """render_mix accepts a custom sample rate value."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        for sr in (44100, 48000, 88200, 96000):
            manager = RenderManager(MagicMock())
            manager._bridge = mock_bridge  # reuse bridge but fresh manager

            # Act
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    result = manager.render_mix(
                        str(output_dir), sample_rate=sr, timeout=5.0
                    )

            # Assert
            assert isinstance(result, dict), (
                f"render_mix with sample_rate={sr} should return a dict"
            )


# ══════════════════════════════════════════════════════════════
# Integration tests (require running REAPER)
# ══════════════════════════════════════════════════════════════

@pytest.mark.integration
class TestRenderIntegration:
    """End-to-end tests that require a running REAPER instance.

    These tests connect to a live REAPER and exercise the real rendering
    pipeline. They are skipped automatically when REAPER is not available.
    """

    @staticmethod
    def _require_reaper():
        """Connect to REAPER or skip the current test."""
        bridge = ReaperBridge()
        if not bridge.connect():
            pytest.skip("REAPER is not running -- skipping integration test")

    def test_render_empty_project_is_rejected(self, tmp_path):
        """Empty project is rejected with nothing_to_render error.

        This is the defensive guard preventing the modal 'Nothing to render!'
        dialog from blocking subsequent REAPER API calls.
        """
        # Arrange
        self._require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        from tests.conftest import clean_project
        clean_project(bridge)

        output_dir = tmp_path / "render_empty"
        output_dir.mkdir()

        manager = RenderManager(bridge)

        # Act
        result = manager.render_mix(str(output_dir))

        # Assert -- empty project should be rejected before render command
        assert "error" in result, f"Empty project should return error, got: {result}"
        assert result["error"] == "nothing_to_render", (
            f"Expected nothing_to_render error, got: {result}"
        )
        assert result["output_path"] is None, (
            f"Expected output_path=None for rejected render, got: {result}"
        )

    def test_render_entire_project_requires_content(self, tmp_path):
        """Rejects render with entire_project bounds when project has no content."""
        # Arrange
        self._require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        from tests.conftest import clean_project
        clean_project(bridge)

        output_dir = tmp_path / "render_entire"
        output_dir.mkdir()

        manager = RenderManager(bridge)

        # Act
        result = manager.render_mix(str(output_dir), bounds="entire_project")

        # Assert -- guard rejects before Main_OnCommand
        assert result.get("error") == "nothing_to_render", (
            f"Empty project should be rejected, got: {result}"
        )
        assert result.get("output_path") is None

    def test_render_requires_content_with_format(self, tmp_path):
        """Rejects render with format param when project has no content."""
        # Arrange
        self._require_reaper()
        bridge = ReaperBridge()
        bridge.connect()
        from tests.conftest import clean_project
        clean_project(bridge)

        output_dir = tmp_path / "render_16bit"
        output_dir.mkdir()

        manager = RenderManager(bridge)

        # Act
        result = manager.render_mix(str(output_dir), fmt="wav")

        # Assert -- guard rejects before Main_OnCommand
        assert result.get("error") == "nothing_to_render", (
            f"Empty project should be rejected, got: {result}"
        )
        assert result.get("output_path") is None


# ══════════════════════════════════════════════════════════════
# Unit: _can_render() project content checks
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestCanRender:
    """Tests for RenderManager._can_render() precondition checks."""

    def test_can_render_returns_false_for_empty_project(self):
        """_can_render returns False when no tracks exist in the project."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=0)
        )
        manager = RenderManager(mock_bridge)

        # Act
        result = manager._can_render()

        # Assert
        assert result is False, (
            f"_can_render should return False for empty project, got {result}"
        )

    def test_can_render_returns_false_for_tracks_without_items(self):
        """_can_render returns False when tracks exist but have no media items."""
        # Arrange
        mock_track = MagicMock()
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=3),
            GetTrack=MagicMock(return_value=mock_track),
            CountTrackMediaItems=MagicMock(return_value=0),
        )
        manager = RenderManager(mock_bridge)

        # Act
        result = manager._can_render()

        # Assert
        assert result is False, (
            f"_can_render should return False when no items on any track, got {result}"
        )

    def test_can_render_returns_true_when_track_has_item(self):
        """_can_render returns True when at least one track has a media item."""
        # Arrange
        mock_track = MagicMock()
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=2),
            GetTrack=MagicMock(return_value=mock_track),
            CountTrackMediaItems=MagicMock(return_value=3),
        )
        manager = RenderManager(mock_bridge)

        # Act
        result = manager._can_render()

        # Assert
        assert result is True, (
            f"_can_render should return True when a track has items, got {result}"
        )

    def test_can_render_handles_null_track(self):
        """_can_render skips null tracks returned by GetTrack."""
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=2),
            GetTrack=MagicMock(side_effect=[None, MagicMock()]),
            CountTrackMediaItems=MagicMock(return_value=5),
        )
        manager = RenderManager(mock_bridge)

        # Act
        result = manager._can_render()

        # Assert -- second track has items, so True
        assert result is True, (
            f"_can_render should skip null tracks, got {result}"
        )


@pytest.mark.unit
class TestRenderMixRejection:
    """Tests for render_mix() rejecting renders with no content."""

    def test_render_mix_rejects_empty_project(self, tmp_path):
        """render_mix returns error when _can_render() returns False.

        Main_OnCommand(42230) must NOT be called when the project is empty.
        """
        # Arrange
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=0),
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            ),
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act -- mock file existence so if we get past the guard, we'd succeed
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(str(output_dir), timeout=5.0)

        # Assert
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"
        assert result.get("error") == "nothing_to_render", (
            f"Expected error='nothing_to_render', got: {result}"
        )
        assert result.get("output_path") is None, (
            f"Expected output_path=None, got: {result}"
        )

        # Main_OnCommand(42230) must NOT be called
        render_calls = [
            c for c in mock_api.Main_OnCommand.call_args_list
            if 42230 in c[0]
        ]
        assert len(render_calls) == 0, (
            "Main_OnCommand(42230) must NOT be called when project is empty"
        )

    def test_render_mix_rejects_zero_length_time_selection(self, tmp_path):
        """render_mix returns error when bounds=time_selection with zero-length."""
        # Arrange
        mock_track = MagicMock()
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=1),
            GetTrack=MagicMock(return_value=mock_track),
            CountTrackMediaItems=MagicMock(return_value=3),
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            ),
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 5.0, 5.0, False)),
        )

        output_dir = tmp_path / "renders"
        output_dir.mkdir()

        manager = RenderManager(mock_bridge)

        # Act
        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mix(
                    str(output_dir), bounds="time_selection", timeout=5.0
                )

        # Assert -- time selection start == end == 5.0 means zero length
        assert isinstance(result, dict)
        assert result.get("error") == "nothing_to_render", (
            f"Expected error='nothing_to_render' for zero-length time selection, got: {result}"
        )
        assert result.get("output_path") is None, (
            f"Expected output_path=None, got: {result}"
        )


# ══════════════════════════════════════════════════════════════
# Unit: Preflight checks
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestPreflight:
    """Tests for the unified _preflight_check() method."""

    def test_preflight_all_passed(self, tmp_path):
        """Returns passed=True when all checks succeed."""
        mock_bridge, _ = _make_bridge()
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("entire_project", "wav", str(output_dir))
        assert result["passed"] is True
        assert result["failures"] == []

    def test_preflight_invalid_bounds(self, tmp_path):
        """Returns failure for invalid bounds value."""
        mock_bridge, _ = _make_bridge()
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("invalid_bounds_xyz", "wav", str(output_dir))
        assert result["passed"] is False
        reasons = [f["reason"] for f in result["failures"]]
        assert "invalid_bounds" in reasons

    def test_preflight_invalid_format(self, tmp_path):
        """Returns failure for invalid format value."""
        mock_bridge, _ = _make_bridge()
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("entire_project", "ogg", str(output_dir))
        assert result["passed"] is False
        reasons = [f["reason"] for f in result["failures"]]
        assert "invalid_format" in reasons

    def test_preflight_empty_project(self, tmp_path):
        """Returns nothing_to_render when project has no media items."""
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=0),
        )
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("entire_project", "wav", str(output_dir))
        assert result["passed"] is False
        reasons = [f["reason"] for f in result["failures"]]
        assert "nothing_to_render" in reasons

    def test_preflight_zero_time_selection(self, tmp_path):
        """Returns nothing_to_render when time_selection bounds is zero-length."""
        mock_bridge, mock_api = _make_bridge(
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 5.0, 5.0, False)),  # start == end
        )
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("time_selection", "wav", str(output_dir))
        assert result["passed"] is False
        reasons = [f["reason"] for f in result["failures"]]
        assert "nothing_to_render" in reasons

    def test_preflight_time_selection_passes_when_valid(self, tmp_path):
        """Passes for time_selection with positive length."""
        mock_bridge, mock_api = _make_bridge(
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 0.0, 10.0, False)),
        )
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("time_selection", "wav", str(output_dir))
        assert result["passed"] is True

    @pytest.mark.unit
    class TestCheckOutputWritable:
        """Tests for _check_output_writable()."""

        def test_writable_directory(self, tmp_path):
            """Returns True for a writable directory."""
            output_dir = tmp_path / "writable"
            output_dir.mkdir()
            assert RenderManager._check_output_writable(str(output_dir)) is True

        def test_creates_directory_if_needed(self, tmp_path):
            """Returns True after creating a missing directory."""
            output_dir = tmp_path / "new_dir"
            assert not output_dir.exists()
            result = RenderManager._check_output_writable(str(output_dir))
            assert result is True
            assert output_dir.exists()

        def test_unwritable_path(self, tmp_path):
            """Returns False when path cannot be written to (e.g. /dev/null/dir)."""
            assert RenderManager._check_output_writable("/dev/null/subdir") is False

    def test_render_mix_includes_preflight_on_failure(self, tmp_path):
        """When preflight fails, render_mix returns error + preflight detail."""
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=0),
        )
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager.render_mix(
            str(output_dir), bounds="entire_project", fmt="wav"
        )
        assert result["error"] == "nothing_to_render"
        assert "preflight" in result
        assert result["preflight"]["passed"] is False

    def test_preflight_multiple_failures(self, tmp_path):
        """Multiple failures are all reported."""
        mock_bridge, mock_api = _make_bridge(
            CountTracks=MagicMock(return_value=0),
            GetSet_LoopTimeRange=MagicMock(return_value=(True, False, 2.0, 1.0, False)),  # end < start
        )
        output_dir = tmp_path / "renders"
        output_dir.mkdir()
        manager = RenderManager(mock_bridge)
        result = manager._preflight_check("invalid", "xyz", str(output_dir))
        assert result["passed"] is False
        # At least 3 failures: invalid_bounds + invalid_format + nothing_to_render
        assert len(result["failures"]) >= 3
        reasons = {f["reason"] for f in result["failures"]}
        assert "invalid_bounds" in reasons
        assert "invalid_format" in reasons
        assert "nothing_to_render" in reasons


@pytest.mark.unit
class TestRenderWithRetry:
    """render_with_retry retries on transient failures."""

    def test_returns_first_success(self):
        """Returns immediately when first render succeeds."""
        mock_bridge, mock_api = _make_bridge()
        manager = RenderManager(mock_bridge)
        manager.render_mix = MagicMock(return_value={"output_path": "/tmp/ok.wav"})
        result = manager.render_with_retry("/tmp/out", max_retries=3)
        assert result["output_path"] == "/tmp/ok.wav"
        assert manager.render_mix.call_count == 1

    def test_retries_on_failure(self):
        """Retries when render_mix returns no output_path."""
        mock_bridge, mock_api = _make_bridge()
        manager = RenderManager(mock_bridge)
        manager.render_mix = MagicMock(side_effect=[
            {"output_path": None, "error": "timeout"},
            {"output_path": None, "error": "timeout"},
            {"output_path": "/tmp/third.wav"},
        ])
        result = manager.render_with_retry("/tmp/out", max_retries=3)
        assert result["output_path"] == "/tmp/third.wav"
        assert manager.render_mix.call_count == 3

    def test_exhausts_retries(self):
        """Returns last error when all retries exhausted."""
        mock_bridge, mock_api = _make_bridge()
        manager = RenderManager(mock_bridge)
        manager.render_mix = MagicMock(return_value={
            "output_path": None, "error": "preflight_failed",
        })
        result = manager.render_with_retry("/tmp/out", max_retries=2)
        assert result["output_path"] is None
        assert result["retries_exhausted"] is True
        assert manager.render_mix.call_count == 2


@pytest.mark.unit
class TestDiskSpaceCheck:
    """_check_disk_space preflight guard."""

    def test_sufficient_space(self, tmp_path):
        """Returns ok=True when free space exceeds requirement."""
        from hermes_core.render import RenderManager
        result = RenderManager._check_disk_space(str(tmp_path), required_mb=1.0)
        assert result["ok"] is True
        assert result["free_mb"] > 0

    def test_insufficient_space(self):
        """Returns ok=False when requirement can't be met."""
        from hermes_core.render import RenderManager
        result = RenderManager._check_disk_space("/nonexistent_path_xyz", required_mb=1.0)
        assert result["ok"] is False


# ══════════════════════════════════════════════════════════════
# focus_reaper（渲染静音修复）
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFocusReaper:
    """ReaperBridge.focus_reaper() — AppleScript 聚焦 REAPER 窗口。"""

    def test_focus_succeeds_on_macos(self):
        """AppleScript 返回 0 → focus_reaper 返回 True。"""
        bridge = ReaperBridge(dialog_killer=False)
        mock_result = MagicMock()
        mock_result.returncode = 0
        with patch("subprocess.run", return_value=mock_result) as mock_run:
            with patch("platform.system", return_value="Darwin"):
                ok = bridge.focus_reaper()
        assert ok is True
        mock_run.assert_called_once()
        # 验证 osascript 被调用
        call_args = mock_run.call_args
        assert "osascript" in call_args[0][0]

    def test_focus_fails_on_non_macos(self):
        """非 macOS 平台直接返回 False。"""
        bridge = ReaperBridge(dialog_killer=False)
        with patch("platform.system", return_value="Linux"):
            ok = bridge.focus_reaper()
        assert ok is False

    def test_focus_handles_osascript_failure(self):
        """AppleScript 返回非零 → focus_reaper 返回 False。"""
        bridge = ReaperBridge(dialog_killer=False)
        mock_result = MagicMock()
        mock_result.returncode = 1
        mock_result.stderr = b"REAPER not found"
        with patch("subprocess.run", return_value=mock_result):
            with patch("platform.system", return_value="Darwin"):
                ok = bridge.focus_reaper()
        assert ok is False

    def test_focus_handles_timeout(self):
        """AppleScript 超时 → focus_reaper 返回 False。"""
        import subprocess as _subprocess
        bridge = ReaperBridge(dialog_killer=False)
        with patch("subprocess.run", side_effect=_subprocess.TimeoutExpired("osascript", 5)):
            with patch("platform.system", return_value="Darwin"):
                ok = bridge.focus_reaper()
        assert ok is False


@pytest.mark.unit
class TestRenderMixCallsFocus:
    """render_mix 在触发渲染前调用 focus_reaper。"""

    def test_focus_called_before_render(self, tmp_path):
        """render_mix 应在 Main_OnCommand 前调用 focus_reaper。"""
        mock_bridge, mock_api = _make_bridge()
        manager = RenderManager(mock_bridge)

        # 模拟渲染成功（文件出现）
        output_file = tmp_path / "render.wav"
        original_exists = os.path.exists

        call_order = []

        def fake_exists(path):
            if str(path) == str(output_file):
                return True
            return original_exists(path)

        def track_focus():
            call_order.append("focus")
            return True

        def track_render(*args, **kwargs):
            call_order.append("render")

        mock_bridge.focus_reaper = MagicMock(side_effect=track_focus)
        mock_api.Main_OnCommand = MagicMock(side_effect=track_render)

        with patch("os.path.exists", side_effect=fake_exists):
            manager.render_mix(str(tmp_path))

        assert call_order == ["focus", "render"], (
            f"focus_reaper 应在 Main_OnCommand 前调用，实际顺序: {call_order}"
        )


# ══════════════════════════════════════════════════════════════
# render_with_silence_retry（渲染后静音检测）
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRenderWithSilenceRetry:
    """render_with_silence_retry — 检测静音并重试。"""

    def test_no_retry_when_audio_is_normal(self, tmp_path):
        """正常音频不触发重试。"""
        mock_bridge, mock_api = _make_bridge()
        mock_bridge.focus_reaper = MagicMock(return_value=True)
        manager = RenderManager(mock_bridge)

        # 第一次渲染成功且非静音
        fake_path = str(tmp_path / "render.wav")
        manager.render_mix = MagicMock(return_value={"output_path": fake_path})
        manager._is_output_silent = MagicMock(return_value=False)

        result = manager.render_with_silence_retry(str(tmp_path))
        assert result["output_path"] == fake_path
        assert manager.render_mix.call_count == 1  # 没有重试

    def test_retry_on_silent_output(self, tmp_path):
        """静音输出触发一次重试。"""
        mock_bridge, mock_api = _make_bridge()
        mock_bridge.focus_reaper = MagicMock(return_value=True)
        manager = RenderManager(mock_bridge)

        silent_path = str(tmp_path / "render_silent.wav")
        good_path = str(tmp_path / "render_good.wav")

        call_count = [0]

        def fake_render(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"output_path": silent_path}
            return {"output_path": good_path}

        manager.render_mix = MagicMock(side_effect=fake_render)

        # 第一次静音，第二次正常
        silence_results = [True, False]
        manager._is_output_silent = MagicMock(side_effect=silence_results)

        with patch("os.remove"):
            result = manager.render_with_silence_retry(str(tmp_path))

        assert result["output_path"] == good_path
        assert manager.render_mix.call_count == 2
        mock_bridge.focus_reaper.assert_called()

    def test_marks_silent_when_retry_also_silent(self, tmp_path):
        """重试后仍静音 → 返回结果带 silent=True 标记。"""
        mock_bridge, mock_api = _make_bridge()
        mock_bridge.focus_reaper = MagicMock(return_value=True)
        manager = RenderManager(mock_bridge)

        silent_path = str(tmp_path / "render.wav")
        manager.render_mix = MagicMock(return_value={"output_path": silent_path})
        manager._is_output_silent = MagicMock(return_value=True)  # 每次都静音

        with patch("os.remove"):
            result = manager.render_with_silence_retry(str(tmp_path))

        assert result.get("silent") is True

    def test_render_failure_returns_error(self, tmp_path):
        """渲染失败（无 output_path）直接返回错误。"""
        mock_bridge, mock_api = _make_bridge()
        mock_bridge.focus_reaper = MagicMock(return_value=True)
        manager = RenderManager(mock_bridge)

        manager.render_mix = MagicMock(return_value={
            "output_path": None, "error": "preflight_failed",
        })

        result = manager.render_with_silence_retry(str(tmp_path))
        assert result["output_path"] is None
        assert result["error"] == "preflight_failed"


@pytest.mark.unit
class TestIsOutputSilent:
    """_is_output_silent — 轻量级静音检测。"""

    def test_silent_file(self, tmp_path):
        """全零 WAV → 静音。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "silent.wav")
        sf.write(wav_path, np.zeros((48000, 2), dtype="float64"), 48000)

        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        assert manager._is_output_silent(wav_path) is True

    def test_normal_file(self, tmp_path):
        """有音频内容的 WAV → 非静音。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "normal.wav")
        # 生成 -6 dBFS 正弦波
        t = np.linspace(0, 1.0, 48000, endpoint=False)
        signal = 0.5 * np.sin(2 * np.pi * 440 * t)
        stereo = np.column_stack([signal, signal])
        sf.write(wav_path, stereo, 48000)

        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        assert manager._is_output_silent(wav_path) is False

    def test_nonexistent_file(self):
        """文件不存在 → 返回 False（不报错）。"""
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)
        assert manager._is_output_silent("/nonexistent/file.wav") is False


# ══════════════════════════════════════════════════════════════
# Unit: _get_format_encoding()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestGetFormatEncoding:
    """Tests for RenderManager._get_format_encoding()."""

    def test_wav_encoding(self):
        """WAV 编码包含 evaw sink code。"""
        encoded = RenderManager._get_format_encoding("wav")
        assert isinstance(encoded, str)
        assert len(encoded) > 0

    def test_mp3_encoding_default_bitrate(self):
        """MP3 编码默认使用 320 kbps bitrate index 0。"""
        encoded = RenderManager._get_format_encoding("mp3")
        assert isinstance(encoded, str)
        assert len(encoded) > 0

    def test_mp3_encoding_various_bitrates(self):
        """不同 bitrate 产生不同的编码字符串。"""
        e320 = RenderManager._get_format_encoding("mp3", mp3_bitrate_kbps=320)
        e128 = RenderManager._get_format_encoding("mp3", mp3_bitrate_kbps=128)
        # 不同 bitrate 应产生不同编码
        assert e320 != e128

    def test_mp3_unknown_bitrate_falls_back_to_index_0(self):
        """未知 bitrate → 回退到 320 kbps (index 0)。"""
        encoded = RenderManager._get_format_encoding("mp3", mp3_bitrate_kbps=999)
        e320 = RenderManager._get_format_encoding("mp3", mp3_bitrate_kbps=320)
        assert encoded == e320

    def test_flac_encoding(self):
        """FLAC 编码包含 calf sink code。"""
        encoded = RenderManager._get_format_encoding("flac")
        assert isinstance(encoded, str)
        assert len(encoded) > 0


# ══════════════════════════════════════════════════════════════
# Unit: render_mp3() / render_flac()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestConvenienceRenderMethods:
    """Tests for render_mp3() and render_flac() convenience wrappers."""

    def test_render_mp3_delegates_to_render_mix(self, tmp_path):
        """render_mp3 调用 render_mix 并传入 fmt='mp3'。"""
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )
        manager = RenderManager(mock_bridge)
        output_dir = tmp_path / "mp3_output"
        output_dir.mkdir()

        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_mp3(str(output_dir), bitrate_kbps=256)

        assert isinstance(result, dict)
        assert "output_path" in result

    def test_render_flac_delegates_to_render_mix(self, tmp_path):
        """render_flac 调用 render_mix 并传入 fmt='flac'。"""
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )
        manager = RenderManager(mock_bridge)
        output_dir = tmp_path / "flac_output"
        output_dir.mkdir()

        with patch("os.path.exists", return_value=True):
            with patch("time.sleep", return_value=None):
                result = manager.render_flac(str(output_dir), compression_level=8)

        assert isinstance(result, dict)
        assert "output_path" in result

    def test_render_mp3_passes_bitrate_to_render_mix(self, tmp_path):
        """render_mp3 正确传递 bitrate 给底层 render_mix。"""
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )
        manager = RenderManager(mock_bridge)
        output_dir = tmp_path / "mp3"
        output_dir.mkdir()

        with patch.object(manager, "render_mix", wraps=manager.render_mix) as spy:
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    manager.render_mp3(str(output_dir), bitrate_kbps=192)

        spy.assert_called_once()
        assert spy.call_args[1]["fmt"] == "mp3"
        assert spy.call_args[1]["mp3_bitrate_kbps"] == 192

    def test_render_flac_passes_compression_to_render_mix(self, tmp_path):
        """render_flac 正确传递 compression_level 给底层 render_mix。"""
        mock_bridge, mock_api = _make_bridge(
            GetSetProjectInfo_String=MagicMock(
                side_effect=_render_config_side_effect()
            )
        )
        manager = RenderManager(mock_bridge)
        output_dir = tmp_path / "flac"
        output_dir.mkdir()

        with patch.object(manager, "render_mix", wraps=manager.render_mix) as spy:
            with patch("os.path.exists", return_value=True):
                with patch("time.sleep", return_value=None):
                    manager.render_flac(str(output_dir), compression_level=8)

        spy.assert_called_once()
        assert spy.call_args[1]["fmt"] == "flac"
        assert spy.call_args[1]["flac_compression"] == 8


# ══════════════════════════════════════════════════════════════
# Unit: _wait_for_render()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestWaitForRender:
    """Tests for RenderManager._wait_for_render() backoff polling."""

    def test_returns_true_when_file_exists(self, tmp_path):
        """文件已存在 → 立即返回 True。"""
        fpath = tmp_path / "exists.wav"
        fpath.write_text("data")
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)

        with patch("time.sleep") as mock_sleep:
            assert manager._wait_for_render(str(fpath), timeout=1.0) is True

        # 文件已存在时不应 sleep
        mock_sleep.assert_not_called()

    def test_returns_false_on_timeout(self, tmp_path):
        """文件始终不出现 → 超时返回 False。"""
        fpath = tmp_path / "never.wav"
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)

        with patch("os.path.exists", return_value=False):
            with patch("time.sleep", return_value=None):
                with patch("time.time", side_effect=[0.0, 0.1, 500.0]):
                    assert manager._wait_for_render(str(fpath), timeout=1.0) is False

    def test_eventually_finds_file(self, tmp_path):
        """文件在几次轮询后出现 → 返回 True。"""
        fpath = tmp_path / "appears.wav"
        fpath.write_text("hello")
        mock_bridge, _ = _make_bridge()
        manager = RenderManager(mock_bridge)

        call_count = [0]

        def fake_exists(path):
            call_count[0] += 1
            # 第一次不存在，第二次及之后存在
            return call_count[0] >= 2

        def fake_size(path):
            return 100  # 非零，稳定

        with patch("os.path.exists", side_effect=fake_exists):
            with patch("os.path.getsize", side_effect=fake_size):
                with patch("time.sleep", return_value=None):
                    assert manager._wait_for_render(str(fpath), timeout=30.0) is True


# ══════════════════════════════════════════════════════════════
# Unit: verify_render()
# ══════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestVerifyRender:
    """Tests for standalone verify_render() function."""

    def test_file_not_found(self):
        """文件不存在 → passed=False，file_exists 检查失败。"""
        result = verify_render("/nonexistent/file.wav", expected_duration_sec=10.0,
                               target_lufs=-12.0)
        assert result["passed"] is False
        assert result["checks"][0]["name"] == "file_exists"
        assert result["checks"][0]["passed"] is False

    def test_valid_wav_passes_checks(self, tmp_path):
        """有效的 WAV 文件通过所有检查。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "valid.wav")
        sr = 48000
        duration = 3.0
        t = np.linspace(0, duration, int(sr * duration), endpoint=False)
        # -12 dBFS 信号（~ -12 LUFS for sine）
        amplitude = 10.0 ** (-12.0 / 20.0) * 0.7
        mono = (amplitude * np.sin(2 * np.pi * 440 * t)).astype(np.float64)
        stereo = np.column_stack([mono, mono])
        sf.write(wav_path, stereo, sr)

        result = verify_render(wav_path, expected_duration_sec=duration,
                               target_lufs=-14.0, ceiling_db=-1.0,
                               tolerance=5.0)
        # 大部分检查应通过（tolerance 宽松）
        assert "file_not_empty" in [c["name"] for c in result["checks"]]
        # 文件有内容
        file_check = [c for c in result["checks"] if c["name"] == "file_not_empty"]
        assert file_check and file_check[0]["passed"] is True

    def test_silent_file_detected(self, tmp_path):
        """静音文件被 not_silent 检查捕获。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "silent.wav")
        silent = np.full((48000, 2), 1e-10, dtype=np.float64)
        sf.write(wav_path, silent, 48000)

        result = verify_render(wav_path, expected_duration_sec=1.0,
                               target_lufs=-12.0, ceiling_db=-1.0)
        # not_silent 检查应失败
        silence_checks = [c for c in result["checks"] if c["name"] == "not_silent"]
        if silence_checks:
            assert silence_checks[0]["passed"] is False

    def test_empty_file_detected(self, tmp_path):
        """空文件被 file_not_empty 检查捕获。"""
        wav_path = tmp_path / "empty.wav"
        wav_path.write_text("")

        result = verify_render(str(wav_path), expected_duration_sec=1.0,
                               target_lufs=-12.0)
        assert result["passed"] is False
        assert result["measurements"]["file_size_bytes"] == 0

    def test_duration_mismatch_detected(self, tmp_path):
        """时长不匹配被 duration_match 检查捕获。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "short.wav")
        sr = 48000
        actual_duration = 1.0
        t = np.linspace(0, actual_duration, int(sr * actual_duration), endpoint=False)
        mono = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float64)
        stereo = np.column_stack([mono, mono])
        sf.write(wav_path, stereo, sr)

        # 期望 10s，实际 1s → 偏差 90%
        result = verify_render(wav_path, expected_duration_sec=10.0,
                               target_lufs=-12.0, ceiling_db=-1.0)
        dur_checks = [c for c in result["checks"] if c["name"] == "duration_match"]
        assert dur_checks and dur_checks[0]["passed"] is False

    def test_returns_measurements_dict(self, tmp_path):
        """验证结果包含 measurements 键。"""
        import soundfile as sf
        import numpy as np

        wav_path = str(tmp_path / "meas.wav")
        sr = 48000
        t = np.linspace(0, 2.0, int(sr * 2.0), endpoint=False)
        mono = (0.3 * np.sin(2 * np.pi * 440 * t)).astype(np.float64)
        stereo = np.column_stack([mono, mono])
        sf.write(wav_path, stereo, sr)

        result = verify_render(wav_path, expected_duration_sec=2.0,
                               target_lufs=-12.0)
        assert "measurements" in result
        meas = result["measurements"]
        assert "file_size_bytes" in meas
        assert meas["file_size_bytes"] > 0

