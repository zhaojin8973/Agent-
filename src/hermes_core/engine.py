"""
MixingEngine — Layer 3 public API. Composes all Layer 2 modules into
a single entry point for Hermes acceptance scenarios.
"""

import logging
import os
from datetime import datetime

from hermes_core.bridge import ReaperBridge
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer

log = logging.getLogger(__name__)

# Genre-based backing track reduction (LU) for prepare_stems.
# Higher values = backing is more heavily compressed/limited → needs
# more reduction to create headroom for the lead vocal.
_GENRE_BACKING_REDUCTION = {
    "folk":                    (3, 6),    # folk / ballad — wide dynamics
    "pop":                     (6, 9),    # pop — moderate compression
    "chinese_folk_bel_canto":  (9, 12),   # Chinese folk / bel canto — vocal-forward
}

# Standard clip gain reference level (dBFS RMS).
# -18 dBFS = 0 VU — industry standard for analog-modelled plugin input calibration.
_CLIP_GAIN_REF_DB: float = -18.0

# Default master output RMS target (dBFS).
# Tune after listening — pop/rock often sits at -12..-10, classical at -18..-14.
_DEFAULT_TARGET_RMS_DB: float = -12.0

# Pro-L 2 calibrated VST parameter ranges (verified 2026-05-28 via REAPER GUI).
# Gain: normalized 0.0 = 0 dB, 1.0 = +30 dB (boost only).
# Output Level: normalized 0.0 = -30 dB, 1.0 = 0 dB.
# Both share a 30 dB span.
_PRO_L2_RANGE_DB: float = 30.0


def _master_error(target_rms_db: float, ceiling_db: float, error: str) -> dict:
    """Build a finalize_master error result dict."""
    return {
        "target_rms_db": target_rms_db,
        "achieved_rms_db": None,
        "gain_db": 0.0,
        "ceiling_db": ceiling_db,
        "passed": False,
        "error": error,
        "output_path": None,
        "pre_limiter_peak_db": None,
    }


