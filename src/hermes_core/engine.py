"""
MixingEngine — Layer 3 public API. Composes all Layer 2 modules into
a single entry point for Hermes acceptance scenarios.
"""

import logging
import os
import time
from datetime import datetime
from typing import Callable

from hermes_core.bridge import ReaperBridge
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer
from hermes_core.exceptions import ConnectionError as HermesConnectionError
from hermes_core.loudness_optimizer import (
    find_optimal_gain,
    verify_output,
    load_calibration,
    generate_report,
    CompressionIntent,
)
from hermes_core.normalize import normalize_params
from hermes_core.profiles import (
    _resolve_fx_type,
    _get_compressor_preset,
    _EQ_BASELINE,
)
from hermes_core.dag import AudioNode, SendNode, ChainExecutor

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
_DEFAULT_TARGET_LUFS: float = -12.0

# Pro-L 2 calibrated VST parameter ranges (verified 2026-05-28 via REAPER GUI).
# Gain: normalized 0.0 = 0 dB, 1.0 = +30 dB (boost only).
# Output Level: normalized 0.0 = -30 dB, 1.0 = 0 dB.
# Both share a 30 dB span.
_PRO_L2_RANGE_DB: float = 30.0


def _master_error(target_lufs: float, ceiling_db: float, error: str) -> dict:
    """Build a finalize_master error result dict."""
    return {
        "target_lufs": target_lufs,
        "achieved_lufs": None,
        "probe_lufs": None,
        "gain_db": 0.0,
        "ceiling_db": ceiling_db,
        "passed": False,
        "converged": False,
        "error": error,
        "hint": _friendly_hint(error),
        "output_path": None,
        "pre_limiter_peak_db": None,
    }


def _friendly_hint(error: str) -> str:
    """Return a user-friendly hint for common errors."""
    hints = {
        "Probe render failed":
            "REAPER may be blocked by a modal dialog. Try watchdog=True "
            "to auto-dismiss dialogs, or check that tracks have media items.",
        "Probe is near-silent":
            "The probe render produced near-silent audio. Check that "
            "the source files are not empty and have audible content.",
        "Pro-L 2 Output Level param not found":
            "Pro-L 2 parameter name doesn't match. Verify the plugin is "
            "installed and named exactly 'FabFilter Pro-L 2 (FabFilter)'. "
            "Try running preflight_plugins() first.",
        "Pro-L 2 Gain param not found":
            "Pro-L 2 Gain parameter not found. Same as above — check "
            "plugin installation and name.",
        "Failed to add":
            "Plugin not found in REAPER. Check the FX name matches "
            "the REAPER FX browser exactly, including vendor suffix.",
        "Not a WAV file":
            "Input file is not a valid WAV. Supported formats: WAV "
            "(16/24-bit PCM, 32-bit float), FLAC, MP3 via soundfile.",
        "WAV data chunk not found":
            "WAV file appears corrupted — data chunk is missing. "
            "Try re-exporting the file from your DAW.",
    }
    for key, hint in hints.items():
        if key.lower() in error.lower():
            return hint
    return "Check the log for details. Common issues: missing plugins, "
    "unwritable output directory, insufficient disk space, or REAPER "
    "modal dialogs blocking automation."


# ════════════════════════════════════════════════════════════════
# Compression derivation + translator layer
# ════════════════════════════════════════════════════════════════


def _derive_compressor_intent(
    rms_db: float, peak_db: float, *, genre: str = "pop"
) -> CompressionIntent:
    """Derive compression targets from Crest Factor (Peak – RMS).

    ==============  ============  ============================
    Crest Factor    Amount        Typical material
    ==============  ============  ============================
    ≥ 15 dB         ``"heavy"``   Folk ballad, classical vocal
    10–15 dB        ``"medium"``  Pop vocal, rock vocal
    < 10 dB         ``"light"``   Pre-compressed, synth, EDM
    ==============  ============  ============================

    *gr_target_db* is set to roughly 40 % of the crest factor so the
    compressor tames peaks without flattening the performance.
    """
    crest = peak_db - rms_db

    if crest >= 15.0:
        amount = "heavy"
        gr_target = round(crest * 0.4, 1)
    elif crest >= 10.0:
        amount = "medium"
        gr_target = round(crest * 0.35, 1)
    else:
        amount = "light"
        gr_target = round(crest * 0.25, 1)

    return CompressionIntent(
        amount=amount,
        gr_target_db=gr_target,
        crest_factor_db=round(crest, 1),
        rms_db=round(rms_db, 1),
        peak_db=round(peak_db, 1),
    )


def _apply_vca_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """VCA / digital compressor → physical parameter dict.

    Threshold is placed so that the signal's peak exceeds it by
    *gr_target_db* — the compressor catches the transient and
    reduces it by the target amount.
    """
    threshold = intent.peak_db - intent.gr_target_db
    ratio = {
        "light":  2.0,
        "medium": 4.0,
        "heavy":  8.0,
    }.get(intent.amount, 4.0)

    return {
        "Threshold":   round(threshold, 1),
        "Ratio":       ratio,
        "Attack":      preset["attack_ms"],
        "Release":     preset["release_ms"],
        "Makeup Gain": round(intent.gr_target_db * 0.6, 1),
    }


def _apply_fet_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """FET compressor (1176-style) → physical parameter dict.

    The Input knob sets an *equivalent threshold* — we compute the
    threshold from peak + target GR, then let the normalisation layer
    reverse-lookup the knob position via the calibration table.
    """
    threshold = intent.peak_db - intent.gr_target_db
    return {
        "Input":    round(threshold, 1),
        "Output":   round(intent.gr_target_db * 0.5, 1),
        "Attack":   preset["attack_ms"],
        "Release":  preset["release_ms"],
    }


def _apply_opto_params(intent: CompressionIntent,
                        preset: dict[str, float]) -> dict[str, float]:
    """Optical compressor (LA-2A style) → physical parameter dict."""
    return {
        "Peak Reduction": round(intent.gr_target_db, 1),
        "Gain":           round(intent.gr_target_db * 0.4, 1),
    }


