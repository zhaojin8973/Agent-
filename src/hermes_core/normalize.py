"""
Normalizer — track loudness normalization via render + analyze + gain adjust.
Soloes the target track before rendering so the measurement reflects only
that track, then restores project state (solo, time selection) afterward.
"""

import logging
import tempfile
from dataclasses import dataclass

from hermes_core.bridge import ReaperBridge
from hermes_core.track import TrackManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class NormalizeResult:
    """Result of normalizing a single track to a target LUFS level."""

    track_index: int
    track_name: str
    original_lufs: float
    target_lufs: float
    gain_applied_db: float
    success: bool


class Normalizer:
    """Per-track LUFS normalization via render-analysis-gain cycle.

    Soloes the target track, renders a time-selection snippet, measures its
    integrated LUFS, computes the gain offset needed to hit the target, and
    applies that offset to the track fader.  Project state (solo flags and
    time selection) is restored after measurement.

    Usage:
        bridge = ReaperBridge()
        bridge.connect()
        normalizer = Normalizer(bridge)
        result = normalizer.normalize_track(0, target_lufs=-14.0)
        all_results = normalizer.normalize_all(target_lufs=-16.0)
    """

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge
        self._tracks = TrackManager(bridge)
        self._render = RenderManager(bridge)

    # ── Core pipeline ────────────────────────────────────────

    def normalize_track(
        self,
        track_index: int,
        target_lufs: float = -14.0,
        duration: float = 5.0,
    ) -> NormalizeResult:
        """Render and normalize a single track to the target LUFS level.

        Soloes *only* the target track during measurement so the rendered
        snippet reflects that track alone.  Restores solo state and time
        selection afterward.

        Args:
            track_index: Zero-based track index in the project.
            target_lufs: Desired integrated LUFS level (e.g. -14.0 LUFS).
            duration: Length of the audio snippet to render and analyze, in
                seconds.

        Returns:
            NormalizeResult with measured LUFS, computed gain offset, and
            success flag.
        """
        # 1. Get TrackInfo — bail on missing track
        info = self._tracks.get(track_index)
        if info is None:
            return NormalizeResult(
                track_index=track_index,
                track_name="",
                original_lufs=0.0,
                target_lufs=target_lufs,
                gain_applied_db=0.0,
                success=False,
            )

        # 2. Save project state so we can restore after measurement
        solo_backup = self._backup_solo_state()
        saved_start, saved_end = self._render.get_time_selection_range()

        try:
            # 3. Solo only the target track
            self._solo_only(track_index)

            # 4. Render a time-selection snippet
            with tempfile.TemporaryDirectory() as tmpdir:
                self._render.set_time_selection(0.0, duration)
                render_result = self._render.render_mix(
                    tmpdir, bounds="time_selection", timeout=30.0
                )

                output_path = render_result.get("output_path")
                if output_path is None:
                    return NormalizeResult(
                        track_index=track_index,
                        track_name=info.name,
                        original_lufs=0.0,
                        target_lufs=target_lufs,
                        gain_applied_db=0.0,
                        success=False,
                    )

                # 5. Analyze loudness
                report = SignalAnalyzer.analyze(output_path)
                measured_lufs = report.integrated_lufs

            # 6. Compute gain offset and apply to track fader
            offset_db = target_lufs - measured_lufs
            self._tracks.set_volume(track_index, info.volume_db + offset_db)

            return NormalizeResult(
                track_index=track_index,
                track_name=info.name,
                original_lufs=measured_lufs,
                target_lufs=target_lufs,
                gain_applied_db=offset_db,
                success=True,
            )

        except (OSError, ValueError, RuntimeError) as e:
            log.exception(
                "normalize_track(%d, %s) failed: %s",
                track_index,
                info.name,
                e,
            )
            return NormalizeResult(
                track_index=track_index,
                track_name=info.name,
                original_lufs=0.0,
                target_lufs=target_lufs,
                gain_applied_db=0.0,
                success=False,
            )
        finally:
            # 7. Restore project state
            self._restore_solo_state(solo_backup)
            self._render.set_time_selection(saved_start, saved_end)

    # ── Batch pipeline ───────────────────────────────────────

    def normalize_all(
        self,
        target_lufs: float = -14.0,
        duration: float = 5.0,
    ) -> list[NormalizeResult]:
        """Normalize every track in the project.

        Args:
            target_lufs: Desired integrated LUFS level.
            duration: Length of audio snippet per track, in seconds.

        Returns:
            List of NormalizeResult, one per track (including failures).
        """
        results: list[NormalizeResult] = []
        for track in self._tracks.list_all():
            result = self.normalize_track(
                track.index, target_lufs=target_lufs, duration=duration
            )
            results.append(result)
        return results

    # ── Solo state helpers ───────────────────────────────────

    def _backup_solo_state(self) -> dict[int, bool]:
        """Return {track_index: solo_flag} for every track in the project."""
        backup: dict[int, bool] = {}
        for track in self._tracks.list_all():
            backup[track.index] = track.solo
        return backup

    def _solo_only(self, track_index: int):
        """Solo *only* the given track; unsolo all others."""
        for track in self._tracks.list_all():
            self._tracks.set_solo(track.index, track.index == track_index)

    def _restore_solo_state(self, backup: dict[int, bool]):
        """Restore solo flags from a backup dict."""
        for idx, was_solo in backup.items():
            self._tracks.set_solo(idx, was_solo)