class MixingEngine:
    """Top-level REAPER mixing engine. Use as context manager for auto-connect.

    with MixingEngine() as eng:
        eng.create_project(sample_rate=48000)
        eng.import_stems(["/path/to/audio.wav"])
        result = eng.render_mix("/tmp/output")
    """

    def __init__(self, watchdog: bool = False):
        self._bridge = ReaperBridge(dialog_killer=watchdog)
        self._tracks = TrackManager(self._bridge)
        self._bus = BusManager(self._bridge)
        self._fx = FxManager(self._bridge)
        self._send = SendManager(self._bridge)
        self._render = RenderManager(self._bridge)
        self._watchdog_enabled = watchdog
        self._project_path: str | None = None

    # ── Context manager ──────────────────────────────────

    def __enter__(self):
        if not self._bridge.connect():
            raise ConnectionError("Failed to connect to REAPER bridge")
        return self

    def __exit__(self, *args):
        if self._watchdog_enabled and self._bridge._dialog_killer.is_running:
            self._bridge._dialog_killer.stop()
        return False

    # ── Scene 1: Connection & health ─────────────────────

    def health_check(self) -> dict:
        """Return health status of the REAPER connection."""
        result = self._bridge.health_check()
        result["watchdog_enabled"] = self._watchdog_enabled
        result["recent_dialog_events"] = [
            {
                "window_title": e.window_title,
                "action_taken": e.action_taken,
                "timestamp": e.timestamp,
            }
            for e in self._bridge._dialog_killer.get_recent_events()[-20:]
        ]
        return result

    # ── Scene 2: Project & tracks ────────────────────────

    def _safe_project_path(self, output_dir: str, name: str) -> tuple[str, bool]:
        """Return (path, conflict_renamed) for ``{output_dir}/{name}.rpp``.

        If the target already exists a timestamp suffix is appended to avoid
        overwriting a previous project.
        """
        os.makedirs(output_dir, exist_ok=True)
        target = os.path.join(output_dir, f"{name}.rpp")
        if not os.path.exists(target):
            return target, False
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        alt = os.path.join(output_dir, f"{name}_{ts}.rpp")
        log.info("Project file exists — renamed to %s", alt)
        return alt, True

    def create_project(self, name: str, output_dir: str,
                       sample_rate: int = 48000) -> dict:
        """Create a named project and save it to *output_dir* without dialogs.

        Returns ``{name, path, sample_rate, track_count, conflict_renamed}``.
        """
        safe_path, conflict_renamed = self._safe_project_path(output_dir, name)

        api = self._bridge.api

        # Delete all tracks via reapy's high-level API.
        # The raw DeleteTrack API is unreliable on ARM64.
        proj = self._bridge.rpr.Project()
        for track in list(proj.tracks):
            try:
                track.delete()
            except Exception:
                pass

        # Reset master track
        master = api.GetMasterTrack(0)
        if master:
            n_fx = api.TrackFX_GetCount(master)
            for i in range(n_fx - 1, -1, -1):
                api.TrackFX_Delete(master, i)
            api.SetMediaTrackInfo_Value(master, "D_VOL", 1.0)
            api.SetMediaTrackInfo_Value(master, "B_MUTE", 0.0)
            api.SetMediaTrackInfo_Value(master, "I_SOLO", 0.0)
            api.SetMediaTrackInfo_Value(master, "D_PAN", 0.0)

        api.GetSetProjectInfo_String(0, "PROJECT_NAME", name, True)
        if sample_rate > 0:
            api.GetSetProjectInfo(0, "PROJECT_SRATE", sample_rate, True)
            api.GetSetProjectInfo(0, "PROJECT_SRATE_USE", 1, True)
        import base64
        api.GetSetProjectInfo_String(
            0, "RENDER_FORMAT",
            base64.b64encode(b"evaw\x18\x00\x01").decode(), True,
        )

        api.Main_SaveProjectEx(0, safe_path, 0)
        self._project_path = safe_path

        return {
            "name": name,
            "path": safe_path,
            "sample_rate": sample_rate,
            "track_count": 0,
            "conflict_renamed": conflict_renamed,
        }

    def save_project(self) -> dict:
        """Silently save to the current project path via ``Main_SaveProjectEx``.

        Raises ``RuntimeError`` when no project path has been established
        (i.e. ``create_project`` was never called).
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        self._bridge.api.Main_SaveProjectEx(0, self._project_path, 0)
        return {"path": self._project_path, "saved_at": datetime.now().isoformat()}

    def save_checkpoint(self, label: str = "") -> dict:
        """Save a timestamped copy without touching the main project file.

        Use before risky operations (adding FX, destructive edits) so you
        can always return to a known-good state.
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        base = os.path.splitext(self._project_path)[0]
        ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
        suffix = f"_{label}_{ts}" if label else f"_{ts}"
        checkpoint_path = f"{base}_checkpoint{suffix}.rpp"

        self._bridge.api.Main_SaveProjectEx(0, checkpoint_path, 0)
        return {"checkpoint_path": checkpoint_path, "main_path": self._project_path}

    def get_project_info(self) -> dict:
        """Return current project metadata.

        ``{name, path, sample_rate, track_count}``.
        """
        api = self._bridge.api
        _, name_buf, _ = api.GetProjectName(0, "", 256)
        path_buf, _ = api.GetProjectPath("", 256)
        sr = api.GetSetProjectInfo(0, "PROJECT_SRATE", 0, False)
        n_tracks = api.CountTracks(0)

        return {
            "name": (name_buf or ""),
            "path": (path_buf or ""),
            "sample_rate": int(sr) if sr else 0,
            "track_count": n_tracks,
        }

    def import_stems(self, file_paths: list[str],
                    position: float = 0.0) -> list[dict]:
        """Import audio files, creating one track per file named by basename.

        Returns list of {name, track_index, file_path, success}.
        """
        results = []
        for path in file_paths:
            name = os.path.splitext(os.path.basename(path))[0]
            idx = self._tracks.create(name=name)
            ok = self._tracks.import_media(idx, path, position)
            results.append({
                "name": name,
                "track_index": idx,
                "file_path": path,
                "success": ok,
            })
        return results

    def list_tracks(self) -> list[TrackInfo]:
        """Return TrackInfo for all tracks in the project."""
        return self._tracks.list_all()

    # ── Scene 3: Gain staging ────────────────────────────

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

    def get_gain_structure(self) -> dict:
        """Return gain overview for all tracks."""
        tracks = []
        for t in self._tracks.list_all():
            tracks.append({
                "index": t.index,
                "name": t.name,
                "volume_db": t.volume_db,
                "pan": t.pan,
                "mute": t.mute,
            })
        return {"tracks": tracks}

    def prepare_stems(
        self,
        stem_paths: list[str],
        *,
        genre: str = "pop",
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        vocal_to_backing_lu: float = 4.0,
        backing_reduction_lu: float | None = None,
    ) -> dict:
        """Analyse raw stems, apply clip gain to reference level, then
        balance vocal vs. backing via fader.

        Two-stage gain staging:
        1. Clip gain — brings every stem to -18 dBFS RMS (0 VU reference).
           Ensures plugins see consistent input levels across projects.
        2. Fader — genre-based vocal/backing balance, keeps vocal fader
           near unity for optimal resolution.

        When *backing_reduction_lu* is given it bypasses the genre table
        and uses that exact LU value. Useful for tuning without editing
        ``_GENRE_BACKING_REDUCTION``.
        """
        # 1. Import stems
        imported = self.import_stems(stem_paths)

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
                    "path": stem_paths[i],
                    "role": self._classify_role(i, vocal_indices, backing_indices),
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
                        log.info(
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
                "path": stem_paths[i],
                "role": self._classify_role(i, vocal_indices, backing_indices),
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

        # 4. Genre-based fader balance using adjusted LUFS
        if backing_reduction_lu is not None:
            reduction = backing_reduction_lu
        else:
            reduction = _GENRE_BACKING_REDUCTION.get(
                genre, _GENRE_BACKING_REDUCTION["pop"]
            )
            if isinstance(reduction, tuple):
                reduction = (reduction[0] + reduction[1]) / 2.0

        backing_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems_out)
            if i in backing_indices and s["adjusted_lufs"] is not None
        ]
        backing_adjusted_lufs = (
            sum(backing_lufs_vals) / len(backing_lufs_vals)
            if backing_lufs_vals else -18.0
        )
        backing_target_lufs = backing_adjusted_lufs - reduction
        vocal_target_lufs = backing_target_lufs + vocal_to_backing_lu

        for i, s in enumerate(stems_out):
            if not s["success"] or s["adjusted_lufs"] is None:
                continue
            if i in vocal_indices:
                target_lufs = vocal_target_lufs
            elif i in backing_indices:
                target_lufs = backing_target_lufs
            else:
                continue
            fader_gain_db = target_lufs - s["adjusted_lufs"]
            s["fader_gain_db"] = round(fader_gain_db, 1)
            self.apply_gain(s["track_index"], fader_gain_db)

        return {
            "stems": stems_out,
            "genre": genre,
            "genre_reduction_lu": reduction,
            "backing_adjusted_lufs": round(backing_adjusted_lufs, 1),
            "backing_target_lufs": round(backing_target_lufs, 1),
            "vocal_target_lufs": round(vocal_target_lufs, 1),
            "vocal_to_backing_lu": vocal_to_backing_lu,
        }

    @staticmethod
    def _classify_role(idx: int, vocal_indices: list[int],
                       backing_indices: list[int]) -> str:
        if idx in vocal_indices:
            return "vocal"
        if idx in backing_indices:
            return "backing"
        return "other"

    def check_headroom(self) -> dict:
        """Check headroom. Without rendering, reports source as unavailable."""
        return {
            "headroom_dbtp": None,
            "source": "unavailable_without_render",
            "message": "Render the project first to measure headroom",
        }

    # ── Scene 4: FX ──────────────────────────────────────

    def add_fx(self, track_index: int, fx_name: str) -> int:
        """Add an effect plugin to a track. Returns FX index."""
        return self._fx.add(track_index, fx_name)

    def get_fx_chain(self, track_index: int) -> list[dict]:
        """Return all FX on a track."""
        return self._fx.get_chain(track_index)

    def add_master_fx(self, fx_name: str) -> int:
        """Add an effect plugin to the master track. Returns FX index."""
        return self._fx.add_master(fx_name)

    # ── Scene 5: Bus & sends ─────────────────────────────

    def create_bus(self, name: str, child_tracks: list[int]) -> int:
        """Create a folder bus containing the given child tracks."""
        return self._bus.create_bus(name, child_tracks)

    def create_reverb_send(self, src_track: int,
                          level_db: float = -8.0,
                          reverb_fx: str = "ReaVerbate",
                          mode: str = "post-fader") -> dict:
        """Create a reverb aux return and send from src_track to it.

        Returns {aux_index, send, fx_index}.
        """
        aux_idx = self._tracks.create(name="Verb Return")

        fx_idx = self._fx.add(aux_idx, reverb_fx)

        send_info = self._send.create(
            src=src_track, dest=aux_idx, level_db=level_db, mode=mode
        )

        return {"aux_index": aux_idx, "send": send_info, "fx_index": fx_idx}

    # ── Scene 6: Render ──────────────────────────────────

    def render_mix(self, output_dir: str,
                   bounds: str = "entire_project",
                   fmt: str = "wav",
                   sample_rate: int = 0,
                   verify: bool = True,
                   timeout: float = 120.0) -> dict:
        """Render project and optionally run signal analysis.

        Returns {output_path, signal_check, ...}.
        """
        result = self._render.render_mix(
            output_dir=output_dir,
            bounds=bounds,
            fmt=fmt,
            sample_rate=sample_rate,
            timeout=timeout,
        )

        if verify and result.get("output_path"):
            try:
                report = SignalAnalyzer.analyze(result["output_path"])
                result["signal_check"] = {
                    "integrated_lufs": report.integrated_lufs,
                    "true_peak_dbtp": report.true_peak_dbtp,
                    "clip_count": report.clip_count,
                    "clip_passed": report.clip_passed,
                    "silence_passed": report.silence_passed,
                    "rms_db": report.rms_db,
                    "peak_db": report.peak_db,
                    "duration_sec": report.duration_sec,
                }
            except (OSError, ValueError, RuntimeError) as e:
                result["signal_check"] = {"error": str(e)}

        return result

    # ── Scene 7: Safety audit ────────────────────────────

    def audit_mix(self, file_path: str) -> dict:
        """Run a full safety audit on a rendered mix file.

        Returns {passed, checks: [{check_name, severity, message}, ...], diagnostics}.
        """
        try:
            report = SignalAnalyzer.analyze(file_path)
        except (OSError, ValueError, RuntimeError) as e:
            return {"passed": False, "error": str(e)}

        checks = []

        if not report.silence_passed:
            checks.append({
                "check_name": "silence",
                "severity": "critical",
                "message": f"Mix is silent (RMS={report.rms_db} dB)",
            })

        if not report.clip_passed:
            checks.append({
                "check_name": "clipping",
                "severity": "critical",
                "message": f"Mix has {report.clip_count} clipped samples",
            })

        if report.true_peak_dbtp > 0.0:
            checks.append({
                "check_name": "true_peak",
                "severity": "warning",
                "message": (
                    f"True peak {report.true_peak_dbtp} dBTP exceeds 0 dBTP"
                ),
            })
        elif report.true_peak_dbtp > -1.0:
            checks.append({
                "check_name": "true_peak",
                "severity": "info",
                "message": (
                    f"True peak {report.true_peak_dbtp} dBTP "
                    "(within 1 dB of ceiling)"
                ),
            })

        criticals = [c for c in checks if c["severity"] == "critical"]
        passed = len(criticals) == 0

        return {
            "passed": passed,
            "checks": checks or [
                {"check_name": "all_clear", "severity": "info",
                 "message": "No issues detected"}
            ],
            "diagnostics": {
                "integrated_lufs": report.integrated_lufs,
                "true_peak_dbtp": report.true_peak_dbtp,
                "rms_db": report.rms_db,
                "peak_db": report.peak_db,
                "clip_count": report.clip_count,
                "duration_sec": report.duration_sec,
                "sample_rate": report.sample_rate,
            },
        }

    # ── Scene 8: Master finalization ───────────────────────

    def finalize_master(
        self,
        target_rms_db: float = _DEFAULT_TARGET_RMS_DB,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tmp_dir: str | None = None,
    ) -> dict:
        """Two-pass master finalization via limiter gain staging.

        1. Add *limiter_fx* to master with Gain=0, Output Level=*ceiling_db*.
        2. Probe render → measure RMS (dB).
        3. Gain = target_rms_db - measured_rms_db (dB → dB, linear).
        4. Apply gain, render final.
        """
        import tempfile

        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_master_")
        probe_dir = os.path.join(tmp, "probe")
        final_dir = os.path.join(tmp, "final")

        # 1. Add limiter
        fx_idx = self._fx.add_master(limiter_fx)
        if fx_idx < 0:
            return _master_error(
                target_rms_db, ceiling_db,
                f"Failed to add {limiter_fx} to master",
            )

        # Pro-L 2 param formulas (verified 2026-05-28 via REAPER calibration):
        #   Gain: 0..+30 dB → normalized = gain_db / 30
        #   Output Level: -30..0 dB → normalized = (ceiling_db + 30) / 30
        ceiling_norm = max(0.0, min(1.0, (ceiling_db + _PRO_L2_RANGE_DB) / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Output Level", ceiling_norm):
            return _master_error(
                target_rms_db, ceiling_db,
                "Pro-L 2 Output Level param not found — may need calibration",
            )
        if not self._fx.set_param(-1, fx_idx, "Gain", 0.0):
            return _master_error(
                target_rms_db, ceiling_db,
                "Pro-L 2 Gain param not found — may need calibration",
            )

        # 2. Probe render
        probe_result = self.render_mix(probe_dir, verify=True)
        probe_sc = probe_result.get("signal_check", {})
        if probe_result.get("output_path") is None:
            return _master_error(
                target_rms_db, ceiling_db, "Probe render failed",
            )

        measured_rms_db = probe_sc.get("rms_db")
        pre_peak = probe_sc.get("peak_db", 0.0)
        if measured_rms_db is None:
            return _master_error(
                target_rms_db, ceiling_db, "Failed to measure RMS from probe",
            )

        # 3. Calculate gain — dB → dB, linear
        gain_db = target_rms_db - measured_rms_db

        # 4. Apply gain and render final.
        # Gain range is 0..+30 dB: normalized = gain_db / 30.
        # If gain_db < 0 (mix too hot), clamp to 0 — Gain cannot attenuate.
        gain_norm = max(0.0, min(1.0, gain_db / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Gain", gain_norm):
            return _master_error(
                target_rms_db, ceiling_db,
                "Pro-L 2 Gain param not found during final render",
            )
        final_result = self.render_mix(final_dir, verify=True)
        output_path = final_result.get("output_path")
        final_sc = final_result.get("signal_check", {})
        achieved_rms_db = final_sc.get("rms_db")

        passed = (
            output_path is not None
            and achieved_rms_db is not None
            and abs(achieved_rms_db - target_rms_db) <= 2.0
        )

        return {
            "target_rms_db": target_rms_db,
            "achieved_rms_db": achieved_rms_db,
            "gain_db": round(gain_db, 1),
            "ceiling_db": ceiling_db,
            "passed": passed,
            "pre_limiter_peak_db": pre_peak,
            "output_path": output_path,
        }