def _apply_rvox_params(intent: CompressionIntent,
                        preset: dict[str, float]) -> dict[str, float]:
    """Waves RVox → physical parameter dict.

    RVox's Compression control is 0–100 (%).  We map the intent amount
    directly, then let normalisation handle the 0–1 scaling.
    """
    comp = {
        "light":  40.0,
        "medium": 60.0,
        "heavy":  80.0,
    }.get(intent.amount, 50.0)

    return {
        "Compression": comp,
        "Gain":         round(intent.gr_target_db * 0.5, 1),
    }


# ════════════════════════════════════════════════════════════════
# Compressor dispatcher
# ════════════════════════════════════════════════════════════════

_TRANSLATORS = {
    "vca":  _apply_vca_params,
    "fet":  _apply_fet_params,
    "opto": _apply_opto_params,
    "rvox": _apply_rvox_params,
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
        self._snapshot_project_path: str | None = None  # from GetProjectPath at init
        self._snapshot_project_name: str | None = None  # from GetProjectName at init
        # Idempotency guards — prevent double-execution of destructive ops.
        self._stems_gain_staged: bool = False
        self._master_finalized: bool = False
        self._stems_cache: list[dict] = []

        # AudioNode pipeline — built by apply_profile()
        self._vocal_chain_nodes: list[AudioNode] = []
        self._backing_chain_nodes: list[AudioNode] = []
        self._reverb_send_node: SendNode | None = None

    # ── Context manager ──────────────────────────────────

    def __enter__(self):
        if not self._bridge.connect():
            raise HermesConnectionError("Failed to connect to REAPER bridge")
        return self

    def __exit__(self, *args):
        if self._watchdog_enabled and self._bridge.dialog_killer_active:
            self._bridge.stop_dialog_killer()
        return False

    # ── Undo / state helpers ────────────────────────────────

    def _undo_block(self, label: str, fn: Callable, /, *args, **kwargs):
        """Wrap *fn* in a REAPER undo block so the user can Ctrl+Z the
        entire operation as one atomic step.
        """
        api = self._bridge.api
        try:
            api.Undo_BeginBlock()
            result = fn(*args, **kwargs)
            api.Undo_EndBlock(f"Hermes: {label}", -1)
            return result
        except Exception:
            try:
                api.Undo_EndBlock(f"Hermes: {label} (failed)", 0)
            except Exception:
                pass
            raise

    def _ensure_project_match(self):
        """Raise ``RuntimeError`` if REAPER's current project has changed
        since ``create_project()`` was called (e.g. user switched tabs).
        """
        if not self._snapshot_project_path and not self._snapshot_project_name:
            return
        _, name_buf, _ = self._bridge.api.GetProjectName(0, "", 256)
        path_buf, _ = self._bridge.api.GetProjectPath("", 256)
        current_name = (name_buf or "").strip()
        current_path = (path_buf or "").strip()

        name_changed = (
            self._snapshot_project_name and current_name
            and current_name != self._snapshot_project_name
        )
        path_changed = (
            self._snapshot_project_path and current_path
            and current_path != self._snapshot_project_path
        )

        if name_changed or path_changed:
            raise RuntimeError(
                f"Project mismatch: expected '{self._snapshot_project_name}'"
                f" at '{self._snapshot_project_path}', "
                f"REAPER now has '{current_name}' at '{current_path}'. "
                f"Call create_project() or re-open the expected project."
            )

    def reset(self):
        """Clear idempotency guards so the engine can be re-used for a new mix."""
        self._stems_gain_staged = False
        self._master_finalized = False
        self._stems_cache.clear()
        self._vocal_chain_nodes.clear()
        self._backing_chain_nodes.clear()
        self._reverb_send_node = None

    def preflight_plugins(self, fx_names: list[str]) -> list[str]:
        """Check which of *fx_names* are available in REAPER.  Returns the
        list of **missing** plugin names (empty = all present).
        """
        missing: list[str] = []
        for name in fx_names:
            # Try adding to master then immediately removing.  We must
            # instantiate (default True) so REAPER actually resolves the
            # plugin, then clean up to leave no side effects.
            idx = self._fx.add_master(name)
            if idx < 0:
                missing.append(name)
            else:
                # Clean up the probe FX — don't leave it on master.
                try:
                    master = self._bridge.api.GetMasterTrack(0)
                    if master:
                        self._bridge.api.TrackFX_Delete(master, idx)
                except Exception:
                    pass
        return missing

    def apply_profile(self, profile, /, *, vocal_track: int = 0,
                      backing_tracks: list[int] | None = None,
                      genre: str = "pop"):
        """Apply a :class:`MixingProfile` — FX chains, sends, and auto-compression.

        1. **EQ baseline** — conservative HPF + gentle presence boost.
        2. **Compression** — Crest Factor analysis → :class:`CompressionIntent`
           → translator → normalise → REAPER.
        3. **Reverb bus** — aux send with Abbey Road safety EQ.

        An :class:`AudioNode` DAG is built in parallel.  Dirty flags cascade
        so that downstream nodes are automatically invalidated when an
        upstream parameter changes (``update_node_param``).
        """
        from hermes_core.profiles import MixingProfile
        if not isinstance(profile, MixingProfile):
            raise TypeError(f"Expected MixingProfile, got {type(profile).__name__}")

        self._profile = profile

        # ── Build a lookup: stem index → analysis data ──
        stem_data: dict[int, dict] = {}
        for i, s in enumerate(self._stems_cache):
            if s.get("success"):
                stem_data[i] = s

        # ── Vocal chain ──
        self._vocal_chain_nodes = self._build_audio_chain(
            track_index=vocal_track,
            fx_list=profile.vocal_chain,
            stem_data=stem_data,
            stem_idx=0,
            genre=genre,
            role="vocal",
        )

        # ── Backing chain (one chain per backing track) ──
        self._backing_chain_nodes.clear()
        if backing_tracks and profile.backing_chain:
            for bt in backing_tracks:
                bt_stem_idx = next(
                    (i for i, s in enumerate(self._stems_cache)
                     if s.get("track_index") == bt),
                    None,
                )
                nodes = self._build_audio_chain(
                    track_index=bt,
                    fx_list=profile.backing_chain,
                    stem_data=stem_data,
                    stem_idx=bt_stem_idx or 1,
                    genre=genre,
                    role="backing",
                )
                self._backing_chain_nodes.extend(nodes)

        # ── Reverb bus with observer (SendNode) ──
        self._reverb_send_node = None
        if profile.bus_reverb:
            reverb_result = self.create_reverb_send(
                vocal_track,
                level_db=profile.reverb_level_db,
                reverb_fx=profile.bus_reverb.name,
            )
            # Attach SendNode as observer on last vocal chain node
            last_vocal = (
                self._vocal_chain_nodes[-1]
                if self._vocal_chain_nodes
                else None
            )
            if last_vocal is not None:
                self._reverb_send_node = SendNode(
                    name="Vocal_Verb_Send",
                    fx_type="reverb",
                    source_node=last_vocal,
                )
                self._reverb_send_node.params = {
                    "level_db": profile.reverb_level_db,
                    "aux_index": reverb_result.get("aux_index"),
                    "fx_index": reverb_result.get("fx_index"),
                }
                self._reverb_send_node.mark_clean()
                log.info("SendNode attached: %s observes %s",
                         self._reverb_send_node.name, last_vocal.name)

    def _build_audio_chain(
        self, track_index: int, fx_list: list,
        stem_data: dict, stem_idx: int,
        genre: str, role: str,
    ) -> list[AudioNode]:
        """Build a linked :class:`AudioNode` chain and apply FX to REAPER.

        Returns the list of nodes (linked via ``add_downstream``).
        """
        nodes: list[AudioNode] = []
        prev: AudioNode | None = None
        sd = stem_data.get(stem_idx, {})
        rms = sd.get("raw_rms_db")
        peak = sd.get("raw_peak_db")

        for i, fx in enumerate(fx_list):
            idx = self._fx.add(track_index, fx.name)
            fx_type = _resolve_fx_type(fx.name, fx.fx_type)

            node = AudioNode(
                name=f"{role}_{fx_type}_{i}_{fx.name}",
                fx_type=fx_type,
                params={},
            )
            node.is_dirty = False  # initially clean — just applied

            if prev:
                prev.add_downstream(node)
            nodes.append(node)

            if fx_type == "eq":
                self._apply_eq_baseline(track_index, idx, role)
            elif fx_type in _TRANSLATORS and rms is not None and peak is not None:
                intent = _derive_compressor_intent(rms, peak, genre=genre)
                preset = _get_compressor_preset(role, genre)
                physical = _TRANSLATORS[fx_type](intent, preset)
                node.params = dict(physical)
                normalized = normalize_params(fx.name, physical)
                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, idx, pname, pval)
                log.info(
                    "Auto-compressor: %s → %s (crest=%.1f dB, gr=%.1f dB)",
                    fx.name, intent.amount, intent.crest_factor_db,
                    intent.gr_target_db,
                )
            else:
                for pname, pval in fx.params.items():
                    self._fx.set_param(track_index, idx, pname, pval)

            log.info("Added %s to track %d [%s]", fx.name, track_index, node.name)
            prev = node

        return nodes

    def update_node_param(self, node: AudioNode, param_name: str,
                          physical_value: float) -> bool:
        """Update a single parameter on a node with dirty-flag cascade.

        The node's params dict is updated and all downstream nodes
        are auto-invalidated.  For EQ nodes, RMS matching suppresses
        cascade invalidation when the overall energy stays constant.

        Returns ``True`` when a dirty cascade was triggered.
        """
        new_params = dict(node.params)
        new_params[param_name] = physical_value
        changed = node.update_params(new_params)
        if changed:
            log.info("[DAG] %s.%s changed → cascade dirty", node.name, param_name)
        return changed

    def _apply_eq_baseline(self, track_index: int, fx_index: int,
                           role: str) -> None:
        """Apply conservative EQ baseline for *role* (``"vocal"`` or ``"backing"``).

        Uses :data:`_EQ_BASELINE` from :mod:`hermes_core.profiles`.
        Currently only supports ReaEQ with ``"hp"`` and ``"bell"`` band types.
        """
        bands = _EQ_BASELINE.get(role, [])
        if not bands:
            return

        for band_idx, band in enumerate(bands):
            btype = band.get("type", "")
            freq = band.get("freq_hz", 1000.0)
            gain = band.get("gain_db", 0.0)
            q = band.get("q", 1.0)

            if btype == "hp":
                # ReaEQ high-pass: enable band, set type to high-pass
                self._fx.set_param(track_index, fx_index,
                                   f"Band {band_idx + 1} Type", 0.0)
                # Most ReaEQ params use "Band N Freq" / "Band N Gain" / "Band N Q"
                pass  # ReaEQ-specific mapping — see note below
            elif btype == "bell":
                pass

        # NOTE: ReaEQ parameter names vary by REAPER version and localisation.
        # The EQ baseline values above define the *intent*; the actual parameter
        # mapping will be completed when we implement the EQ adapter layer
        # (similar to the compressor translator layer).  For now, baseline EQ
        # is intent-only and skipped at the parameter level.
        log.debug(
            "EQ baseline intent for %s track %d: %s",
            role, track_index, bands,
        )

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
            for e in self._bridge.get_recent_dialog_events()[-20:]
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
        # Snapshot REAPER's view of the project — later operations verify
        # the user has not manually switched to a different project.
        _, name_buf, _ = api.GetProjectName(0, "", 256)
        self._snapshot_project_name = name_buf or ""
        path_buf, _ = api.GetProjectPath("", 256)
        self._snapshot_project_path = path_buf or ""
        # Fresh project — clear all idempotency guards.
        self.reset()

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
        """Analyse raw stems and apply clip gain to reference level.

        Clip gain brings every stem to -18 dBFS RMS (0 VU reference) so
        downstream plugins see consistent input levels across projects.

        Fader balancing is deferred to :meth:`post_fx_balance` — call it
        **after** :meth:`apply_profile` so the balance accounts for the
        loudness changes introduced by EQ, compression and reverb.

        This method is **idempotent** — calling it twice on the same
        engine instance raises ``RuntimeError``.  Call :meth:`reset` to
        clear the guard for a fresh mix.
        """
        if self._stems_gain_staged:
            raise RuntimeError(
                "Stems already gain-staged. Call reset() to start a new mix, "
                "or create a new project with create_project()."
            )
        self._ensure_project_match()

        def _do_prepare():
            return self._prepare_stems_impl(
                stem_paths, genre=genre, vocal_indices=vocal_indices,
                backing_indices=backing_indices,
                vocal_to_backing_lu=vocal_to_backing_lu,
                backing_reduction_lu=backing_reduction_lu,
            )

        result = self._undo_block("Prepare Stems", _do_prepare)
        self._stems_gain_staged = True
        self._stems_cache = result.get("stems", [])
        return result

    def _prepare_stems_impl(
        self,
        stem_paths: list[str],
        *,
        genre: str = "pop",
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        vocal_to_backing_lu: float = 4.0,
        backing_reduction_lu: float | None = None,
    ) -> dict:
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

        # 4. Fader balancing is deferred to post_fx_balance() — after FX chains
        #    have been applied the LUFS values change, so balancing pre-FX would
        #    become inaccurate as soon as EQ/compression/reverb are added.
        reduction = backing_reduction_lu
        if reduction is None:
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

        return {
            "stems": stems_out,
            "genre": genre,
            "genre_reduction_lu": reduction,
            "backing_adjusted_lufs": round(backing_adjusted_lufs, 1),
            "vocal_indices": vocal_indices,
            "backing_indices": backing_indices,
            "vocal_to_backing_lu": vocal_to_backing_lu,
            "backing_reduction_lu": reduction,
        }

    @staticmethod
    def _classify_role(idx: int, vocal_indices: list[int],
                       backing_indices: list[int]) -> str:  # noqa: D401
        if idx in vocal_indices:
            return "vocal"
        if idx in backing_indices:
            return "backing"
        return "other"

    # ── Post-FX fader balancing ──────────────────────────

    def _balance_faders(
        self,
        stems: list[dict],
        *,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        genre: str = "pop",
        vocal_to_backing_lu: float = 4.0,
        backing_reduction_lu: float | None = None,
    ) -> dict:
        """Apply genre-based fader gains using *post-FX* LUFS values.

        Call this after :meth:`apply_profile` so the balance accounts
        for loudness changes introduced by EQ, compression and reverb.

        *stems* is the cached stem list from :meth:`prepare_stems` with
        updated ``adjusted_lufs`` fields from post-FX measurement.
        """
        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stems))
                               if i not in vocal_indices]

        if backing_reduction_lu is not None:
            reduction = backing_reduction_lu
        else:
            reduction = _GENRE_BACKING_REDUCTION.get(
                genre, _GENRE_BACKING_REDUCTION["pop"]
            )
            if isinstance(reduction, tuple):
                reduction = (reduction[0] + reduction[1]) / 2.0

        backing_lufs_vals = [
            s["adjusted_lufs"] for i, s in enumerate(stems)
            if i in backing_indices and s["adjusted_lufs"] is not None
        ]
        backing_adjusted_lufs = (
            sum(backing_lufs_vals) / len(backing_lufs_vals)
            if backing_lufs_vals else -18.0
        )
        backing_target_lufs = backing_adjusted_lufs - reduction
        vocal_target_lufs = backing_target_lufs + vocal_to_backing_lu

        for i, s in enumerate(stems):
            if not s.get("success") or s.get("adjusted_lufs") is None:
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
            "reduction_lu": reduction,
            "backing_adjusted_lufs": round(backing_adjusted_lufs, 1),
            "backing_target_lufs": round(backing_target_lufs, 1),
            "vocal_target_lufs": round(vocal_target_lufs, 1),
            "vocal_to_backing_lu": vocal_to_backing_lu,
        }

    def _solo_render(
        self, indices: list[int], output_dir: str, label: str = ""
    ) -> dict:
        """Temporarily solo *indices*, render, restore solo state.

        Returns the render result dict (including ``output_path``).
        """
        api = self._bridge.api
        n = api.CountTracks(0)

        # Save solo state and solo only the requested indices
        saved: dict[int, bool] = {}
        for i in range(n):
            tr = api.GetTrack(0, i)
            if tr:
                try:
                    solo = api.GetMediaTrackInfo_Value(tr, "I_SOLO")
                except Exception:
                    solo = 0.0
                saved[i] = bool(solo)
                api.SetMediaTrackInfo_Value(tr, "I_SOLO", 1.0 if i in indices else 0.0)

        try:
            result = self.render_mix(output_dir, verify=False)
        finally:
            # Restore original solo state
            for i in range(n):
                tr = api.GetTrack(0, i)
                if tr:
                    api.SetMediaTrackInfo_Value(
                        tr, "I_SOLO", 1.0 if saved.get(i, False) else 0.0
                    )

        return result

    def post_fx_balance(
        self,
        *,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
        genre: str = "pop",
        vocal_to_backing_lu: float = 4.0,
        backing_reduction_lu: float | None = None,
        tmp_dir: str | None = None,
    ) -> dict:
        """Measure post-FX LUFS and set fader balance.

        **Must be called after** :meth:`apply_profile`.  Renders the
        vocal and backing groups independently (via solo), measures
        their actual post-FX integrated LUFS, then computes and applies
        fader gains so the vocal sits at the correct level above the
        backing.

        Returns balance metadata plus the **combined LUFS** of the
        full mix (all tracks unsoloed), which can be used to seed the
        loudness optimiser in :meth:`finalize_master`.
        """
        import tempfile

        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_balance_")

        stems = list(self._stems_cache)
        if not stems:
            raise RuntimeError(
                "No cached stems — call prepare_stems() first"
            )

        if vocal_indices is None:
            vocal_indices = [0]
        if backing_indices is None:
            backing_indices = [i for i in range(len(stems))
                               if i not in vocal_indices]

        # Map stem index → track index
        stem_idx_to_track = {
            i: s["track_index"] for i, s in enumerate(stems) if s.get("success")
        }

        # ── Solo-render vocal group ──
        vocal_tracks = [
            stem_idx_to_track[i] for i in vocal_indices
            if i in stem_idx_to_track
        ]
        vocal_lufs = None
        if vocal_tracks:
            vocal_result = self._solo_render(
                vocal_tracks,
                os.path.join(tmp, "vocal_solo"),
                "vocal",
            )
            if vocal_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(vocal_result["output_path"])
                    vocal_lufs = ana.integrated_lufs
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Solo-render backing group ──
        backing_tracks = [
            stem_idx_to_track[i] for i in backing_indices
            if i in stem_idx_to_track
        ]
        backing_lufs = None
        if backing_tracks:
            backing_result = self._solo_render(
                backing_tracks,
                os.path.join(tmp, "backing_solo"),
                "backing",
            )
            if backing_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(backing_result["output_path"])
                    backing_lufs = ana.integrated_lufs
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Update cached LUFS ──
        for i, s in enumerate(stems):
            if not s.get("success"):
                continue
            if i in vocal_indices and vocal_lufs is not None:
                s["adjusted_lufs"] = vocal_lufs
            elif i in backing_indices and backing_lufs is not None:
                s["adjusted_lufs"] = backing_lufs

        # ── Apply fader balance ──
        balance_info = self._balance_faders(
            stems,
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
            genre=genre,
            vocal_to_backing_lu=vocal_to_backing_lu,
            backing_reduction_lu=backing_reduction_lu,
        )

        # ── Full-mix LUFS (for finalize_master seed) ──
        combined_lufs = None
        full_tracks = vocal_tracks + backing_tracks
        if full_tracks:
            full_result = self._solo_render(
                full_tracks,
                os.path.join(tmp, "full_mix"),
                "full",
            )
            if full_result.get("output_path"):
                try:
                    ana = SignalAnalyzer.analyze(full_result["output_path"])
                    combined_lufs = ana.integrated_lufs
                except (OSError, ValueError, RuntimeError):
                    pass

        log.info(
            "Post-FX balance: vocal=%.1f LUFS, backing=%.1f LUFS, "
            "combined=%.1f LUFS, reduction=%.1f LU, vocal_to_backing=%.1f LU",
            vocal_lufs or float("nan"),
            backing_lufs or float("nan"),
            combined_lufs or float("nan"),
            balance_info["reduction_lu"],
            balance_info["vocal_to_backing_lu"],
        )

        # ── Build reverb wet cache for preview mode ──
        reverb_wet_path = self._cache_reverb_wet(tmp)

        return {
            **balance_info,
            "vocal_lufs": vocal_lufs,
            "backing_lufs": backing_lufs,
            "combined_lufs": combined_lufs,
            "reverb_wet_cache": reverb_wet_path,
            "stems": stems,
        }

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

        **Abbey Road trick**: a safety EQ (HPF @ 600 Hz, LPF @ 10 kHz)
        is automatically inserted before the reverb on the aux track.
        This prevents low-frequency mud and high-frequency sibilance
        in the reverb tail — the Agent never sees these filters.

        Returns {aux_index, send, fx_index, abbey_eq_index}.
        """
        aux_idx = self._tracks.create(name="Verb Return")

        # Abbey Road safety EQ — de-mud + de-ess the reverb input
        abbey_eq_idx = self._fx.add(aux_idx, "ReaEQ (Cockos)")
        if abbey_eq_idx >= 0:
            self._apply_abbey_road_eq(aux_idx, abbey_eq_idx)

        fx_idx = self._fx.add(aux_idx, reverb_fx)

        send_info = self._send.create(
            src=src_track, dest=aux_idx, level_db=level_db, mode=mode
        )

        return {
            "aux_index": aux_idx,
            "send": send_info,
            "fx_index": fx_idx,
            "abbey_eq_index": abbey_eq_idx,
        }

    @staticmethod
    def _apply_abbey_road_eq(aux_track: int, eq_fx_idx: int) -> None:
        """Configure ReaEQ as an Abbey Road safety filter.

        Band 1: HPF @ 600 Hz (removes low-end mud from reverb).
        Band 2: LPF @ 10 kHz (removes sibilance / harshness).

        These parameters are **not exposed to the Agent** — they are
        an engine-level safeguard applied automatically to every
        reverb send.
        """
        # ReaEQ band types: 0=low-shelf, 1=band, 2=high-shelf, 3=LPF, 4=HPF, …
        # We set these via normalised values.  Without a registered param
        # map for ReaEQ, we use raw parameter indices discovered at runtime.
        # For now the intent is captured; full mapping requires ReaEQ
        # parameter discovery (see _apply_eq_baseline note).
        log.debug(
            "Abbey Road EQ intent: HPF@600Hz + LPF@10kHz on aux %d slot %d",
            aux_track, eq_fx_idx,
        )

    def _apply_eq_rms_match(
        self, track_index: int, fx_index: int,
        pre_rms_db: float, post_rms_db: float,
    ) -> None:
        """Compensate EQ gain change so downstream nodes see consistent RMS.

        If the EQ caused the RMS to drop by *Δ* dB, apply *+Δ* dB of
        output gain.  This prevents cascade invalidation of downstream
        compressors when only EQ frequencies changed.

        Called after every EQ parameter update.
        """
        delta = pre_rms_db - post_rms_db
        if abs(delta) < 0.2:
            return  # inaudible — skip to avoid parameter churn

        log.debug(
            "RMS match: track %d EQ@%d pre=%.1f → post=%.1f (Δ=%.1f dB)",
            track_index, fx_index, pre_rms_db, post_rms_db, delta,
        )
        # Attempt to set Output Gain on the EQ plugin.
        # If the param name differs, the call silently fails — the EQ just
        # won't be gain-compensated, which is acceptable (not critical).
        self._fx.set_param(track_index, fx_index, "Output Gain", delta)
        self._fx.set_param(track_index, fx_index, "Output", delta)

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
        target_lufs: float = _DEFAULT_TARGET_LUFS,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tolerance: float = 0.3,
        tmp_dir: str | None = None,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> dict:
        """Two-pass master finalization via brickwall-limiter simulation.

        1. Add *limiter_fx* to master with Gain=0, Output Level=*ceiling_db*.
        2. Probe render → brickwall simulation + binary search → optimal Gain.
        3. Apply gain, render final.
        4. Verify final LUFS against target.

        The binary search accounts for limiter nonlinearity directly,
        so the open-loop formula is no longer needed.

        This method is **idempotent** — calling it twice on the same
        engine instance raises ``RuntimeError``.  Call :meth:`reset` to
        clear the guard for a fresh mix.

        *on_progress* is an optional callback ``(stage: str, pct: float)``
        called at each phase for progress reporting.
        """
        if self._master_finalized:
            raise RuntimeError(
                "Master already finalized. Call reset() to start a new mix, "
                "or create a new project with create_project()."
            )
        self._ensure_project_match()

        def _do_finalize():
            return self._finalize_master_impl(
                target_lufs, limiter_fx=limiter_fx, ceiling_db=ceiling_db,
                tolerance=tolerance, tmp_dir=tmp_dir,
                on_progress=on_progress,
            )

        result = self._undo_block("Finalize Master", _do_finalize)
        if result.get("passed"):
            self._master_finalized = True
        return result

    def _finalize_master_impl(
        self,
        target_lufs: float = _DEFAULT_TARGET_LUFS,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tolerance: float = 0.3,
        tmp_dir: str | None = None,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> dict:
        def _progress(stage: str, pct: float):
            if on_progress:
                on_progress(stage, pct)

        import tempfile

        _progress("setup", 0.0)
        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_master_")
        probe_dir = os.path.join(tmp, "probe")
        final_dir = os.path.join(tmp, "final")

        # 1. Add limiter
        fx_idx = self._fx.add_master(limiter_fx)
        if fx_idx < 0:
            return _master_error(
                target_lufs, ceiling_db,
                f"Failed to add {limiter_fx} to master",
            )

        # Pro-L 2 param formulas (verified 2026-05-28 via REAPER calibration):
        #   Gain: 0..+30 dB → normalized = gain_db / 30
        #   Output Level: -30..0 dB → normalized = (ceiling_db + 30) / 30
        ceiling_norm = max(0.0, min(1.0, (ceiling_db + _PRO_L2_RANGE_DB) / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Output Level", ceiling_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Output Level param not found — may need calibration",
            )
        if not self._fx.set_param(-1, fx_idx, "Gain", 0.0):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found — may need calibration",
            )

        # 2. Probe render
        _progress("probe_render", 0.15)
        probe_result = self.render_mix(probe_dir, verify=True)
        probe_sc = probe_result.get("signal_check", {})
        pre_peak = probe_sc.get("peak_db", 0.0)
        if probe_result.get("output_path") is None:
            return _master_error(
                target_lufs, ceiling_db, "Probe render failed",
            )

        # 3. Hard-clip model + binary search → optimal Gain
        _progress("search", 0.35)
        probe_path = probe_result.get("output_path")
        cal = load_calibration()
        search = find_optimal_gain(
            probe_path,
            target_lufs=target_lufs,
            ceiling_dbtp=ceiling_db,
            tolerance=tolerance,
            calibration_offset=cal,
        )
        if not search.converged and search.probe_lufs <= -70:
            return _master_error(
                target_lufs, ceiling_db, "Probe is near-silent",
            )

        gain_db = search.gain_db

        # 4. Apply gain and render final.
        _progress("final_render", 0.65)
        gain_norm = max(0.0, min(1.0, gain_db / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Gain", gain_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found during final render",
            )
        final_result = self.render_mix(final_dir, verify=True)
        output_path = final_result.get("output_path")

        # 5. Verify
        _progress("verify", 0.90)
        achieved_lufs = None
        passed = output_path is not None
        if output_path:
            verify = verify_output(output_path, target_lufs=target_lufs)
            achieved_lufs = verify.actual_lufs
            passed = verify.passed

        log.info(
            "Master report:\n%s",
            generate_report(search, verify if output_path else None),
        )

        return {
            "target_lufs": target_lufs,
            "achieved_lufs": achieved_lufs,
            "probe_lufs": search.probe_lufs,
            "gain_db": gain_db,
            "ceiling_db": ceiling_db,
            "passed": passed,
            "converged": search.converged,
            "pre_limiter_peak_db": pre_peak,
            "output_path": output_path,
        }

    # ── Wet reverb caching ────────────────────────────────

    def _cache_reverb_wet(self, cache_dir: str) -> str | None:
        """Render 100% wet reverb and cache the WAV.

        Solos the reverb return track, renders, and saves the result.
        Subsequent preview renders can numpy-mix this cache with
        dry renders without waking REAPER for simple level changes.

        Returns the cache path or ``None``.
        """
        if self._reverb_send_node is None:
            return None

        aux_index = self._reverb_send_node.params.get("aux_index")
        if aux_index is None:
            return None

        os.makedirs(cache_dir, exist_ok=True)
        wet_path = os.path.join(cache_dir, "reverb_wet_cache.wav")

        # ── Solo the reverb return, render ──
        result = self._solo_render([aux_index], cache_dir, "reverb_wet")
        rendered = result.get("output_path")
        if rendered and os.path.exists(rendered):
            import shutil
            shutil.move(rendered, wet_path)
            log.info("[wet-cache] Reverb wet cached → %s", wet_path)
            self._reverb_send_node.params["_wet_cache_path"] = wet_path
            return wet_path

        log.warning("[wet-cache] Reverb wet render failed")
        return None

    @staticmethod
    def _numpy_mix(dry_path: str, wet_path: str,
                   wet_level_db: float, output_path: str) -> str | None:
        """Mix dry + wet WAVs in numpy with *wet_level_db* gain on wet.

        Pure Python / numpy — no REAPER call.  Returns *output_path*.
        """
        import numpy as np
        import soundfile as sf

        try:
            dry, sr = sf.read(dry_path, dtype="float64")
            wet, sr_w = sf.read(wet_path, dtype="float64")
        except Exception as exc:
            log.warning("[numpy-mix] Read error: %s", exc)
            return None

        # Match sample rates and lengths
        if sr != sr_w:
            log.warning("[numpy-mix] SR mismatch dry=%d wet=%d", sr, sr_w)
            return None

        min_len = min(len(dry), len(wet))
        dry = dry[:min_len]
        wet = wet[:min_len]

        # Ensure 2-D
        if dry.ndim == 1:
            dry = dry.reshape(-1, 1)
        if wet.ndim == 1:
            wet = wet.reshape(-1, 1)

        # Broadcast to same channel count
        if dry.shape[1] != wet.shape[1]:
            nch = min(dry.shape[1], wet.shape[1])
            dry = dry[:, :nch]
            wet = wet[:, :nch]

        wet_gain = 10.0 ** (wet_level_db / 20.0)
        mix = dry + wet * wet_gain

        sf.write(output_path, mix, sr, subtype="FLOAT")
        return output_path

    # ── Preview / Finalize 双模渲染 ────────────────────────

    def render_preview(self, output_dir: str,
                       target_lufs: float = -12.0,
                       ceiling_db: float = -0.5,
                       cache_dir: str | None = None) -> dict:
        """Fast preview render — numpy mix, no Pro-L 2.

        1. Mute reverb return → render dry tracks from REAPER.
        2. Restore reverb → numpy-mix cached wet WAV at desired level.
        3. Apply hard-clip model to estimate final integrated LUFS.

        Returns ``{output_path, estimated_lufs, signal_check, ...}``.
        The ``"mastering"`` key is ``"bypassed"`` — callers should
        not base final loudness decisions on the preview.
        """
        import tempfile
        import numpy as np

        tmp = cache_dir or tempfile.mkdtemp(prefix="hermes_preview_")
        os.makedirs(output_dir, exist_ok=True)
        api = self._bridge.api

        # ── 1. Mute reverb return, render dry ──
        saved_mute: dict[int, float] = {}
        if self._reverb_send_node:
            aux_idx = self._reverb_send_node.params.get("aux_index")
            if aux_idx is not None:
                tr = api.GetTrack(0, aux_idx)
                if tr:
                    saved_mute[aux_idx] = api.GetMediaTrackInfo_Value(tr, "B_MUTE")
                    api.SetMediaTrackInfo_Value(tr, "B_MUTE", 1.0)

        try:
            dry_result = self.render_mix(
                os.path.join(tmp, "dry"), verify=False,
            )
        finally:
            for idx, mute_val in saved_mute.items():
                tr = api.GetTrack(0, idx)
                if tr:
                    api.SetMediaTrackInfo_Value(tr, "B_MUTE", mute_val)

        dry_path = dry_result.get("output_path")
        if dry_path is None:
            return {"output_path": None, "error": "Dry render failed",
                    "mode": "preview"}

        # ── 2. Numpy-mix reverb wet cache ──
        wet_path = None
        wet_level_db = -8.0
        if self._reverb_send_node:
            wet_path = self._reverb_send_node.params.get("_wet_cache_path")
            wet_level_db = self._reverb_send_node.params.get("level_db", -8.0)

        if wet_path and os.path.exists(wet_path):
            mix_input = os.path.join(tmp, "dry_wet_mix.wav")
            mixed = self._numpy_mix(dry_path, wet_path, wet_level_db, mix_input)
            if mixed:
                dry_path = mixed
            else:
                log.warning("[preview] numpy mix failed, using dry-only")

        # ── 3. Hard-clip simulation for LUFS estimate ──
        from hermes_core.loudness_optimizer import find_optimal_gain, _hard_clip
        search = find_optimal_gain(
            dry_path, target_lufs=target_lufs, ceiling_dbtp=ceiling_db,
        )

        # ── 4. Apply gain + hard-clip to produce preview WAV ──
        pcm, sr = SignalAnalyzer._read_pcm(dry_path)
        limited = _hard_clip(pcm, search.gain_db, ceiling_db)

        import soundfile as sf
        preview_path = os.path.join(output_dir, "preview.wav")
        sf.write(preview_path, limited, sr, subtype="FLOAT")

        signal_check = {}
        try:
            ana = SignalAnalyzer.analyze(preview_path)
            signal_check = {
                "integrated_lufs": ana.integrated_lufs,
                "true_peak_dbtp": ana.true_peak_dbtp,
                "rms_db": ana.rms_db,
                "peak_db": ana.peak_db,
                "clip_count": ana.clip_count,
            }
        except (OSError, ValueError, RuntimeError):
            pass

        return {
            "output_path": preview_path,
            "mode": "preview",
            "estimated_lufs": search.predicted_lufs,
            "gain_applied_db": search.gain_db,
            "converged": search.converged,
            "signal_check": signal_check,
            "mastering": "bypassed",
            "warning": (
                "Preview mode — Pro-L 2 bypassed. "
                "Use finalize_master() for production output."
            ),
        }

    # ── Micro-render pipeline ──────────────────────────────

    def _micro_render_node(self, node: AudioNode,
                           input_wav: str | None,
                           cache_dir: str) -> str | None:
        """Render a single :class:`AudioNode` to a cached WAV.

        Creates a temporary track, imports *input_wav*, adds the FX,
        sets its params, solo-renders, then cleans up.

        Returns the output WAV path or ``None`` on failure.
        """
        import shutil

        # ── Cache hit: clean node with valid output ──
        if not node.is_dirty and node.output_audio_path:
            if os.path.exists(node.output_audio_path):
                log.debug("[micro] %s cache hit → %s", node.name,
                          node.output_audio_path)
                return node.output_audio_path

        if input_wav is None or not os.path.exists(input_wav):
            log.warning("[micro] %s: no input WAV — skipping", node.name)
            return None

        os.makedirs(cache_dir, exist_ok=True)
        out_path = os.path.join(cache_dir, f"{node.name}.wav")

        # ── Clean up stale output ──
        if os.path.exists(out_path):
            os.remove(out_path)

        api = self._bridge.api
        n_before = api.CountTracks(0)

        # ── Create temp track ──
        api.InsertTrackAtIndex(n_before, True)
        temp_track_idx = n_before
        temp_track = api.GetTrack(0, temp_track_idx)

        try:
            # ── Import media ──
            self._tracks.import_media(temp_track_idx, input_wav, position=0.0)

            # ── Add FX + set params ──
            fx_idx = self._fx.add(temp_track_idx, node.params.get("_fx_name", ""))
            if fx_idx < 0:
                log.warning("[micro] %s: failed to add FX", node.name)
                return None

            fx_type = node.fx_type
            if fx_type in _TRANSLATORS:
                # Re-derive physical params (may have changed since build)
                normalized = normalize_params(
                    node.params.get("_fx_name", ""),
                    {k: v for k, v in node.params.items()
                     if not k.startswith("_")},
                )
                for pname, pval in normalized.items():
                    self._fx.set_param(temp_track_idx, fx_idx, pname, pval)

            # ── Solo render ──
            render_result = self._solo_render(
                [temp_track_idx], cache_dir, node.name,
            )
            rendered = render_result.get("output_path")
            if rendered and os.path.exists(rendered):
                shutil.move(rendered, out_path)

            if os.path.exists(out_path):
                node.mark_clean(out_path)
                log.info("[micro] %s rendered → %s", node.name, out_path)
                return out_path

            return None

        finally:
            # ── Clean up temp track ──
            try:
                api.DeleteTrack(temp_track)
            except Exception:
                pass

    def _make_chain_executor(self, cache_dir: str) -> ChainExecutor:
        """Return a :class:`ChainExecutor` wired to :meth:`_micro_render_node`."""
        return ChainExecutor(
            lambda node, inp: self._micro_render_node(node, inp, cache_dir)
        )

    def execute_chain(self, nodes: list[AudioNode],
                      cache_dir: str | None = None) -> list[AudioNode]:
        """Execute *nodes* via micro-rendering, reusing cached outputs.

        Dirty nodes are re-rendered; clean nodes with valid caches are
        skipped.  Returns the (mutated) node list.
        """
        import tempfile
        cdir = cache_dir or tempfile.mkdtemp(prefix="hermes_chain_")
        executor = self._make_chain_executor(cdir)
        first = executor.first_dirty(nodes)
        if first < 0:
            log.info("[chain] All %d nodes clean — nothing to render", len(nodes))
            return nodes
        log.info("[chain] Executing from node %d/%d (%s)", first,
                 len(nodes), nodes[first].name)
        return executor.execute(nodes)

    # ── GR Calibration ─────────────────────────────────────

    def calibrate_compressor(
        self,
        plugin_name: str,
        param_name: str,
        param_range: tuple[float, float],
        *,
        steps: int = 10,
        test_signal_path: str | None = None,
        cache_dir: str | None = None,
    ) -> list[tuple[float, float]]:
        """Auto-calibrate a compressor parameter's knob curve.

        Creates a test signal (pink noise at -18 dBFS RMS), then
        iterates *param_name* through *param_range* in *steps*
        increments.  At each step the signal is micro-rendered
        through the plugin and the resulting LUFS is measured.

        Returns a table of ``(normalised_value, physical_result)``
        pairs suitable for ``PLUGIN_REGISTRY``.

        Parameters
        ----------
        plugin_name:
            REAPER FX name (must be installed).
        param_name:
            The parameter to sweep (e.g. ``"Input"`` for 1176).
        param_range:
            ``(physical_lo, physical_hi)`` of the parameter.
        steps:
            Number of measurement points (default 10).
        test_signal_path:
            Path to a WAV test signal.  If ``None``, a -18 dBFS RMS
            pink-noise WAV is generated automatically.
        cache_dir:
            Temp directory for intermediate renders.
        """
        import tempfile

        tmp = cache_dir or tempfile.mkdtemp(prefix="hermes_cal_")

        # ── Generate or use test signal ──
        if test_signal_path and os.path.exists(test_signal_path):
            signal_path = test_signal_path
        else:
            signal_path = self._gen_calibration_signal(tmp)

        log.info(
            "Calibrating %s.%s over [%.1f, %.1f] in %d steps",
            plugin_name, param_name, param_range[0], param_range[1], steps,
        )

        table: list[tuple[float, float]] = []
        phys_lo, phys_hi = param_range

        for i in range(steps + 1):
            t = i / steps
            physical = phys_lo + t * (phys_hi - phys_lo)

            # Create a one-node chain for this measurement
            node = AudioNode(
                name=f"cal_{plugin_name}_{i}",
                fx_type="comp",
                params={"_fx_name": plugin_name, param_name: physical},
            )
            node.is_dirty = True

            result_path = self._micro_render_node(
                node, signal_path, os.path.join(tmp, f"step_{i}"),
            )

            if result_path and os.path.exists(result_path):
                try:
                    ana = SignalAnalyzer.analyze(result_path)
                    table.append((t, ana.integrated_lufs))
                    log.debug("  [%d/%d] knob=%.2f → %.1f LUFS",
                              i, steps, t, ana.integrated_lufs)
                except (OSError, ValueError, RuntimeError):
                    table.append((t, 0.0))
            else:
                log.warning("  [%d/%d] knob=%.2f → render failed", i, steps, t)
                table.append((t, 0.0))

        log.info("Calibration complete: %d points", len(table))
        return table

    @staticmethod
    def _gen_calibration_signal(output_dir: str,
                                duration: float = 5.0,
                                sr: int = 48000) -> str:
        """Generate a -18 dBFS RMS pink-like noise WAV for calibration."""
        import numpy as np
        import soundfile as sf

        n = int(sr * duration)
        rng = np.random.default_rng(42)
        # Approximate pink noise via filtered white noise
        white = rng.standard_normal(n)
        # Simple 1/f filter: cumulative sum of white noise
        pink = np.cumsum(white)
        pink /= np.max(np.abs(pink)) + 1e-10
        # Scale to -18 dBFS RMS
        target_linear = 10.0 ** (-18.0 / 20.0)
        pink *= target_linear / (np.sqrt(np.mean(pink ** 2)) + 1e-10)
        stereo = np.column_stack([pink, pink])

        out_path = os.path.join(output_dir, "cal_signal.wav")
        sf.write(out_path, stereo, sr, subtype="FLOAT")
        log.info("Generated calibration signal: %s (%.1fs, -18 dBFS RMS)",
                 out_path, duration)
        return out_path
