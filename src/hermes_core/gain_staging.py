"""
GainStagingEngine — 分轨导入、分析、增益校准。
从 MixingEngine 提取的增益分级模块。
"""

import logging
import os

from hermes_core.signal import SignalAnalyzer
from hermes_core.genre_tables import _CLIP_GAIN_REF_DB, _GENRE_VOCAL_TO_BACKING

log = logging.getLogger(__name__)


class GainStagingEngine:
    """增益分级引擎 — 分轨导入、分析、增益校准。"""

    def __init__(self, bridge, track_manager, signal_analyzer):
        self._bridge = bridge
        self._tracks = track_manager
        self._signal = signal_analyzer

    # ── 分轨导入 ───────────────────────────────────────────

    def import_stems(self, file_paths: list[str],
                    position: float = 0.0,
                    output_dir: str | None = None) -> list[dict]:
        """Import audio files, creating one track per file named by basename.

        Returns list of {name, track_index, file_path, success}.
        """
        results = []
        for path in file_paths:
            name = os.path.splitext(os.path.basename(path))[0]
            idx = self._tracks.create(name=name)
            ok = self._tracks.import_media(idx, path, position, output_dir)
            results.append({
                "name": name,
                "track_index": idx,
                "file_path": path,
                "success": ok,
            })
        return results

    # ── 增益控制 ───────────────────────────────────────────

    def apply_gain(self, track_index: int, gain_db: float,
                   target: str = "track_fader"):
        """Apply a gain change to a track.

        target: "track_fader" | "clip_gain" | "master_fader"
        """
        if target == "track_fader":
            self._tracks.set_volume(track_index, gain_db)
        elif target == "clip_gain":
            self._tracks.set_item_volume(track_index, gain_db)
        elif target in ("master_fader",):
            raise NotImplementedError(
                f"Gain target '{target}' not yet implemented"
            )
        else:
            raise ValueError(f"Unknown gain target: {target}")

    # ── 分轨准备 ───────────────────────────────────────────

    def prepare(self, stem_paths: list[str], *,
                genre: str = "pop",
                bpm: float | None = None,
                vocal_indices: list[int] | None = None,
                backing_indices: list[int] | None = None,
                output_dir: str | None = None) -> dict:
        """公共入口：分轨分析 + 录音增益 + 状态转换。

        Returns {stems, genre, vocal_indices, backing_indices}.
        """
        return self._prepare_stems_impl(
            stem_paths, genre=genre, vocal_indices=vocal_indices,
            backing_indices=backing_indices,
            output_dir=output_dir,
        )

    def _prepare_stems_impl(
        self,
        stem_paths: list[str],
        *,
        genre: str = "pop",
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        output_dir: str | None = None,
    ) -> dict:
        # 1. Import stems
        imported = self.import_stems(stem_paths, output_dir=output_dir)

        # 2. Classify roles
        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stem_paths))
                               if i not in vocal_indices]

        # 3. Measure each imported stem and apply clip gain
        stems_out = []
        for i, imp in enumerate(imported):
            if not imp["success"]:
                stems_out.append({
                    "file_path": stem_paths[i],
                    "role": self.classify_role(i, vocal_indices, backing_indices),
                    "track_index": imp["track_index"],
                    "track_name": imp["name"],
                    "raw_rms_db": None,
                    "raw_lufs": None,
                    "raw_peak_db": None,
                    "clip_gain_db": 0.0,
                    "adjusted_lufs": None,
                    "fader_gain_db": 0.0,
                    "success": False,
                })
                continue

            try:
                ana = SignalAnalyzer.analyze(stem_paths[i])
                raw_rms_db = ana.rms_db
                raw_lufs = ana.integrated_lufs
                raw_peak_db = ana.peak_db
            except (OSError, ValueError, RuntimeError):
                raw_rms_db = None
                raw_lufs = None
                raw_peak_db = None

            # Stage 1: clip gain to reference level
            clip_gain_db = 0.0
            if raw_rms_db is not None:
                clip_gain_db = _CLIP_GAIN_REF_DB - raw_rms_db
                # Peak guard — clip gain must not push any sample above 0 dBFS
                if raw_peak_db is not None and clip_gain_db > 0:
                    headroom = -raw_peak_db
                    if clip_gain_db > headroom:
                        log.debug(
                            "Clip gain %.1f dB capped to %.1f dB — "
                            "peak %.1f dBFS leaves no headroom",
                            clip_gain_db, headroom, raw_peak_db,
                        )
                        clip_gain_db = headroom
                self.apply_gain(imp["track_index"], clip_gain_db,
                                target="clip_gain")

            adjusted_lufs = (
                raw_lufs + clip_gain_db if raw_lufs is not None else None
            )

            stems_out.append({
                "file_path": stem_paths[i],
                "role": self.classify_role(i, vocal_indices, backing_indices),
                "track_index": imp["track_index"],
                "track_name": imp["name"],
                "raw_rms_db": raw_rms_db,
                "raw_lufs": raw_lufs,
                "raw_peak_db": raw_peak_db,
                "clip_gain_db": round(clip_gain_db, 1),
                "adjusted_lufs": (
                    round(adjusted_lufs, 1) if adjusted_lufs is not None
                    else None
                ),
                "fader_gain_db": 0.0,
                "success": imp["success"],
            })

        # 4. Fader balancing and peak ceiling are deferred to
        #    post_fx_balance() — after FX chains have been applied.

        return {
            "stems": stems_out,
            "genre": genre,
            "vocal_indices": vocal_indices,
            "backing_indices": backing_indices,
        }

    # ── 分轨分类 ───────────────────────────────────────────

    @staticmethod
    def classify_role(idx: int, vocal_indices: list[int],
                      backing_indices: list[int]) -> str:
        """Classify a stem index as 'vocal', 'backing', or 'other'."""
        if idx in vocal_indices:
            return "vocal"
        if idx in backing_indices:
            return "backing"
        return "other"

    # ── 推子平衡 ───────────────────────────────────────────

    def _balance_faders(
        self,
        stems: list[dict],
        *,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        genre: str = "pop",
    ) -> dict:
        """Set fader gains so backing sits *ratio* LU below vocal.

        Vocal fader stays at 0 (reference).  Backing is attenuated to
        achieve the genre-appropriate vocal/backing ratio.
        """
        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stems))
                               if i not in vocal_indices]

        ratio = _GENRE_VOCAL_TO_BACKING.get(genre, 3)

        vocal_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems)
            if i in vocal_indices and s.get("adjusted_lufs") is not None
        ]
        backing_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems)
            if i in backing_indices and s.get("adjusted_lufs") is not None
        ]
        vocal_lufs = (
            sum(vocal_lufs_vals) / len(vocal_lufs_vals)
            if vocal_lufs_vals else -20.0
        )
        backing_lufs = (
            sum(backing_lufs_vals) / len(backing_lufs_vals)
            if backing_lufs_vals else -20.0
        )

        backing_target = vocal_lufs - ratio

        for i, s in enumerate(stems):
            if not s.get("success") or s.get("adjusted_lufs") is None:
                continue
            if i in vocal_indices:
                fader_gain_db = 0.0  # reference — don't move
            elif i in backing_indices:
                fader_gain_db = backing_target - s["adjusted_lufs"]
            else:
                continue
            s["fader_gain_db"] = round(fader_gain_db, 1)
            self.apply_gain(s["track_index"], fader_gain_db)

        return {
            "ratio_lu": ratio,
            "vocal_lufs": round(vocal_lufs, 1),
            "backing_lufs": round(backing_lufs, 1),
            "backing_target_lufs": round(backing_target, 1),
        }
