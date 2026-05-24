"""Tests for hermes_core.normalize — Normalizer with mocked dependencies.

Unit tests require no REAPER. All external dependencies (TrackManager,
RenderManager, SignalAnalyzer) are mocked.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_normalize.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from hermes_core.track import TrackInfo
from hermes_core.signal import SignalReport


# ══════════════════════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════════════════════


def _make_track_info(index=0, name="TestTrack", volume_db=0.0):
    """Create a minimal TrackInfo for use in normalization tests."""
    return TrackInfo(
        index=index,
        name=name,
        volume_db=volume_db,
        pan=0.0,
        mute=False,
        solo=False,
        fx_count=0,
        depth=0,
        item_count=1,
        selected=False,
    )


def _make_signal_report(integrated_lufs=-20.0):
    """Create a SignalReport with the given LUFS value."""
    return SignalReport(
        rms_db=-15.0,
        peak_db=-9.0,
        integrated_lufs=integrated_lufs,
        true_peak_dbtp=-8.5,
        clip_count=0,
        clip_passed=True,
        silence_passed=True,
        duration_sec=5.0,
        sample_rate=48000,
    )


def _setup_normalizer_with_mocks():
    """Create a Normalizer with mocked TrackManager and RenderManager.

    Returns (normalizer, mock_tracks, mock_render).
    """
    from hermes_core.normalize import Normalizer

    mock_bridge = MagicMock()
    norm = Normalizer(mock_bridge)

    mock_tracks = MagicMock()
    mock_render = MagicMock()
    norm._tracks = mock_tracks
    norm._render = mock_render

    # Defaults for solo backup/restore and time selection save/restore
    mock_tracks.list_all = MagicMock(return_value=[])
    mock_render.get_time_selection_range = MagicMock(return_value=(0.0, 0.0))

    return norm, mock_tracks, mock_render


# ══════════════════════════════════════════════════════════════
# NormalizeResult
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeResult:
    """Tests for the NormalizeResult dataclass."""

    def test_construction_and_fields(self):
        """NormalizeResult stores all fields correctly."""
        from hermes_core.normalize import NormalizeResult

        # Arrange & Act
        result = NormalizeResult(
            track_index=0,
            track_name="Kick",
            original_lufs=-20.0,
            target_lufs=-14.0,
            gain_applied_db=6.0,
            success=True,
        )

        # Assert
        assert result.track_index == 0
        assert result.track_name == "Kick"
        assert result.original_lufs == -20.0
        assert result.target_lufs == -14.0
        assert result.gain_applied_db == 6.0
        assert result.success is True

    def test_failed_result_defaults(self):
        """A failed NormalizeResult has success=False and zero gain."""
        from hermes_core.normalize import NormalizeResult

        # Arrange & Act
        result = NormalizeResult(
            track_index=5,
            track_name="",
            original_lufs=0.0,
            target_lufs=-14.0,
            gain_applied_db=0.0,
            success=False,
        )

        # Assert
        assert result.success is False
        assert result.gain_applied_db == 0.0
        assert result.original_lufs == 0.0
        assert result.track_index == 5


# ══════════════════════════════════════════════════════════════
# Normalizer construction
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizerConstruction:
    """Tests for Normalizer.__init__()."""

    def test_stores_bridge_and_creates_managers(self):
        """Normalizer stores bridge and creates TrackManager/RenderManager."""
        from hermes_core.normalize import Normalizer
        from hermes_core.track import TrackManager
        from hermes_core.render import RenderManager

        # Arrange
        mock_bridge = MagicMock()

        # Act
        norm = Normalizer(mock_bridge)

        # Assert
        assert norm._bridge is mock_bridge
        assert isinstance(norm._tracks, TrackManager)
        assert isinstance(norm._render, RenderManager)

    def test_requires_bridge_argument(self):
        """Normalizer.__init__ raises TypeError when called without bridge."""
        from hermes_core.normalize import Normalizer

        # Act & Assert
        with pytest.raises(TypeError):
            Normalizer()  # pylint: disable=no-value-for-parameter


# ══════════════════════════════════════════════════════════════
# normalize_track
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeTrack:
    """Tests for Normalizer.normalize_track()."""

    def test_computes_correct_gain_offset(self):
        """normalize_track computes correct gain offset.

        LUFS=-20, target=-14 => offset=+6 dB.
        """
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Kick", volume_db=0.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        mock_render.set_time_selection = MagicMock()
        signal_report = _make_signal_report(integrated_lufs=-20.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(0, target_lufs=-14.0)

        # Assert
        assert result.success is True
        assert result.gain_applied_db == pytest.approx(6.0, abs=0.01)
        assert result.original_lufs == -20.0
        assert result.target_lufs == -14.0
        assert result.track_name == "Kick"

    def test_returns_failed_result_when_track_not_found(self):
        """normalize_track returns failed NormalizeResult when track is None."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        mock_tracks.get = MagicMock(return_value=None)

        with patch("hermes_core.normalize.SignalAnalyzer", autospec=True):
            # Act
            result = norm.normalize_track(0)

        # Assert
        assert result.success is False
        assert result.track_index == 0
        assert result.gain_applied_db == 0.0
        mock_render.render_mix.assert_not_called()

    def test_returns_failed_result_when_render_fails(self):
        """normalize_track returns failed result when render_mix has no output_path."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Snare", volume_db=0.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": None, "error": "timeout"}
        )

        with patch("hermes_core.normalize.SignalAnalyzer", autospec=True):
            # Act
            result = norm.normalize_track(0)

        # Assert
        assert result.success is False
        assert result.track_name == "Snare"
        assert result.gain_applied_db == 0.0

    def test_applies_gain_to_track_fader(self):
        """normalize_track applies computed gain to the track fader via set_volume."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Bass", volume_db=-3.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        # measured_lufs=-26, target=-14 => offset=+12
        signal_report = _make_signal_report(integrated_lufs=-26.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(0, target_lufs=-14.0)

        # Assert
        assert result.success is True
        expected_volume = track_info.volume_db + result.gain_applied_db
        mock_tracks.set_volume.assert_called_once_with(0, expected_volume)

    def test_returns_normalize_result_with_correct_fields(self):
        """normalize_track returns NormalizeResult with all expected fields populated."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=3, name="Vocal", volume_db=-1.5)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report(integrated_lufs=-18.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(3, target_lufs=-14.0)

        # Assert
        assert result.track_index == 3
        assert result.track_name == "Vocal"
        assert result.original_lufs == -18.0
        assert result.target_lufs == -14.0
        assert result.gain_applied_db == pytest.approx(4.0, abs=0.01)
        assert result.success is True

    def test_with_custom_target_lufs_and_duration(self):
        """normalize_track passes custom target_lufs and duration correctly."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Guitar", volume_db=2.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        # measured_lufs=-30, target=-23 => offset=+7
        signal_report = _make_signal_report(integrated_lufs=-30.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(0, target_lufs=-23.0, duration=3.0)

        # Assert
        assert result.success is True
        assert result.target_lufs == -23.0
        assert result.gain_applied_db == pytest.approx(7.0, abs=0.01)
        mock_render.set_time_selection.assert_any_call(0.0, 3.0)
        mock_render.render_mix.assert_called_once()
        # Verify the render was done with time_selection bounds
        call_kwargs = mock_render.render_mix.call_args[1]
        assert call_kwargs["bounds"] == "time_selection"

    def test_sets_time_selection_before_render(self):
        """normalize_track configures time selection before rendering.

        set_time_selection must be called before render_mix to ensure the
        correct segment is rendered.
        """
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Test", volume_db=0.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report()

        # We need to track call order manually
        call_order = []

        def _record_set_time(start, end):
            call_order.append("set_time_selection")

        def _record_render(*args, **kwargs):
            call_order.append("render_mix")
            return {"output_path": "/fake/render.wav"}

        mock_render.set_time_selection = MagicMock(side_effect=_record_set_time)
        mock_render.render_mix = MagicMock(side_effect=_record_render)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            norm.normalize_track(0)

        # Assert — set_time_selection must be called before render_mix
        # The third set_time_selection is the restore in the finally block
        assert call_order[0] == "set_time_selection", (
            f"Expected set_time_selection before render_mix, got: {call_order}"
        )
        assert call_order[1] == "render_mix", (
            f"Expected render_mix second, got: {call_order}"
        )

    def test_attenuates_when_already_too_loud(self):
        """normalize_track applies negative gain when measured LUFS > target."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Loud", volume_db=5.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        # measured_lufs=-8, target=-14 => offset=-6
        signal_report = _make_signal_report(integrated_lufs=-8.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(0, target_lufs=-14.0)

        # Assert
        assert result.success is True
        assert result.gain_applied_db == pytest.approx(-6.0, abs=0.01)
        # Volume should be decreased: 5.0 + (-6.0) = -1.0
        mock_tracks.set_volume.assert_called_once_with(0, pytest.approx(-1.0, abs=0.01))

    def test_uses_default_target_lufs(self):
        """normalize_track defaults to -14.0 LUFS when target not specified."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="Default", volume_db=0.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report(integrated_lufs=-14.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            result = norm.normalize_track(0)

        # Assert
        assert result.target_lufs == -14.0

    def test_handles_signal_analysis_error(self):
        """normalize_track returns failed result when SignalAnalyzer.analyze raises."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        track_info = _make_track_info(index=0, name="BadFile", volume_db=0.0)
        mock_tracks.get = MagicMock(return_value=track_info)
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/bad.wav"}
        )

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(
                side_effect=RuntimeError("Cannot read file")
            )

            # Act
            result = norm.normalize_track(0)

        # Assert
        assert result.success is False
        assert result.track_name == "BadFile"
        assert result.gain_applied_db == 0.0


# ══════════════════════════════════════════════════════════════
# normalize_all
# ══════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeAll:
    """Tests for Normalizer.normalize_all()."""

    def test_processes_all_tracks_from_list_all(self):
        """normalize_all calls normalize_track for every track returned by list_all."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()

        t0 = _make_track_info(index=0, name="Kick", volume_db=0.0)
        t1 = _make_track_info(index=1, name="Snare", volume_db=-2.0)
        t2 = _make_track_info(index=2, name="HiHat", volume_db=-4.0)
        mock_tracks.list_all = MagicMock(return_value=[t0, t1, t2])
        mock_tracks.get = MagicMock(side_effect=[t0, t1, t2])
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report(integrated_lufs=-20.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            results = norm.normalize_all(target_lufs=-14.0)

        # Assert
        assert len(results) == 3
        assert results[0].track_index == 0
        assert results[1].track_index == 1
        assert results[2].track_index == 2
        assert all(r.success for r in results)

    def test_returns_empty_list_when_no_tracks(self):
        """normalize_all returns an empty list when list_all is empty."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()
        mock_tracks.list_all = MagicMock(return_value=[])

        # Act
        results = norm.normalize_all()

        # Assert
        assert isinstance(results, list)
        assert len(results) == 0

    def test_passes_custom_parameters_to_normalize_track(self):
        """normalize_all forwards target_lufs and duration to normalize_track."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()

        t0 = _make_track_info(index=0, name="Track", volume_db=0.0)
        mock_tracks.list_all = MagicMock(return_value=[t0])
        mock_tracks.get = MagicMock(return_value=t0)
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report(integrated_lufs=-23.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            results = norm.normalize_all(target_lufs=-23.0, duration=3.0)

        # Assert
        assert len(results) == 1
        assert results[0].target_lufs == -23.0
        mock_render.set_time_selection.assert_any_call(0.0, 3.0)

    def test_continues_on_failed_track(self):
        """normalize_all continues processing after a failed track."""
        # Arrange
        norm, mock_tracks, mock_render = _setup_normalizer_with_mocks()

        t0 = _make_track_info(index=0, name="Good", volume_db=0.0)
        t1 = _make_track_info(index=1, name="Bad", volume_db=0.0)
        mock_tracks.list_all = MagicMock(return_value=[t0, t1])
        # Track 0 succeeds, Track 1 get() returns None
        mock_tracks.get = MagicMock(
            side_effect=[t0, None]
        )
        mock_tracks.set_volume = MagicMock()
        mock_render.set_time_selection = MagicMock()
        mock_render.render_mix = MagicMock(
            return_value={"output_path": "/fake/render.wav"}
        )
        signal_report = _make_signal_report(integrated_lufs=-20.0)

        with patch(
            "hermes_core.normalize.SignalAnalyzer",
            autospec=True,
        ) as mock_analyzer_cls:
            mock_analyzer_cls.analyze = MagicMock(return_value=signal_report)

            # Act
            results = norm.normalize_all(target_lufs=-14.0)

        # Assert
        assert len(results) == 2
        assert results[0].success is True
        assert results[1].success is False
