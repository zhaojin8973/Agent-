"""
MixingEngine — Layer 3 public API. Composes all Layer 2 modules into
a single entry point for Hermes acceptance scenarios.
"""

import logging
import math
import os
import shutil
import tempfile
import time
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Callable

from hermes_core.bridge import ReaperBridge, _extract_reaper_string
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager
from hermes_core.fx import FxManager
from hermes_core.send import SendManager
from hermes_core.render import RenderManager
from hermes_core.signal import SignalAnalyzer
from hermes_core.exceptions import BridgeConnectionError as HermesConnectionError, InvalidStateError
from hermes_core.project_meta import (
    ProjectMeta, ProjectIndex, make_project_path, create_project_dirs,
)
from hermes_core.loudness_optimizer import (
    find_optimal_gain,
    verify_output,
    load_calibration,
    generate_report,
    CompressionIntent,
    EqIntent,
    EqBandIntent,
)
from hermes_core.normalize import normalize_params, compute_bus_compressor_params, PLUGIN_REGISTRY
from hermes_core.audio_utils import note_to_ms, read_pcm
from hermes_core.profiles import (
    _resolve_fx_type,
    _get_compressor_preset,
    _EQ_BASELINE,
    get_bpm_timing,
)
from hermes_core.dag import AudioNode, SendNode, ChainExecutor
from hermes_core.spectrum import SpectrumAnalyzer, SpectrumReport
from hermes_core.config import HermesConfig

# ── 从提取的子模块重新导出（向后兼容）──────────────────────────
from hermes_core.genre_tables import (
    _GENRE_VOCAL_TO_BACKING,
    _PEAK_CEILING_DB,
    _GENRE_TARGET_LUFS,
    _CLIP_GAIN_REF_DB,
    _DEFAULT_TARGET_LUFS,
    _PRO_L2_RANGE_DB,
    _GENRE_CREST_GR_RATIO,
    _GENRE_RVOX_MULTIPLIER,
    _GENRE_PRODS_RANGE,
    _GENRE_CLA76_ATTACK_BASE,
    _GENRE_CLA76_ATTACK_K,
    _GENRE_REVERB_SEND_BASE,
    _GENRE_DELAY_SEND_BASE,
    _SEND_LEVEL_MIN,
    _SEND_LEVEL_MAX,
    _SEND_DISABLED_THRESHOLD,
    _CREST_REFERENCE,
    _PRESENCE_DEFICIT_THRESHOLD,
    _SIBILANCE_REFERENCE_PEAK,
    _SECTION_BOOST,
    _GENRE_RETURN_EQ,
    _GENRE_SPATIAL_PARAMS,
    _SPATIAL_PARAM_FALLBACK_MAP,
    _SPATIAL_PLUGIN,
    _SPATIAL_BUS_NAMES,
    _REVERB_BUS_TYPES,
    _DELAY_BUS_TYPES,
    _CLA76_ATTACK_KNOB_MIN,
    _CLA76_ATTACK_KNOB_MAX,
    _CLA76_GR_TABLE,
    _CLA76_ATTACK_MS_TABLE,
    _CLA76_RELEASE_MS_TABLE,
    _GENRE_EQ_TWEAKS,
    _MIN_EQ_Q,
    _PROQ3_SHAPE,
    _PROQ3_FREQ_LOG_BASE,
    _LF_FRQ_TABLE,
    _LMF_FRQ_STEPS,
    _HMF_FRQ_TABLE,
    _HF_FRQ_TABLE,
    _HP_FRQ_TABLE,
    _SSL_Q_MIN,
    _SSL_Q_MAX,
    _SSL_Q_RANGE,
)
from hermes_core.comp_engine import (
    _compute_cla76_attack_knob,
    _derive_compressor_intent,
    _apply_vca_params,
    _apply_fet_params,
    _apply_cla76_params,
    _apply_opto_params,
    _apply_rvox_params,
    _ms_to_cla76_attack,
    _ms_to_cla76_release,
    _lookup_ms_table,
)
from hermes_core.eq_engine import (
    _derive_eq_intent,
    _proq3_freq_norm,
    _proq3_q_norm,
    _apply_proq3_eq,
    _ssleq_freq_norm,
    _ssleq_q_norm,
    _apply_ssleq_eq,
)
from hermes_core.mastering import MasteringEngine, _get_genre_target_lufs, _master_error, _friendly_hint
from hermes_core.gain_staging import GainStagingEngine
from hermes_core.spatial_engine import _compute_spatial_sends

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 管线状态枚举
# ════════════════════════════════════════════════════════════════

class PipelineState(Enum):
    CREATED = "created"
    STEMS_PREPARED = "stems_prepared"
    FX_APPLIED = "fx_applied"
    SPATIAL_APPLIED = "spatial_applied"
    MASTERED = "mastered"
    RENDERED = "rendered"


# ════════════════════════════════════════════════════════════════
# Compressor dispatcher（保留在 engine.py，是引擎级调度逻辑）
# ════════════════════════════════════════════════════════════════

# Note: "rvox" is NOT in this dictionary because it requires special handling
# (rvox_multiplier parameter). See dispatch logic at line ~1344.
# CLA-76 is also handled separately (different signature: attack_knob, release_knob).
_TRANSLATORS = {
    "vca":  _apply_vca_params,
    "fet":  _apply_fet_params,
    "opto": _apply_opto_params,
}

class MixingEngine:
    """Top-level REAPER mixing engine. Use as context manager for auto-connect.

    with MixingEngine() as eng:
        eng.create_project(sample_rate=48000)
        eng.import_stems(["/path/to/audio.wav"])
        result = eng.render_mix("/tmp/output")
    """

    def __init__(self, watchdog: bool = False, audit_logger=None):
        self._bridge = ReaperBridge(dialog_killer=watchdog)
        self._tracks = TrackManager(self._bridge)
        self._bus = BusManager(self._bridge)
        self._fx = FxManager(self._bridge)
        self._send = SendManager(self._bridge)
        self._render = RenderManager(self._bridge)
        self._watchdog_enabled = watchdog
        self._project_path: str | None = None
        self._meta: ProjectMeta | None = None  # 工程元数据
        self._meta_dir: str | None = None      # 工程文件夹路径
        self._dirty: bool = False              # 自上次保存以来是否有修改
        self._snapshot_project_path: str | None = None  # from GetProjectPath at init
        self._snapshot_project_name: str | None = None  # from GetProjectName at init
        self._audit = audit_logger             # 可选 AuditLogger，记录管线操作
        self._stage_t0: float = 0.0            # 当前阶段开始时间戳（monotonic）
        # Idempotency guards — prevent double-execution of destructive ops.
        self._stems_gain_staged: bool = False
        self._master_finalized: bool = False
        # Safety guard — prevent accidental track deletion.
        # Set to False to allow create_project / reset to wipe tracks.
        self._tracks_protected: bool = True
        self._stems_cache: list[dict] = []

        # AudioNode pipeline — built by apply_profile()
        self._vocal_chain_nodes: list[AudioNode] = []
        self._backing_chain_nodes: list[AudioNode] = []
        self._reverb_send_node: SendNode | None = None

        # ── 状态机 ────────────────────────────────────────────────
        self._pipeline_state = PipelineState.CREATED
        self._undo_depth = 0

        # ── 子引擎 ────────────────────────────────────────────────
        self._mastering = MasteringEngine(
            self._bridge, self._fx, self._render,
        )
        self._gain_staging = GainStagingEngine(
            self._bridge, self._tracks, SignalAnalyzer,
        )
        self._spatial_result: dict = {}

    # ── Context manager ──────────────────────────────────

    def __enter__(self):
        if not self._bridge.connect():
            raise HermesConnectionError("Failed to connect to REAPER bridge")
        return self

    def __exit__(self, *args):
        if self._watchdog_enabled and self._bridge.dialog_killer_active:
            self._bridge.stop_dialog_killer()
        return False

    def connect(self) -> bool:
        """连接到 REAPER bridge。

        Returns:
            True 表示连接成功，False 表示连接失败
        """
        return self._bridge.connect()

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
        except Exception as e:
            try:
                api.Undo_EndBlock(f"Hermes: {label} (failed)", 0)
            except Exception as inner_e:
                log.debug("Undo_EndBlock cleanup failed: %s", inner_e)
            raise

    # ── 状态机方法 ───────────────────────────────────────────

    def _require_state(self, *allowed: PipelineState) -> None:
        """验证当前管线状态在允许集合内。

        ``CREATED`` 是宽容入口 — 允许独立调用（不强制严格顺序）。
        """
        if PipelineState.CREATED in allowed:
            return
        if self._pipeline_state in allowed:
            return
        raise InvalidStateError(
            f"操作要求状态为 {[s.value for s in allowed]}，"
            f"当前状态为 {self._pipeline_state.value}"
        )

    def _transition_to(self, state: PipelineState) -> None:
        """将管线状态转移到 *state*。"""
        self._pipeline_state = state

    def _auto_save(self) -> None:
        """自动保存工程（静默，不弹窗）。"""
        try:
            if self._bridge._api is not None:
                self._bridge._api.Main_SaveProjectEx(0, "", 0)
        except Exception:
            pass

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

    def allow_track_deletion(self):
        """Unlock destructive track operations.

        Must be called before :meth:`create_project` or any operation that
        deletes tracks.  This is a deliberate opt-in to prevent accidental
        loss of manually placed plugins and project state.
        """
        self._tracks_protected = False

    def reset(self):
        """Clear idempotency guards so the engine can be re-used for a new mix."""
        self._stems_gain_staged = False
        self._master_finalized = False
        self._stems_cache.clear()
        self._vocal_chain_nodes.clear()
        self._backing_chain_nodes.clear()
        self._reverb_send_node = None
        self._bpm = None
        self._last_spectrum: dict = {}
        self._dirty = False
        self._pipeline_state = PipelineState.CREATED
        self._spatial_result.clear()

    def apply_profile(self, profile, /, *, vocal_track: int = 0,
                      backing_tracks: list[int] | None = None,
                      genre: str = "pop",
                      bpm: float | None = None):
        """Apply a :class:`MixingProfile` — FX chains, sends, and auto-compression.

        1. **EQ baseline** — conservative HPF + gentle presence boost.
        2. **Compression** — Crest Factor analysis → :class:`CompressionIntent`
           → translator → normalise → REAPER.  If *bpm* is provided, BPM-aware
           attack/release timing is used (see :func:`get_bpm_timing`).
        3. **Reverb bus** — aux send with Abbey Road safety EQ.

        An :class:`AudioNode` DAG is built in parallel.  Dirty flags cascade
        so that downstream nodes are automatically invalidated when an
        upstream parameter changes (``update_node_param``).
        """
        from hermes_core.profiles import MixingProfile
        if not isinstance(profile, MixingProfile):
            raise TypeError(f"Expected MixingProfile, got {type(profile).__name__}")

        self._profile = profile

        # Resolve BPM: explicit arg takes priority over prepare_stems stash.
        _bpm = bpm if bpm is not None else getattr(self, "_bpm", None)

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
            bpm=_bpm,
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
                    bpm=_bpm,
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

        self._mark_stage("apply_profile")
        self._record_audit(
            "apply_profile", {"genre": genre, "vocal_track": vocal_track},
            f"vocal_fx={len(self._vocal_chain_nodes)} backing_fx={len(self._backing_chain_nodes)} "
            f"reverb={self._reverb_send_node is not None}",
        )

    def _build_audio_chain(
        self, track_index: int, fx_list: list,
        stem_data: dict, stem_idx: int,
        genre: str, role: str,
        bpm: float | None = None,
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
                file_path = sd.get("file_path", "")
                eq_position = fx.eq_position if hasattr(fx, "eq_position") else "solo"
                self._apply_eq_baseline(
                    track_index, idx, role,
                    genre=genre, stem_file_path=file_path,
                    position=eq_position, fx_name=fx.name,
                )
                # Update node params with derived EQ bands for traceability
                if hasattr(self, "_last_eq_params"):
                    node.params = dict(self._last_eq_params)
            elif fx_type in _TRANSLATORS and rms is not None and peak is not None:
                intent = _derive_compressor_intent(rms, peak, genre=genre)

                # BPM-aware timing: override genre preset when BPM is known.
                # Skip RVox — it has no attack/release params (single-fader).
                preset = _get_compressor_preset(role, genre)
                if bpm is not None and bpm > 0 and "cla-76" not in fx.name.lower() and fx_type != "rvox":
                    bpm_timing = get_bpm_timing(bpm)
                    if bpm_timing is not None:
                        preset = dict(preset, **bpm_timing)
                        log.info(
                            "BPM-aware timing: %.0f BPM → attack=%.0fms release=%.0fms",
                            bpm, bpm_timing["attack_ms"], bpm_timing["release_ms"],
                        )

                # CLA-76: crest-driven attack + BPM-driven release
                if "cla-76" in fx.name.lower():
                    attack_knob = _compute_cla76_attack_knob(
                        intent.crest_factor_db, genre,
                    )
                    release_knob = None
                    if bpm is not None and bpm > 0:
                        release_ms = 60000.0 / bpm
                        release_knob = _ms_to_cla76_release(release_ms)
                        log.info(
                            "BPM-aware timing: %.0f BPM → release=%.0fms (knob %.2f)",
                            bpm, release_ms, release_knob,
                        )
                    physical = _apply_cla76_params(
                        intent, attack_knob, release_knob,
                    )
                    log.info(
                        "CLA-76 attack: crest=%.1f → knob=%.2f (genre=%s)",
                        intent.crest_factor_db, attack_knob, genre,
                    )
                elif fx_type == "rvox":
                    rvox_mult = _GENRE_RVOX_MULTIPLIER.get(genre, 1.0)
                    physical = _apply_rvox_params(intent, preset, rvox_mult)
                else:
                    physical = _TRANSLATORS[fx_type](intent, preset)

                # No BPM → leave timing at plugin defaults (don't touch).
                # CLA-76 exception: attack is always set (crest-driven).
                if bpm is None:
                    if "cla-76" in fx.name.lower():
                        physical.pop("Release", None)
                    else:
                        for timing_key in ("Attack", "Release"):
                            physical.pop(timing_key, None)

                node.params = dict(physical)
                normalized = normalize_params(fx.name, physical)
                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, idx, pname, pval)
                log.info(
                    "Auto-compressor: %s → %s (crest=%.1f dB, gr=%.1f dB)",
                    fx.name, intent.amount, intent.crest_factor_db,
                    intent.gr_target_db,
                )
            elif fx_type == "deesser":
                # Pro-DS: threshold from presence deficit.  Fixed detection
                # band HPF=4.6kHz / LPF=12kHz covers sibilance range.
                # Single Vocal mode distinguishes sibilance from harmonics
                # internally — no peak-tracking needed.
                spectrum = getattr(self, "_last_spectrum", {}) or {}
                presence_def = spectrum.get("presence_deficit", 0.0)

                # Threshold: aggressive so Range actually engages as safety net.
                threshold_db = -32.0 + presence_def * 0.1
                threshold_db = max(-60.0, min(0.0, threshold_db))

                # Range: genre-aware max gain reduction (dB).
                range_db = _GENRE_PRODS_RANGE.get(genre, 8.5)

                # Fixed detection band (log: freq ≈ 2000 × 10^n Hz).
                hpf_norm = math.log10(5500.0 / 2000.0)
                lpf_norm = math.log10(12000.0 / 2000.0)

                physical = {
                    "Mode":              0.0,      # Single Vocal
                    "Band Processing":   0.0,      # Wide Band (natural)
                    "Threshold":         round(threshold_db, 1),
                    "Range":             range_db,
                    "Lookahead":         10.0,     # ms (manual: ~10 ms optimal)
                    "Lookahead Enabled": 1.0,
                    "High-Pass Frequency": round(hpf_norm, 3),
                    "Low-Pass Frequency":  round(lpf_norm, 3),
                    "Input Level":       0.0,
                    "Output Level":      0.0,
                    "Wet":               1.0,
                }
                node.params = dict(physical)
                normalized = normalize_params(fx.name, physical)
                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, idx, pname, pval)
                log.info(
                    "Auto-deesser: band=5.5k–12kHz, presence_def=%.1f → "
                    "threshold=%.1f dB, range=%.1f dB (genre=%s)",
                    presence_def, threshold_db, range_db, genre,
                )
            elif fx_type == "saturation":
                # 饱和增强：Crest Factor 推导 Drive 量。
                # 高波峰 → 保留瞬态，少饱和；低波峰 → 增加谐波密度。
                # 优先使用 Decapitator，不可用则跳过（fallback 为 None）。
                crest_db = 12.0
                if rms is not None and peak is not None:
                    crest_db = peak - rms
                drive = round(max(0.1, 1.0 - (crest_db - 8.0) * 0.05), 2)
                drive = max(0.1, min(1.0, drive))

                try:
                    physical = {"Drive": drive, "Mix": 0.5}
                    normalized = normalize_params(fx.name, physical)
                    for pname, pval in normalized.items():
                        self._fx.set_param(track_index, idx, pname, pval)
                    node.params = dict(physical)
                    log.info(
                        "Auto-saturation: crest=%.1fdB → drive=%.2f (plugin=%s)",
                        crest_db, drive, fx.name,
                    )
                except Exception as exc:
                    log.debug("Saturation plugin unavailable (%s), skipping", exc)

            elif fx_type == "dynamic_eq":
                # 动态 EQ：对共振频率设置动态衰减。
                # 使用 Pro-Q 3 动态模式（Dynamics Enabled = 1.0）处理
                # _derive_eq_intent 检测到的共振频点。
                dyn_stem_path = sd.get("file_path", "")
                if dyn_stem_path and os.path.exists(dyn_stem_path):
                    try:
                        report = SpectrumAnalyzer.analyze(dyn_stem_path)
                        eq_intent = _derive_eq_intent(
                            report, role=role, genre=genre, position="solo",
                        )
                        # 为共振频段启用动态模式
                        normalized = _apply_proq3_eq(eq_intent)
                        # 启用动态 EQ 特性：在共振频段开启 Dynamics Enabled
                        for band_num in range(1, 9):
                            band_key = f"Band {band_num} Used"
                            if band_key in normalized and normalized.get(band_key, 0.0) > 0.0:
                                normalized[f"Band {band_num} Dynamics Enabled"] = 1.0
                                normalized[f"Band {band_num} Dynamic Range"] = 0.6
                                normalized[f"Band {band_num} Threshold"] = 0.5
                        for pname, pval in normalized.items():
                            self._fx.set_param(track_index, idx, pname, pval)
                        node.params = dict(normalized)
                        log.info(
                            "Auto-dynamic-EQ: %d resonance bands with dynamic mode (plugin=%s)",
                            sum(1 for b in eq_intent.bands if b.gain_db < 0), fx.name,
                        )
                    except Exception as exc:
                        log.debug("Dynamic EQ spectrum analysis failed (%s), skipping", exc)
                else:
                    log.debug("Dynamic EQ: no stem file available, skipping")

            elif fx_type == "doubler":
                # Doubler/MicroShift：增加人声宽度和空间感。
                # 如果可用则设置默认参数，否则跳过。
                try:
                    physical = {"Mix": 0.3, "Detune": 0.15, "Delay": 0.05}
                    normalized = normalize_params(fx.name, physical)
                    for pname, pval in normalized.items():
                        self._fx.set_param(track_index, idx, pname, pval)
                    node.params = dict(physical)
                    log.info(
                        "Auto-doubler: Mix=%.2f Detune=%.2f (plugin=%s)",
                        0.3, 0.15, fx.name,
                    )
                except Exception as exc:
                    log.debug("Doubler plugin unavailable (%s), skipping", exc)

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

    def write_automation(self, track_idx: int, param_name: str,
                         points: list[tuple[float, float]]) -> dict:
        """写入自动化曲线。

        为指定轨道的参数创建自动化包络并写入时间值对。

        Args:
            track_idx: 轨道索引
            param_name: 参数名（如 ``"D_VOL"``, ``"PAN"``）
            points: ``[(time_sec, value), ...]`` 时间秒和值的二元组列表

        Returns
        -------
        dict
            ``{track_idx, param_name, point_count, envelope_index}``
        """
        api = self._bridge.api
        track_ptr = api.GetTrack(0, track_idx)
        if not track_ptr:
            return {"track_idx": track_idx, "param_name": param_name,
                    "error": "Track not found", "point_count": 0}

        # 获取或创建自动化包络
        envelope = api.GetTrackEnvelopeByName(track_ptr, param_name)
        if not envelope:
            # 尝试通过参数索引创建可见包络
            # REAPER API: GetTrackEnvelopeByName 返回 None 时
            # 可能需要先通过其他方式创建
            log.warning(
                "write_automation: envelope '%s' not found on track %d — "
                "参数可能不可自动化或轨道无该参数",
                param_name, track_idx,
            )
            return {"track_idx": track_idx, "param_name": param_name,
                    "error": f"Envelope '{param_name}' not found",
                    "point_count": 0}

        # 写入时间-值点（按时间排序以确保曲线正确）
        sorted_points = sorted(points, key=lambda p: p[0])
        for time_sec, value in sorted_points:
            api.InsertEnvelopePoint(envelope, time_sec, value, 0, 0.0, 0, True)

        log.info(
            "write_automation: track %d, param=%s — %d points written",
            track_idx, param_name, len(sorted_points),
        )
        return {
            "track_idx": track_idx,
            "param_name": param_name,
            "point_count": len(sorted_points),
        }

    def _apply_eq_baseline(self, track_index: int, fx_index: int,
                           role: str, *,
                           genre: str = "pop",
                           stem_file_path: str = "",
                           position: str = "solo",
                           fx_name: str = "") -> None:
        """Apply EQ to *track_index* / *fx_index* for the given *role*.

        When *stem_file_path* points to a readable WAV file the full
        spectrum-driven pipeline is used::

            SpectrumAnalyzer → EqIntent → translator → FxManager

        The translator is chosen based on *fx_name*:
        - ``SSLEQ`` → :func:`_apply_ssleq_eq`
        - Everything else → :func:`_apply_proq3_eq`

        *position* ("pre" / "post" / "solo") controls which rules fire
        (see :func:`_derive_eq_intent`).

        Otherwise falls back to the static :data:`_EQ_BASELINE` from
        :mod:`hermes_core.profiles`.
        """
        self._last_eq_params = {}

        log.debug(
            "EQ baseline for %s/%s/%s: stem_file_path=%r, exists=%s, "
            "fx_name=%r, position=%s",
            role, genre, "spectrum" if (stem_file_path and os.path.exists(stem_file_path)) else "static",
            stem_file_path or "",
            os.path.exists(stem_file_path) if stem_file_path else False,
            fx_name, position,
        )

        # ── Spectrum-driven EQ (happy path) ─────────────────
        if stem_file_path and os.path.exists(stem_file_path):
            try:
                report = SpectrumAnalyzer.analyze(stem_file_path)
                # Cache spectrum data so downstream FX (de-esser) can use it.
                self._last_spectrum = {
                    "presence_deficit": report.presence_deficit_db,
                    "air_level_db": report.air_level_db,
                    "sibilance_peak_hz": report.sibilance_peak_hz,
                    "mud_ratio": report.mud_ratio_db,
                }
                log.info(
                    "Spectrum analysis: tilt=%.1f dB/oct, mud=%.1f dB, "
                    "presence_deficit=%.1f dB, sib_peak=%.0f Hz, air=%.1f dB, "
                    "resonances=%d, bands=%s",
                    report.spectral_tilt_db_per_octave,
                    report.mud_ratio_db,
                    report.presence_deficit_db,
                    report.sibilance_peak_hz,
                    report.air_level_db,
                    len(report.resonances),
                    {k: v for k, v in report.band_energy_db.items()},
                )
                eq_intent = _derive_eq_intent(
                    report, role=role, genre=genre, position=position,
                )

                # Select translator based on FX
                is_ssl = "ssleq" in fx_name.lower()
                if is_ssl:
                    normalized = _apply_ssleq_eq(eq_intent)
                else:
                    normalized = _apply_proq3_eq(eq_intent)

                for pname, pval in normalized.items():
                    self._fx.set_param(track_index, fx_index, pname, pval)

                self._last_eq_params = normalized
                log.info(
                    "Auto-EQ (%s/%s/%s): %d bands @%s — %s",
                    role, genre, position, len(eq_intent.bands),
                    "SSLEQ" if is_ssl else "Pro-Q3",
                    ", ".join(b.reason for b in eq_intent.bands),
                )
                return
            except Exception as exc:
                log.warning(
                    "Spectrum-driven EQ failed (%s), falling back to baseline",
                    exc,
                )

        # ── Static baseline fallback ─────────────────────────
        bands = _EQ_BASELINE.get(role, [])
        if not bands:
            log.debug("EQ baseline: no baseline bands for role=%r, skipping", role)
            return

        log.info(
            "EQ baseline fallback (%s/%s/%s): %d bands — %s",
            role, genre, position, len(bands),
            [(b.get("type"), b.get("freq_hz"), b.get("gain_db", 0.0))
             for b in bands],
        )

        # Build a synthetic EqIntent so the same translators
        # (_apply_proq3_eq / _apply_ssleq_eq) handle normalisation.
        band_intents = []
        for b in bands:
            band_intents.append(EqBandIntent(
                band_type=b.get("type", "bell"),
                freq_hz=b.get("freq_hz", 1000.0),
                gain_db=b.get("gain_db", 0.0),
                q=b.get("q", 1.0),
                reason=f"baseline:{b.get('type','')}@{b.get('freq_hz',0):.0f}Hz",
            ))
        eq_intent = EqIntent(
            bands=band_intents,
            spectral_tilt="neutral",
            mud_detected=False,
        )

        is_ssl = "ssleq" in fx_name.lower()
        try:
            if is_ssl:
                normalized = _apply_ssleq_eq(eq_intent)
            else:
                normalized = _apply_proq3_eq(eq_intent)
            for pname, pval in normalized.items():
                self._fx.set_param(track_index, fx_index, pname, pval)
            self._last_eq_params = normalized
        except Exception as exc:
            log.warning("Baseline EQ apply failed: %s", exc)
            return

        log.info(
            "EQ baseline (%s/%s): %d bands applied",
            role, genre, len(bands),
        )

    def auto_corrective_eq(self, track_idx: int) -> dict:
        """基于频谱分析的共振检测自动生成校正性 EQ。

        分析轨道上的音频，检测共振频率，并自动设置 Pro-Q 3 的
        衰减频段来削减不需要的共振。

        返回包含应用频段和共振信息的诊断字典。

        spectrum.py 的共振检测结果直接反馈到 EQ 参数设置，
        闭环连接频谱分析与 EQ 调整。

        Returns
        -------
        dict
            ``{track_idx, eq_bands, resonance_count, applied}``
        """
        api = self._bridge.api
        track_ptr = api.GetTrack(0, track_idx)
        if not track_ptr:
            return {"track_idx": track_idx, "error": "Track not found",
                    "eq_bands": [], "applied": False}

        # 1. 获取轨道上的音频文件路径
        #    尝试从已有的 stems_cache 或通过渲染临时片段获取
        stem_file = ""
        for s in getattr(self, "_stems_cache", []):
            if s.get("track_index") == track_idx and s.get("success"):
                stem_file = s.get("file_path", "")
                break

        if not stem_file or not os.path.exists(stem_file):
            log.warning("auto_corrective_eq: no audio source for track %d", track_idx)
            return {"track_idx": track_idx, "error": "No audio source",
                    "eq_bands": [], "applied": False}

        # 2. 频谱分析 → 共振检测
        try:
            report = SpectrumAnalyzer.analyze(stem_file)
        except Exception as exc:
            log.warning("auto_corrective_eq: spectrum analysis failed — %s", exc)
            return {"track_idx": track_idx,
                    "error": f"Spectrum analysis failed: {exc}",
                    "eq_bands": [], "applied": False}

        # 3. 仅对非谐波共振（Q > 15）生成衰减频段
        eq_bands: list[dict] = []
        for resonance in report.resonances:
            if resonance.is_harmonic:
                continue  # 跳过音乐性的谐波
            if resonance.q_factor < 15.0:
                continue  # Q 太低不是真正共振
            cut_db = -min(resonance.prominence_db, 6.0)
            eq_bands.append({
                "freq": resonance.freq_hz,
                "gain": cut_db,
                "q": min(resonance.q_factor * 0.5, 10.0),
                "type": "bell",
                "reason": f"{resonance.freq_hz}Hz room mode "
                          f"Q={resonance.q_factor:.0f} "
                          f"prominence={resonance.prominence_db:.1f}dB",
            })

        if not eq_bands:
            log.info("auto_corrective_eq: no resonances detected on track %d",
                     track_idx)
            return {"track_idx": track_idx,
                    "resonance_count": len(report.resonances),
                    "eq_bands": [], "applied": False}

        # 4. 查找轨道上第一个可用的 EQ 插件并应用频段
        n_fx = api.TrackFX_GetCount(track_ptr)
        eq_fx_idx = -1
        for f in range(n_fx):
            ret, name_buf = api.TrackFX_GetFXName(track_ptr, f, "", 256)
            if isinstance(ret, (list, tuple)):
                name = ret[4] if len(ret) > 4 else ""
            else:
                name = name_buf or ""
            if "pro-q" in name.lower() or "reaeq" in name.lower():
                eq_fx_idx = f
                break

        if eq_fx_idx < 0:
            # 没有 EQ 插件 — 添加一个 ReaEQ
            eq_fx_idx = self._fx.add(track_idx, "ReaEQ (Cockos)")
            if eq_fx_idx < 0:
                log.warning("auto_corrective_eq: cannot add EQ plugin to track %d",
                            track_idx)
                return {"track_idx": track_idx,
                        "eq_bands": eq_bands,
                        "resonance_count": len(report.resonances),
                        "applied": False}

        # 5. 构建 EqIntent 并应用
        band_intents = []
        for b in eq_bands[:8]:  # 最多 8 个频段
            band_intents.append(EqBandIntent(
                band_type=b["type"],
                freq_hz=b["freq"],
                gain_db=b["gain"],
                q=b["q"],
                reason=b.get("reason", f"corrective:{b['freq']:.0f}Hz"),
            ))

        eq_intent = EqIntent(
            bands=band_intents,
            spectral_tilt="neutral",
            mud_detected=report.mud_ratio_db > 3.0,
        )
        normalized = _apply_proq3_eq(eq_intent)
        for pname, pval in normalized.items():
            self._fx.set_param(track_idx, eq_fx_idx, pname, pval)

        log.info(
            "auto_corrective_eq: track %d — %d corrective bands applied "
            "(out of %d detected resonances)",
            track_idx, len(eq_bands), len(report.resonances),
        )
        return {
            "track_idx": track_idx,
            "eq_bands": eq_bands,
            "resonance_count": len(report.resonances),
            "applied": True,
            "eq_fx_idx": eq_fx_idx,
        }

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

        REAPER's ``Main_SaveProjectEx`` can fail with NEWTEMP errors on
        paths with non-ASCII characters or restrictive macOS permissions.
        To guarantee headless reliability we ALWAYS save to a system temp
        directory and then copy the result to *output_dir* as a post-save
        step.  The temp directory is returned so the caller knows where
        REAPER actually writes.
        """
        os.makedirs(output_dir, exist_ok=True)

        target = os.path.join(output_dir, f"{name}.rpp")
        conflict = os.path.exists(target)
        if conflict:
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            target = os.path.join(output_dir, f"{name}_{ts}.rpp")
            log.info("Project file exists — renamed to %s", target)

        return target, conflict

    def create_project(self, name: str, output_dir: str = "",
                       sample_rate: int = 48000, *,
                       category: str = "", producer: str = "",
                       genre: str = "pop") -> dict:
        """Create a named project and save it without dialogs.

        If *output_dir* is empty, the project is placed under the configured
        project root (``~/REAPER 工程文件/`` by default), organised as::

            {project_root}/{category}/{name}/

        A ``.hermes_meta.json`` is created automatically and the global
        ``.hermes_index.json`` is updated.

        Returns ``{name, path, sample_rate, track_count, conflict_renamed,
        meta_dir}``.
        """
        if not output_dir:
            output_dir = str(make_project_path(name, category))
        self._meta_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        safe_path, conflict_renamed = self._safe_project_path(output_dir, name)

        # ── 创建标准子目录 ────────────────────────────────────
        create_project_dirs(output_dir)

        api = self._bridge.api

        # Safety: never delete tracks without explicit user consent.
        # If the project already has tracks (manually placed plugins, settings),
        # require opt-in.  Empty projects are safe to set up.
        existing_tracks = api.CountTracks(0)
        if self._tracks_protected and existing_tracks > 0:
            raise RuntimeError(
                f"Project has {existing_tracks} existing track(s). "
                "Deleting tracks is protected. Call eng.allow_track_deletion() "
                "first to confirm you want to wipe the project."
            )

        # Delete all tracks using raw API (reverse order to avoid index shifting).
        # Try both raw API and reapy's high-level API for reliability.
        n_tracks = api.CountTracks(0)
        for i in range(n_tracks - 1, -1, -1):
            tr = api.GetTrack(0, i)
            if tr:
                try:
                    api.DeleteTrack(tr)
                except Exception:
                    # Fallback to reapy's high-level API if raw API fails
                    try:
                        proj = self._bridge.rpr.Project()
                        if i < len(proj.tracks):
                            proj.tracks[i].delete()
                    except Exception as e:
                        log.warning("Failed to delete track %d: %s", i, e)

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

        # Save to a temp directory first — REAPER can fail with NEWTEMP
        # errors on paths with non-ASCII characters or restrictive macOS
        # sandbox permissions.  We always save to a known-safe temp dir,
        # then copy the result to the user's requested path.
        tmp_dir = tempfile.mkdtemp(prefix="hermes_proj_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(safe_path))
        api.Main_SaveProjectEx(0, tmp_path, 0)
        # 清除 REAPER 内部 dirty flag（避免退出弹窗）
        api.Main_SaveProjectEx(0, "", 0)
        try:
            shutil.copy2(tmp_path, safe_path)
            log.info("Project copied %s → %s", tmp_path, safe_path)
        except OSError:
            log.warning("Could not copy project to %s; using temp path", safe_path)
            safe_path = tmp_path
        self._project_path = safe_path
        # Snapshot REAPER's view of the project — later operations verify
        # the user has not manually switched to a different project.
        _, name_buf, _ = api.GetProjectName(0, "", 256)
        self._snapshot_project_name = name_buf or ""
        path_buf, _ = api.GetProjectPath("", 256)
        self._snapshot_project_path = path_buf or ""
        # Fresh project — clear all idempotency guards.
        self.reset()

        # ── 创建工程元数据 ────────────────────────────────────
        self._meta = ProjectMeta(
            name=name, category=category, producer=producer or None,
            genre=genre,
        )
        self._meta.save(output_dir)
        # 更新全局索引
        try:
            cfg = HermesConfig.load()
            idx = ProjectIndex.load(cfg.project_root_expanded)
            idx.add_or_update(
                str(Path(output_dir).relative_to(cfg.project_root_expanded)),
                self._meta, root_dir=cfg.project_root_expanded,
            )
        except Exception as exc:
            log.debug("Failed to update project index: %s", exc)

        # 设置审计日志的工程目录
        if self._audit is not None:
            self._audit.project_dir = output_dir

        self._record_audit(
            "create_project",
            {"name": name, "sample_rate": sample_rate, "genre": genre,
             "category": category, "producer": producer},
            f"path={safe_path} renamed={conflict_renamed}",
        )

        return {
            "name": name,
            "path": safe_path,
            "meta_dir": output_dir,
            "sample_rate": sample_rate,
            "track_count": 0,
            "conflict_renamed": conflict_renamed,
        }

    def _safe_save(self, target_path: str) -> str:
        """Save project via a temp dir to avoid REAPER NEWTEMP errors.

        REAPER's ``Main_SaveProjectEx`` can trigger modal "Error creating
        project file" dialogs on paths with non-ASCII characters or macOS
        sandbox restrictions.  We always save to a temp directory, then
        copy the result to *target_path*.
        """
        tmp_dir = tempfile.mkdtemp(prefix="hermes_save_")
        tmp_path = os.path.join(tmp_dir, os.path.basename(target_path))
        self._bridge.api.Main_SaveProjectEx(0, tmp_path, 0)
        self._bridge.api.Main_SaveProjectEx(0, "", 0)  # 清除 dirty flag
        try:
            shutil.copy2(tmp_path, target_path)
        except OSError:
            log.warning("Could not copy to %s; keeping temp path", target_path)
            return tmp_path
        return target_path

    # ── 管线阶段追踪 ────────────────────────────────────────

    def _mark_stage(self, stage: str) -> None:
        """标记管线阶段为已完成（如果 meta 存在）。

        同时自动更新生命周期状态（规范 §二）。
        """
        self._dirty = True
        self._stage_t0 = time.monotonic()  # 为下一阶段重置计时
        if self._meta is not None:
            self._meta.mark_stage(stage)
            self._meta.update_lifecycle()

    def _record_audit(self, operation: str, params: dict,
                      result_summary: str, duration_ms: float = 0.0,
                      success: bool = True) -> None:
        """记录审计条目（当 AuditLogger 已配置时）。

        Parameters
        ----------
        operation : str
            操作名称，如 ``"create_project"``。
        params : dict
            操作参数（会被防御性拷贝）。
        result_summary : str
            结果摘要文本。
        duration_ms : float
            操作耗时（毫秒）。为 0 时自动从 _stage_t0 计算。
        success : bool
            操作是否成功。
        """
        if self._audit is None:
            return
        if duration_ms <= 0 and self._stage_t0 > 0:
            duration_ms = (time.monotonic() - self._stage_t0) * 1000.0
        try:
            self._audit.record(
                operation=operation,
                params=params,
                result_summary=result_summary,
                duration_ms=round(duration_ms, 1),
                success=success,
            )
        except Exception as exc:
            log.debug("审计记录失败（非致命）: %s", exc)

    @property
    def is_dirty(self) -> bool:
        """自上次 ``save_project()`` 以来是否有未保存的修改。"""
        return self._dirty

    # ── 元数据同步 ───────────────────────────────────────────

    def _sync_meta(self) -> None:
        """将当前工程状态同步到 ``self._meta``。

        在 save_project() 前自动调用，确保元数据始终是最新的。
        """
        if self._meta is None:
            return
        # 轨道信息
        try:
            tracks = self.list_tracks()
            self._meta.track_count = len(tracks)
            vocal_track = None
            for t in tracks:
                chain = self.get_fx_chain(t.index)
                fx_names = [fx["name"] for fx in chain] if chain else []
                if t.index == 0:
                    self._meta.vocal_fx = fx_names
                    vocal_track = t
                elif t.index == 1 and fx_names:
                    self._meta.backing_fx = fx_names
        except Exception as exc:
            log.debug("_sync_meta: failed to read tracks — %s", exc)

        # 空间总线信息
        if hasattr(self, "_reverb_send_node") and self._reverb_send_node:
            try:
                self._meta.spatial_buses = {
                    "reverb": {
                        "level_db": self._reverb_send_node.params.get("level_db"),
                        "aux_index": self._reverb_send_node.params.get("aux_index"),
                    }
                }
            except Exception:
                pass

    def save_project(self) -> dict:
        """保存工程并同步元数据。

        在保存 ``.rpp`` 之前自动调用 :meth:`_sync_meta` 刷新状态快照，
        然后将 ``.hermes_meta.json`` 一并写入工程目录。
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        self._sync_meta()
        actual = self._safe_save(self._project_path)
        # 同步元数据到磁盘
        if self._meta and self._meta_dir:
            try:
                self._meta.save(self._meta_dir)
            except Exception as exc:
                log.debug("Failed to save meta: %s", exc)
        self._dirty = False
        return {"path": actual, "saved_at": datetime.now().isoformat()}

    def save_project_as(self, new_name: str) -> dict:
        """另存为一个新的工程名称（在同一目录下）。

        不修改当前 ``_project_path`` — 相当于导出一个副本。
        """
        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )
        self._sync_meta()
        proj_dir = os.path.dirname(self._project_path)
        new_path = os.path.join(proj_dir, f"{new_name}.rpp")
        # 如果目标已存在，追加时间戳
        if os.path.exists(new_path):
            ts = datetime.now().strftime("%Y-%m-%d_%H-%M")
            new_path = os.path.join(proj_dir, f"{new_name}_{ts}.rpp")
        actual = self._safe_save(new_path)
        # 同步元数据
        if self._meta and self._meta_dir:
            self._meta.name = new_name
            try:
                self._meta.save(self._meta_dir)
            except Exception as exc:
                log.debug("Failed to save meta: %s", exc)
        self._dirty = False
        return {"path": actual, "original_path": self._project_path,
                "saved_at": datetime.now().isoformat()}

    def archive_project(self, output_path: str | None = None) -> str:
        """将工程打包为可交付的 ZIP 归档文件。

        包含：
        - ProjectName.rpp（工程文件）
        - Audio/（工程目录下的所有音频文件）
        - Renders/（渲染输出目录，如果存在）
        - .hermes_meta.json（元数据）
        - mix_report.json（混音报告）

        Parameters
        ----------
        output_path : str | None
            输出 ZIP 路径，默认在工程目录下以 ``{project_name}_archive.zip`` 命名。

        Returns
        -------
        str
            ZIP 归档文件的绝对路径。
        """
        import json
        import zipfile

        if not self._project_path:
            raise RuntimeError(
                "No project path — call create_project(name, output_dir) first"
            )

        proj_dir = os.path.dirname(self._project_path)
        proj_name = os.path.splitext(os.path.basename(self._project_path))[0]

        if output_path is None:
            output_path = os.path.join(proj_dir, f"{proj_name}_archive.zip")

        # 保存当前工程状态
        self._sync_meta()
        self.save_project()

        # ── 收集归档文件 ──
        archive_files: list[tuple[str, str]] = []  # (absolute_path, arcname)

        # 1. 工程文件 (.rpp)
        archive_files.append((self._project_path, f"{proj_name}.rpp"))

        # 2. 元数据
        meta_path = os.path.join(proj_dir, ".hermes_meta.json")
        if os.path.exists(meta_path):
            archive_files.append((meta_path, ".hermes_meta.json"))

        # 3. Audio/ 目录
        audio_dir = os.path.join(proj_dir, "Audio")
        if os.path.isdir(audio_dir):
            for root, _dirs, files in os.walk(audio_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.join("Audio", os.path.relpath(fpath, audio_dir))
                    archive_files.append((fpath, arcname))

        # 4. Renders/ 目录
        renders_dir = os.path.join(proj_dir, "Renders")
        if os.path.isdir(renders_dir):
            for root, _dirs, files in os.walk(renders_dir):
                for fname in files:
                    fpath = os.path.join(root, fname)
                    arcname = os.path.join("Renders", os.path.relpath(fpath, renders_dir))
                    archive_files.append((fpath, arcname))

        # 5. 混音报告
        report_path = os.path.join(proj_dir, "mix_report.json")
        report_data = self._build_mix_report()
        with open(report_path, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, ensure_ascii=False)
        archive_files.append((report_path, "mix_report.json"))

        # ── 创建 ZIP ──
        os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for abs_path, arcname in archive_files:
                if os.path.exists(abs_path):
                    zf.write(abs_path, arcname)

        count = len([p for p, _ in archive_files if os.path.exists(p)])
        log.info(
            "Project archived: %s (%d files, %s bytes)",
            output_path, count,
            os.path.getsize(output_path) if os.path.exists(output_path) else 0,
        )
        return output_path

    def _build_mix_report(self) -> dict:
        """生成混音报告字典，用于 archive_project。

        报告包含工程元数据、轨道信息、FX 链、LUFS 数据等摘要。
        """
        report: dict = {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "project": {},
            "tracks": [],
            "mastering": {},
        }

        # 工程信息
        try:
            info = self.get_project_info()
            report["project"] = {
                "name": info.get("name", ""),
                "sample_rate": info.get("sample_rate", 0),
                "track_count": info.get("track_count", 0),
            }
        except Exception:
            pass

        # 元数据
        if self._meta:
            report["project"]["genre"] = self._meta.genre
            report["project"]["category"] = self._meta.category
            report["project"]["producer"] = self._meta.producer
            report["project"]["stages"] = self._meta.stages

        # 轨道信息
        try:
            for t in self.list_tracks():
                chain = self.get_fx_chain(t.index)
                report["tracks"].append({
                    "index": t.index,
                    "name": t.name,
                    "volume_db": round(t.volume_db, 1) if t.volume_db else 0.0,
                    "fx_count": len(chain) if chain else 0,
                    "fx_names": [fx["name"] for fx in chain] if chain else [],
                })
        except Exception:
            pass

        # 母带信息
        report["mastering"]["pipeline_state"] = self._pipeline_state.value
        if hasattr(self, "_reverb_send_node") and self._reverb_send_node:
            report["mastering"]["reverb"] = {
                "level_db": self._reverb_send_node.params.get("level_db"),
            }

        return report

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

        actual = self._safe_save(checkpoint_path)
        # 记录 checkpoint 到元数据
        if self._meta:
            self._meta.checkpoints.append({
                "label": label or ts,
                "path": os.path.basename(actual),
                "timestamp": datetime.now().isoformat(timespec="seconds"),
            })
        return {"checkpoint_path": actual, "main_path": self._project_path}

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

    # ── 工程关闭 ──────────────────────────────────────────

    def close_project(self, save: bool = True) -> dict:
        """保存并清理当前工程，不弹窗。

        REAPER API 的 Main_SaveProject 不清除内部 dirty flag，
        所以不能调用 Close/Quit action（会触发保存弹窗）。

        替代方案：保存到磁盘 → 删除所有轨道 → 重置 Master。
        效果等同于关闭后打开空白工程。

        返回 ``{saved, project_path}``。
        """
        api = self._bridge.api
        result = {"saved": False,
                  "project_path": getattr(self, "_project_path", "")}

        if save:
            self.save_project()
            result["saved"] = True

        # 删除轨道 + 重置 Master（等效关闭）
        n = api.CountTracks(0)
        for i in range(n - 1, -1, -1):
            tr = api.GetTrack(0, i)
            if tr:
                api.DeleteTrack(tr)

        master = api.GetMasterTrack(0)
        if master:
            n_fx = api.TrackFX_GetCount(master)
            for i in range(n_fx - 1, -1, -1):
                api.TrackFX_Delete(master, i)
            api.SetMediaTrackInfo_Value(master, "D_VOL", 1.0)

        log.info("Project cleaned: %s", result["project_path"])
        return result

    def safe_quit(self) -> dict:
        """保存 → 退出 REAPER。

        1. 保存到磁盘
        2. 启动 DialogKiller（捕获退出弹窗 Sheet）
        3. 发送 quit → DialogKiller 自动点 No
        """
        self.save_project()

        # 启动 DialogKiller，等它就绪
        self._bridge._dialog_killer.start()
        import time
        time.sleep(1.0)

        # 发送退出命令（会触发 Sheet 弹窗，DialogKiller 处理）
        import subprocess
        subprocess.run(
            ["osascript", "-e", 'tell application "REAPER" to quit'],
            capture_output=True, timeout=20,
        )

        self._bridge._dialog_killer.stop()
        log.info("REAPER quit — %d dialogs handled",
                 self._bridge._dialog_killer.killed_count if hasattr(
                     self._bridge._dialog_killer, "killed_count") else 0)
        return {"saved": True}

    # ── 插件预检查 ──────────────────────────────────────────

    def preflight_plugins(self,
                          required: list[str] | None = None) -> dict[str, bool]:
        """验证所需插件是否在 REAPER 中可用。

        在临时轨道上尝试加载每个插件，加载成功后立即删除。
        规范 §八：预检查数据库，发现缺失即进入 Error State。

        Parameters
        ----------
        required : list[str] | None
            需要检查的插件名列表。为 None 时检查所有空间插件
            （从 _SPATIAL_PLUGIN 提取）。

        Returns
        -------
        dict
            ``{"plugin_name": True/False, ...}``
        """
        if required is None:
            required = []
            for candidates in _SPATIAL_PLUGIN.values():
                for c in candidates:
                    if c not in required:
                        required.append(c)

        api = self._bridge.api
        # 创建临时轨道
        api.InsertTrackAtIndex(0, True)
        tmp_track = api.GetTrack(0, 0)

        result: dict[str, bool] = {}
        for name in required:
            idx = api.TrackFX_AddByName(tmp_track, name, False, 1)
            ok = idx >= 0
            result[name] = ok
            if ok:
                api.TrackFX_Delete(tmp_track, idx)
            else:
                log.warning("preflight: plugin MISSING — %s", name)

        # 清理临时轨道
        api.DeleteTrack(tmp_track)

        missing = [k for k, v in result.items() if not v]
        if missing:
            log.error(
                "preflight: %d/%d plugins MISSING: %s",
                len(missing), len(required), missing,
            )
        else:
            log.info("preflight: all %d plugins OK", len(required))

        return result

    def import_stems(self, file_paths: list[str],
                    position: float = 0.0) -> list[dict]:
        """Import audio files, creating one track per file named by basename.

        Returns list of {name, track_index, file_path, success}.
        """
        return self._gain_staging.import_stems(file_paths, position)

    def list_tracks(self) -> list[TrackInfo]:
        """Return TrackInfo for all tracks in the project."""
        return self._tracks.list_all()

    # ── Scene 3: Gain staging ────────────────────────────

    def apply_gain(self, track_index: int, gain_db: float,
                   target: str = "track_fader"):
        """Apply a gain change to a track.

        target: "track_fader" | "clip_gain" | "master_fader"
        """
        self._gain_staging.apply_gain(track_index, gain_db, target)

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
        bpm: float | None = None,
        vocal_indices: list[int] | None = None,
        backing_indices: list[int] | None = None,
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
        self._require_state(PipelineState.CREATED)
        self._ensure_project_match()

        # Store BPM for downstream use (apply_profile / _build_audio_chain).
        self._bpm = bpm

        def _do_prepare():
            return self._gain_staging.prepare(
                stem_paths, genre=genre, vocal_indices=vocal_indices,
                backing_indices=backing_indices,
            )

        result = self._undo_block("Prepare Stems", _do_prepare)
        self._stems_gain_staged = True
        self._stems_cache = result.get("stems", [])
        self._transition_to(PipelineState.STEMS_PREPARED)
        self._auto_save()
        self._mark_stage("prepare_stems")
        stems = result.get("stems", [])
        self._record_audit(
            "prepare_stems",
            {"genre": genre, "stem_count": len(stem_paths),
             "vocal_count": len(vocal_indices) if vocal_indices else 1,
             "backing_count": len(backing_indices) if backing_indices else 1},
            f"imported={sum(1 for s in stems if s.get('success'))}/{len(stems)}",
        )
        return result

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
                except Exception as e:
                    log.debug("Failed to get solo state for track %d: %s", i, e)
                    solo = 0.0
                saved[i] = bool(solo)
                api.SetMediaTrackInfo_Value(tr, "I_SOLO", 1.0 if i in indices else 0.0)

        try:
            result = self.render_mix(output_dir, verify=False, _internal=True)
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
        tmp_dir: str | None = None,
    ) -> dict:
        """Measure post-FX LUFS, set fader balance, enforce peak ceiling.

        **Must be called after** :meth:`apply_profile`.

        1. Solo-render vocal + backing → measure post-FX LUFS.
        2. Set faders so backing sits *ratio* LU below vocal (genre-based).
        3. Render full mix → measure peak.
        4. If peak > :data:`_PEAK_CEILING_DB`, attenuate both equally.

        Returns balance metadata plus combined LUFS and peak.
        """

        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_balance_")

        # Deep copy to avoid mutating the cached stem data
        stems = [dict(s) for s in self._stems_cache]
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

        # ── Step 1: ratio-based fader balance ──
        balance_info = self._gain_staging._balance_faders(
            stems,
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
            genre=genre,
        )

        # ── Step 2: full-mix render → peak check ──
        combined_lufs = None
        combined_peak = None
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
                    combined_peak = ana.peak_db
                except (OSError, ValueError, RuntimeError):
                    pass

        # ── Step 3: peak ceiling — scale both down if peak > -3 ──
        atten_db = 0.0
        if combined_peak is not None and combined_peak > _PEAK_CEILING_DB:
            atten_db = _PEAK_CEILING_DB - combined_peak  # negative
            for i, s in enumerate(stems):
                if not s.get("success") or s.get("track_index") is None:
                    continue
                if i in vocal_indices or i in backing_indices:
                    # Accumulate — ratio faders were already set by _balance_faders.
                    current_fader = s.get("fader_gain_db", 0.0)
                    new_fader = current_fader + atten_db
                    self.apply_gain(s["track_index"], new_fader)
                    s["fader_gain_db"] = round(new_fader, 1)
            combined_peak = _PEAK_CEILING_DB
            if combined_lufs is not None:
                combined_lufs = combined_lufs + atten_db

            log.info(
                "Peak ceiling: peak=%.1f dB → attenuated %.1f dB to hit %.1f dB",
                combined_peak - atten_db, atten_db, _PEAK_CEILING_DB,
            )

        log.info(
            "Post-FX balance: vocal=%.1f LUFS, backing=%.1f LUFS, "
            "combined=%.1f LUFS, peak=%.1f dB, ratio=%.1f LU",
            vocal_lufs or float("nan"),
            backing_lufs or float("nan"),
            combined_lufs or float("nan"),
            combined_peak or float("nan"),
            balance_info["ratio_lu"],
        )

        # ── Build reverb wet cache for preview mode ──
        reverb_wet_path = self._cache_reverb_wet(tmp)

        # ── Compute spatial send levels ────────────────────────
        # Uses signal analysis already collected during
        # prepare_stems (crest factor) and _build_audio_chain
        # (spectrum data) to derive genre-aware reverb/delay
        # send levels.  The sends are not created here — only
        # computed and returned for downstream use.
        vocal_stem = self._stems_cache[0] if self._stems_cache else {}
        crest_db = (
            vocal_stem.get("raw_peak_db", -3.0)
            - vocal_stem.get("raw_rms_db", -18.0)
        )
        spectrum = getattr(self, "_last_spectrum", {}) or {}
        spatial_sends = _compute_spatial_sends(
            genre=genre,
            crest_factor_db=crest_db,
            presence_deficit_db=spectrum.get("presence_deficit", 2.0),
            mud_ratio_db=spectrum.get("mud_ratio", -3.0),
            sibilance_peak_db=spectrum.get("sibilance_peak_hz"),
            section="verse",
        )

        self._mark_stage("post_fx_balance")
        self._record_audit(
            "post_fx_balance",
            {"genre": genre, "ratio_lu": balance_info.get("ratio_lu")},
            f"vocal={vocal_lufs}LUFS backing={backing_lufs}LUFS "
            f"combined={combined_lufs}LUFS peak={combined_peak}dB",
        )
        return {
            **balance_info,
            "vocal_lufs": vocal_lufs,
            "backing_lufs": backing_lufs,
            "combined_lufs": combined_lufs,
            "combined_peak_db": combined_peak,
            "peak_atten_db": atten_db,
            "reverb_wet_cache": reverb_wet_path,
            "stems": stems,
            "spatial_sends": spatial_sends,
        }

    def apply_backing_processing(
        self,
        backing_track_idx: int | None = None,
        vocal_track_idx: int = 0,
        genre: str = "pop",
    ) -> dict:
        """可选的伴奏后处理 — 总线压缩 + 频率互让。

        在 ``apply_profile`` 之后调用，为伴奏轨添加 glue compression，
        并在人声和伴奏之间协调 3kHz 频率区间。

        Parameters
        ----------
        backing_track_idx : int | None
            伴奏轨索引。为 None 时取第一个非人声轨道。
        vocal_track_idx : int
            人声轨索引，默认 0。
        genre : str
            流派名，用于选择压缩预设。

        Returns
        -------
        dict
            ``{"glue_compression": {...}, "frequency_pocket": {...}}``
        """
        from hermes_core.backing import BackingProcessor

        if backing_track_idx is None:
            # 取第一个非人声轨道
            backing_track_idx = 1

        processor = BackingProcessor(self._bridge, self._fx)

        glue_result = processor.apply_glue_compression(
            track_idx=backing_track_idx, genre=genre,
        )

        pocket_result = processor.apply_frequency_pocket(
            vocal_idx=vocal_track_idx,
            backing_idx=backing_track_idx,
        )

        return {
            "glue_compression": glue_result,
            "frequency_pocket": pocket_result,
        }

    def apply_bus_compressor(
        self,
        bpm: float | None = None,
        genre: str = "pop",
    ) -> dict:
        """Apply bx_townhouse bus compressor to the master track.

        Pipeline step between ``post_fx_balance`` and manual mastering.
        The automation chain::

            1. Probe-render to measure the mix peak after fader balance.
            2. Compute threshold, attack, and makeup from genre + BPM.
            3. Add bx_townhouse to the master track, set all parameters.

        Returns a diagnostic dict with *peak_db*, *thresh_db*, *attack_ms*,
        *makeup_db*, and *gr_target*.
        """

        # ── 1. Probe render — measure what hits the master bus ──
        tmp_dir = tempfile.mkdtemp(prefix="hermes_bus_probe_")
        probe = self.render_mix(tmp_dir, verify=True, _internal=True)
        signal = probe.get("signal_check", {})
        peak_db = signal.get("peak_db", -6.0)
        if signal.get("error"):
            log.warning("Bus compressor probe failed: %s — using peak=%.1f dB",
                        signal["error"], peak_db)

        # ── 2. Compute parameters ──
        physical = compute_bus_compressor_params(
            peak_db=peak_db, bpm=bpm, genre=genre,
        )
        target_gr_db = physical.pop("_target_gr", 2.0)

        # ── 3. Add bx_townhouse to master ──
        fx_idx = self.add_master_fx(
            "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"
        )
        if fx_idx < 0:
            log.error("Failed to add bx_townhouse to master track")
            return {
                "peak_db": peak_db,
                "thresh_db": physical.get("Thresh", 0),
                "attack_ms": physical.get("Attack", 30),
                "makeup_db": physical.get("MakeUp", 1.0),
                "gr_target": target_gr_db,
                "error": "fx_add_failed",
            }

        # ── 4. Normalise and apply ──
        plugin_name = "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"
        try:
            normalized = normalize_params(plugin_name, physical)
        except Exception as exc:
            log.error("Failed to normalise bus compressor params: %s", exc)
            return {
                "peak_db": peak_db,
                "thresh_db": physical.get("Thresh", 0),
                "attack_ms": physical.get("Attack", 30),
                "makeup_db": physical.get("MakeUp", 1.0),
                "gr_target": target_gr_db,
                "error": "normalise_failed",
            }

        for param_name, norm_value in normalized.items():
            self._fx.set_param(-1, fx_idx, param_name, norm_value)

        log.info(
            "Bus compressor: peak=%.1f dB → thresh=%.1f dB, "
            "attack=%.1f ms, makeup=%.1f dB, target GR=%.1f dB",
            peak_db,
            physical["Thresh"],
            physical["Attack"],
            physical["MakeUp"],
            target_gr_db,
        )

        self._mark_stage("apply_bus_compressor")
        return {
            "peak_db": peak_db,
            "thresh_db": physical["Thresh"],
            "attack_ms": physical["Attack"],
            "makeup_db": physical["MakeUp"],
            "gr_target": target_gr_db,
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
        self._require_state(PipelineState.CREATED, PipelineState.STEMS_PREPARED,
                            PipelineState.FX_APPLIED)
        result = self._fx.add(track_index, fx_name)
        if self._pipeline_state not in (PipelineState.FX_APPLIED, PipelineState.SPATIAL_APPLIED,
                                         PipelineState.MASTERED, PipelineState.RENDERED):
            self._transition_to(PipelineState.FX_APPLIED)
        self._auto_save()
        self._dirty = True
        return result

    def get_fx_chain(self, track_index: int) -> list[dict]:
        """Return all FX on a track."""
        return self._fx.get_chain(track_index)

    def add_master_fx(self, fx_name: str) -> int:
        """Add an effect plugin to the master track. Returns FX index."""
        return self._fx.add_master(fx_name)

    def _check_cache_validity(self, track_idx: int, fx_idx: int) -> bool:
        """检查缓存的 FX 参数是否仍然有效。

        通过比较 REAPER 中实际的参数值与缓存值来检测外部修改。
        当用户在 REAPER 界面中手动调整了参数时，缓存将变为过时。

        使用 :meth:`hermes_core.fx.FxManager.get_param_list` 读取 REAPER
        当前的归一化参数值，与引擎中最后一次写入的缓存值逐项比对。

        Args:
            track_idx: 轨道索引（-1 表示 Master 轨道）
            fx_idx: FX 插件索引

        Returns:
            True 表示缓存仍然有效，False 表示检测到外部修改
        """
        # 1. 读取 REAPER 中当前的实际参数值
        try:
            actual_params = self._fx.get_param_list(track_idx, fx_idx)
        except Exception as exc:
            log.debug("_check_cache_validity: failed to read params — %s", exc)
            return False

        if not actual_params:
            # 无参数可读，视为有效（插件可能没有暴露参数）
            return True

        # 2. 获取缓存的参数值
        # 优先从 DAG 节点中查找匹配的缓存
        cached_params: dict[str, float] | None = None
        all_nodes = self._vocal_chain_nodes + self._backing_chain_nodes
        for node in all_nodes:
            node_track = node.params.get("_track_idx") if isinstance(node.params, dict) else None
            node_fx = node.params.get("_fx_idx") if isinstance(node.params, dict) else None
            if node_track == track_idx and node_fx == fx_idx:
                cached_params = {k: v for k, v in node.params.items()
                                if not k.startswith("_")}
                break

        if cached_params is None:
            # 尝试 _last_eq_params（EQ 专用缓存）
            cached_params = getattr(self, "_last_eq_params", None)

        if not cached_params:
            log.debug(
                "_check_cache_validity: no cached params for track %d fx %d",
                track_idx, fx_idx,
            )
            return True  # 无缓存则无比较基准，视为有效

        # 3. 构建 REAPER 实际值查找表（按参数名索引）
        actual_by_name: dict[str, float] = {}
        for p in actual_params:
            name = p.get("name", "")
            if name:
                actual_by_name[name.lower()] = float(p.get("value", 0.0))

        # 4. 逐项比对缓存值与 REAPER 实际值
        mismatch_count = 0
        compared_count = 0
        for param_name, cached_value in cached_params.items():
            if param_name.startswith("_"):
                continue  # 跳过元数据字段
            actual_value = actual_by_name.get(param_name.lower())
            if actual_value is None:
                continue  # REAPER 中没有对应名称的参数，跳过
            compared_count += 1
            # 浮点比较，容差 0.005（归一化值域 0.0–1.0，约 0.5% 差异）
            if abs(actual_value - float(cached_value)) > 0.005:
                mismatch_count += 1
                log.debug(
                    "_check_cache_validity: mismatch on '%s' — "
                    "cached=%.4f actual=%.4f (delta=%.4f)",
                    param_name, float(cached_value), actual_value,
                    abs(actual_value - float(cached_value)),
                )

        if mismatch_count > 0:
            log.warning(
                "_check_cache_validity: track %d fx %d — %d/%d params mismatch "
                "(external modification detected)",
                track_idx, fx_idx, mismatch_count, compared_count,
            )
            return False

        return True

    def check_all_fx_cache(self) -> dict[str, bool]:
        """检查所有轨道的所有 FX 缓存有效性。

        遍历所有已缓存的 FX 参数，逐一与 REAPER 实际值比对，
        返回每个 FX 位置的缓存状态字典。

        Returns:
            ``{"track{N}_fx{M}": True/False, ...}`` 字典，
            True 表示缓存有效，False 表示检测到外部修改
        """
        result: dict[str, bool] = {}
        api = self._bridge.api
        n_tracks = api.CountTracks(0)

        # 检查 Master 轨道 (index=-1)
        master_ptr = api.GetMasterTrack(0)
        if master_ptr:
            n_fx = api.TrackFX_GetCount(master_ptr)
            for fx_idx in range(n_fx):
                key = f"track-1_fx{fx_idx}"
                result[key] = self._check_cache_validity(-1, fx_idx)

        for track_idx in range(n_tracks):
            track_ptr = api.GetTrack(0, track_idx)
            if not track_ptr:
                continue
            n_fx = api.TrackFX_GetCount(track_ptr)
            for fx_idx in range(n_fx):
                key = f"track{track_idx}_fx{fx_idx}"
                result[key] = self._check_cache_validity(track_idx, fx_idx)

        total = len(result)
        valid = sum(1 for v in result.values() if v)
        log.info(
            "check_all_fx_cache: %d/%d FX positions valid",
            valid, total,
        )
        return result

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
        self._require_state(PipelineState.CREATED, PipelineState.FX_APPLIED,
                            PipelineState.SPATIAL_APPLIED)

        aux_idx = self._tracks.create(name="Verb Return")

        # Abbey Road safety EQ — de-mud + de-ess the reverb input
        abbey_eq_idx = self._fx.add(aux_idx, "ReaEQ (Cockos)")
        if abbey_eq_idx >= 0:
            self._apply_abbey_road_eq(aux_idx, abbey_eq_idx)

        fx_idx = self._fx.add(aux_idx, reverb_fx)

        send_info = self._send.create(
            src=src_track, dest=aux_idx, level_db=level_db, mode=mode
        )

        self._transition_to(PipelineState.SPATIAL_APPLIED)
        self._dirty = True
        return {
            "aux_index": aux_idx,
            "send": send_info,
            "fx_index": fx_idx,
            "abbey_eq_index": abbey_eq_idx,
        }

    def _apply_abbey_road_eq(self, aux_track: int, eq_fx_idx: int) -> None:
        """Configure ReaEQ as an Abbey Road safety filter.

        Band 1: HPF @ 600 Hz (removes low-end mud from reverb).
        Band 2: LPF @ 10 kHz (removes sibilance / harshness).

        These parameters are **not exposed to the Agent** — they are
        an engine-level safeguard applied automatically to every
        reverb send.

        ReaEQ 参数（来自 PLUGIN_REGISTRY 的 normalize.py 条目）：
        - Band n Freq:   20–20000 Hz, linear
        - Band n Gain:   -24–24 dB, linear
        - Band n Q:      0.01–10, linear
        - Band n Type:   0=HP, 1=Low Shelf, 2=Bell, 3=High Shelf, 4=LP
        - Band n Enabled: 0=off, 1=on
        """
        # 使用 normalize_params 写入归一化的 ReaEQ 参数
        physical = {
            # Band 1: HPF @ 600 Hz
            "Band 1 Type":    0.0,       # HPF
            "Band 1 Freq":    600.0,     # Hz
            "Band 1 Gain":    0.0,       # 不适用（HPF 无增益）
            "Band 1 Q":       0.7,       # 标准 12dB/oct 斜率
            "Band 1 Enabled": 1.0,
            # Band 2: LPF @ 10 kHz
            "Band 2 Type":    4.0,       # LPF
            "Band 2 Freq":    10000.0,   # Hz
            "Band 2 Gain":    0.0,
            "Band 2 Q":       0.7,
            "Band 2 Enabled": 1.0,
            # Band 3-4: 不启用
            "Band 3 Enabled": 0.0,
            "Band 4 Enabled": 0.0,
        }
        try:
            normalized = normalize_params("ReaEQ (Cockos)", physical)
            for pname, pval in normalized.items():
                self._fx.set_param(aux_track, eq_fx_idx, pname, pval)
            log.debug(
                "Abbey Road EQ applied: HPF@600Hz + LPF@10kHz on aux %d slot %d",
                aux_track, eq_fx_idx,
            )
        except Exception as exc:
            log.warning(
                "Abbey Road EQ failed on aux %d slot %d: %s — "
                "reverb may have excess mud/sibilance",
                aux_track, eq_fx_idx, exc,
            )

    # ── 空间效果器链 ──────────────────────────────────────────

    def _resolve_spatial_plugin_key(self, fx_name: str) -> str | None:
        """将 REAPER 返回的插件名匹配到 PLUGIN_REGISTRY 键。

        先用子串匹配查找，失败后用 _SPATIAL_PARAM_FALLBACK_MAP 的键匹配。
        返回 PLUGIN_REGISTRY 的键名，找不到返回 None。
        """
        # 精确匹配
        if fx_name in PLUGIN_REGISTRY:
            return fx_name
        # 子串匹配（如 "VST3: EchoBoy (Soundtoys)" 匹配 PLUGIN_REGISTRY 键）
        name_lower = fx_name.lower()
        for key in PLUGIN_REGISTRY:
            if key.lower() in name_lower or name_lower in key.lower():
                return key
        # 回退映射键匹配（如 "ValhallaPlate" 匹配 "ValhallaPlate (Valhalla DSP, LLC)"）
        for fallback_key in _SPATIAL_PARAM_FALLBACK_MAP:
            if fallback_key.lower() in name_lower:
                return fallback_key
        return None

    def _apply_spatial_params(
        self, aux_track: int, fx_idx: int, loaded_name: str,
        bus: str, genre: str, bpm: float | None = None,
    ) -> None:
        """对流派空间插件应用预设参数。

        1. 从 _GENRE_SPATIAL_PARAMS[genre][bus] 获取归一化参数
        2. 将 REAPER 返回的插件名匹配到 PLUGIN_REGISTRY
        3. 如果是回退插件，通过 _SPATIAL_PARAM_FALLBACK_MAP 转换参数名
        4. 通过 FxManager.set_param() 应用
        5. 特殊处理：音符值（如 "1/4"）需要 BPM 转换
        """
        genre_params = _GENRE_SPATIAL_PARAMS.get(
            genre, _GENRE_SPATIAL_PARAMS["pop"],
        )
        bus_params = genre_params.get(bus)
        if not bus_params:
            return  # 该流派/总线无预设参数

        registry_key = self._resolve_spatial_plugin_key(loaded_name)
        if registry_key is None:
            log.debug(
                "_apply_spatial_params: plugin '%s' not in PLUGIN_REGISTRY "
                "— skipping param application", loaded_name,
            )
            return

        # 判断是否为回退插件（非首选插件）
        primary_candidates = _SPATIAL_PLUGIN.get(bus, [])
        is_fallback = (
            len(primary_candidates) > 0
            and not any(
                c.lower() in loaded_name.lower()
                for c in primary_candidates[:1]
            )
        )

        # 如果是回退插件，加载参数名映射
        fallback_map: dict[str, str] = {}
        if is_fallback:
            for fk in _SPATIAL_PARAM_FALLBACK_MAP:
                if fk.lower() in loaded_name.lower():
                    fallback_map = _SPATIAL_PARAM_FALLBACK_MAP[fk]
                    log.info(
                        "Using fallback param map for %s → %s (%d mappings)",
                        bus, fk, len(fallback_map),
                    )
                    break

        applied = 0
        skipped = 0
        for pname, pval in bus_params.items():
            # 如果是回退插件，先查映射表
            actual_pname = fallback_map.get(pname, pname) if fallback_map else pname

            # 检查参数是否在 PLUGIN_REGISTRY 的该插件条目中
            plugin_entry = PLUGIN_REGISTRY.get(registry_key, {})
            plugin_params = plugin_entry.get("params", {})
            if actual_pname not in plugin_params:
                skipped += 1
                continue

            ok = self._fx.set_param(aux_track, fx_idx, actual_pname, pval)
            if ok:
                applied += 1
            else:
                skipped += 1

        if applied > 0 or skipped > 0:
            log.info(
                "Spatial params (%s/%s/%s): %d applied, %d skipped",
                genre, bus, loaded_name, applied, skipped,
            )

    def _apply_return_eq(
        self, aux_track: int, eq_fx_idx: int, bus: str, genre: str,
    ) -> None:
        """Configure Pro-Q 3 as a return-track safety filter.

        Band 1: HPF — removes low-end mud from reverb/delay.
        Band 2: LPF — tames sibilance and harshness in the tail.

        Frequencies are genre- and bus-aware via :data:`_GENRE_RETURN_EQ`.
        """
        eq_defaults = _GENRE_RETURN_EQ.get(genre, _GENRE_RETURN_EQ["pop"])
        # Delay buses share the "delay" EQ entry; reverb buses use their
        # specific type ("plate" / "hall" / "room").
        eq_key = "delay" if bus in _DELAY_BUS_TYPES else bus
        eq_cfg = eq_defaults.get(eq_key, {"hpf": 300, "lpf": 8000})

        hpf_hz = eq_cfg["hpf"]
        lpf_hz = eq_cfg["lpf"]

        # Build a minimal EqIntent: just HPF + LPF, no gain bands.
        eq_intent = EqIntent(
            bands=[
                EqBandIntent(
                    band_type="hp", freq_hz=hpf_hz, gain_db=0.0,
                    q=1.0, reason=f"Return {bus} HPF @ {hpf_hz:.0f} Hz",
                ),
                EqBandIntent(
                    band_type="lp", freq_hz=lpf_hz, gain_db=0.0,
                    q=1.0, reason=f"Return {bus} LPF @ {lpf_hz:.0f} Hz",
                ),
            ],
            spectral_tilt="neutral",
            mud_detected=False,
        )
        normalized = _apply_proq3_eq(eq_intent)
        for pname, pval in normalized.items():
            self._fx.set_param(aux_track, eq_fx_idx, pname, pval)

        log.debug(
            "Return EQ: %s bus on aux %d — HPF=%.0f Hz, LPF=%.0f Hz (genre=%s)",
            bus, aux_track, hpf_hz, lpf_hz, genre,
        )

    def build_spatial_chain(
        self, vocal_track: int, spatial_sends: dict, genre: str = "pop",
        bpm: float | None = None,
    ) -> dict:
        """Create reverb and delay return tracks with sends from the vocal.

        Uses the send levels computed by :func:`_compute_spatial_sends`
        (via ``post_fx_balance``).  Buses whose send level is ``None``
        are skipped — no track or plugin is created for them.

        Each return track gets:
        1. FabFilter Pro-Q 3 as a safety HPF+LPF filter
        2. A genre-appropriate reverb or delay plugin
        3. [NEW] Genre-specific spatial parameters applied
        4. A post-fader send from the vocal track

        Parameters
        ----------
        vocal_track : int
            Index of the vocal track to send from.
        spatial_sends : dict
            Send levels computed by :func:`_compute_spatial_sends`.
        genre : str
            Genre key for parameter lookup.
        bpm : float | None
            Project tempo — used for musical note-to-ms conversion
            in delay plugins.  Defaults to 120 BPM when None.

        Returns a dict mapping bus keys to their track/send/fx indices.
        """

        result: dict[str, dict] = {}

        # Order matters: create reverbs first, then delays.
        bus_order = ["plate", "hall", "room", "slap", "rhythm"]

        for bus in bus_order:
            send_key = f"delay_{bus}" if bus in _DELAY_BUS_TYPES else f"reverb_{bus}"
            level_db = spatial_sends.get(send_key)

            # None = disabled for this genre — skip entirely.
            if level_db is None:
                continue

            bus_name = _SPATIAL_BUS_NAMES.get(bus, f"{bus} Return")
            plugin_names = _SPATIAL_PLUGIN.get(bus, [])
            if not plugin_names:
                log.warning("build_spatial_chain: no plugin mapped for bus=%s", bus)
                continue

            # 1. Create return track
            aux_idx = self._tracks.create(name=bus_name)

            # 2. Pro-Q 3 safety EQ (HPF + LPF)
            eq_idx = self._fx.add(aux_idx, "FabFilter Pro-Q 3")
            if eq_idx >= 0:
                self._apply_return_eq(aux_idx, eq_idx, bus, genre)

            # 3. Spatial plugin — try each candidate until one loads
            fx_idx = -1
            loaded_name = ""
            for candidate in plugin_names:
                fx_idx = self._fx.add(aux_idx, candidate)
                if fx_idx >= 0:
                    loaded_name = candidate
                    break
            if fx_idx < 0:
                log.warning(
                    "build_spatial_chain: failed to load any plugin for "
                    "bus=%s (tried %s)", bus, plugin_names,
                )
            else:
                # 3a. Query REAPER for the actual plugin name
                #     (TrackFX_AddByName may have resolved a short name
                #      to the full VST3/VST name)
                track_ptr = self._bridge.api.GetTrack(0, aux_idx)
                raw_name = self._bridge.api.TrackFX_GetFXName(
                    track_ptr, fx_idx, "", 256,
                )
                actual_name = _extract_reaper_string(raw_name) or loaded_name

                # 3b. Apply genre-specific spatial parameters
                self._apply_spatial_params(
                    aux_idx, fx_idx, actual_name, bus, genre, bpm,
                )

            # 4. Create send from vocal track
            send_info = self._send.create(
                src=vocal_track, dest=aux_idx, level_db=level_db,
            )

            result[send_key] = {
                "aux_index": aux_idx,
                "eq_index": eq_idx,
                "fx_index": fx_idx,
                "send": send_info,
                "send_level_db": level_db,
            }

            log.info(
                "Spatial chain: %s → aux %d [%s + %s], send=%.1f dB (genre=%s)",
                bus_name, aux_idx, "Pro-Q 3", loaded_name or "?", level_db, genre,
            )

        self._mark_stage("build_spatial_chain")
        return result

    # ── 大师空间模板 ──────────────────────────────────────────

    def apply_master_template(
        self, master_name: str, vocal_track: int,
        genre: str = "pop", bpm: float | None = None,
    ) -> dict:
        """调度大师空间模板。

        Parameters
        ----------
        master_name : str
            模板名。大小写不敏感。支持完整名称或缩写:
            ``"cla"`` / ``"chris lord-alge"``,
            ``"hewitt"`` / ``"ryan hewitt"``,
            ``"serban"`` / ``"serban ghenea"``,
            ``"townsend"`` / ``"devin townsend"``.
        vocal_track : int
            人声轨索引。
        genre : str
            流派键，用于回退参数。
        bpm : float | None
            工程速度，延迟音符值需要。

        Returns
        -------
        dict
            模板结果，格式因模板而异。

        Raises
        ------
        ValueError
            未知模板名。
        """
        name_lower = master_name.lower().replace(" ", "_")
        dispatch = {
            "cla": self._master_cla,
            "chris_lord-alge": self._master_cla,
            "hewitt": self._master_hewitt,
            "ryan_hewitt": self._master_hewitt,
            "serban": self._master_serban,
            "serban_ghenea": self._master_serban,
            "townsend": self._master_townsend,
            "devin_townsend": self._master_townsend,
        }
        method = dispatch.get(name_lower)
        if method is None:
            available = ["cla", "hewitt", "serban", "townsend"]
            raise ValueError(
                f"未知大师模板 '{master_name}'。可用: {available}"
            )
        log.info("应用大师模板: %s", master_name)
        result = method(vocal_track, genre, bpm)
        self._mark_stage("build_spatial_chain")
        return result

    def _master_cla(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master A: Chris Lord-Alge — 延迟送入混响。

        3 条延迟 + 3 条混响，延迟输出送入混响产生光泽尾音。
        """
        api = self._bridge.api
        result: dict = {"delays": {}, "reverbs": {}, "cross_sends": []}

        # ── 延迟总线 ──────────────────────────────────────
        delay_specs = [
            {
                "key": "slap", "name": "CLA Slap",
                "time_val": 0.05, "feedback": 0.10,
                "lowcut": 0.12, "mode": 0.0,  # Echoplex mode=0
            },
            {
                "key": "throw", "name": "CLA Throw",
                "time_val": 0.08, "feedback": 0.15,
                "lowcut": 0.12, "mode": 0.0,
            },
            {
                "key": "tape", "name": "CLA Tape",
                "time_val": 0.04, "feedback": 0.20,
                "lowcut": 0.12, "highcut": 0.40,  # SpaceEcho LPF ~3kHz
                "mode": 0.3,
            },
        ]
        delay_tracks: list[int] = []
        for ds in delay_specs:
            aux = self._tracks.create(name=ds["name"])
            delay_tracks.append(aux)
            # Pro-Q 3 HPF
            eq_idx = self._fx.add(aux, "FabFilter Pro-Q 3")
            if eq_idx >= 0:
                hpf_intent = {
                    "bands": [{"band_type": "hp", "freq_hz": 200,
                               "gain_db": 0.0, "q": 0.71, "reason": "CLA HPF 200Hz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                try:
                    normed = _apply_proq3_eq(hpf_intent)
                    for pn, pv in normed.items():
                        self._fx.set_param(aux, eq_idx, pn, pv)
                except Exception:
                    pass

            # EchoBoy
            eb_idx = self._fx.add(aux, "EchoBoy")
            if eb_idx >= 0:
                eb_params = {
                    "Echo1Time": ds["time_val"], "Feedback": ds["feedback"],
                    "Mix": 1.0, "LowCut": ds.get("lowcut", 0.12),
                    "Saturation": 0.15,
                }
                if "highcut" in ds:
                    eb_params["HighCut"] = ds["highcut"]
                for pn, pv in eb_params.items():
                    self._fx.set_param(aux, eb_idx, pn, pv)

            # 发送
            send_info = self._send.create(
                src=vocal_track, dest=aux, level_db=-15.0,
            )
            result["delays"][ds["key"]] = {
                "aux_index": aux, "fx_index": eb_idx, "send": send_info,
            }

        # ── 混响总线 ──────────────────────────────────────
        reverb_specs = [
            {"key": "plate", "name": "CLA Plate", "plugin": "Little Plate",
             "params": {"Decay": 0.32, "Mix": 1.0, "Low Cut": 0.15}},
            {"key": "room", "name": "CLA Room", "plugin": "ValhallaRoom",
             "params": {"decay": 0.18, "mix": 1.0, "predelay": 0.05}},
            {"key": "hall", "name": "CLA Hall", "plugin": "LX480",
             "params": {
                 "E1: Reverb Time Mid (RTM)": 0.32,
                 "E1: Pre Delay (PDL)": 0.15,
                 "E1: Mix (MIX)": 1.0,
             }},
        ]
        reverb_tracks: list[int] = []
        for rs in reverb_specs:
            aux = self._tracks.create(name=rs["name"])
            reverb_tracks.append(aux)
            # Pro-Q 3 HPF 250Hz
            eq_idx = self._fx.add(aux, "FabFilter Pro-Q 3")
            if eq_idx >= 0:
                try:
                    hpf_intent = {
                        "bands": [{"band_type": "hp", "freq_hz": 250,
                                   "gain_db": 0.0, "q": 0.71, "reason": "CLA HPF 250Hz"}],
                        "spectral_tilt": "neutral", "mud_detected": False,
                    }
                    normed = _apply_proq3_eq(hpf_intent)
                    for pn, pv in normed.items():
                        self._fx.set_param(aux, eq_idx, pn, pv)
                except Exception:
                    pass

            # 混响插件
            rv_idx = self._fx.add(aux, rs["plugin"])
            if rv_idx >= 0:
                for pn, pv in rs["params"].items():
                    self._fx.set_param(aux, rv_idx, pn, pv)

            # 发送
            send_info = self._send.create(
                src=vocal_track, dest=aux, level_db=-14.0,
            )
            result["reverbs"][rs["key"]] = {
                "aux_index": aux, "fx_index": rv_idx, "send": send_info,
            }

        # ── 跨发送: 延迟 → 混响（CLA 秘方）────────────────
        for dt in delay_tracks:
            for rvt in reverb_tracks:
                try:
                    si = self._send.create(src=dt, dest=rvt, level_db=-8.0)
                    result["cross_sends"].append({
                        "src": dt, "dest": rvt, "level_db": -8.0,
                    })
                except Exception as exc:
                    log.debug("CLA cross-send failed: %s", exc)

        log.info(
            "CLA template: %d delays + %d reverbs + %d cross-sends",
            len(delay_tracks), len(reverb_tracks), len(result["cross_sends"]),
        )
        return result

    def _master_hewitt(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master B: Ryan Hewitt — 三层 EMT 140 板混响。

        不同 Pre-Delay 创造「立体声→单声道崩塌」效果。
        优先使用 UAD EMT 140，回退到 ValhallaPlate。
        """
        result: dict = {"plates": {}}
        plate_specs = [
            {
                "key": "plate_1_mono", "name": "HP Plate 1 (Mono)",
                "PreDly": 0.50, "DampA": 0.60, "DampB": 0.55,
                "Width": 0.0, "LowCut": 0.12,  # HPF 180Hz
                "send_db": -14.0,
            },
            {
                "key": "plate_2_stereo", "name": "HP Plate 2 (Stereo)",
                "PreDly": 0.13, "DampA": 0.55, "DampB": 0.50,
                "Width": 0.50, "LowCut": 0.17,  # HPF 250Hz
                "send_db": -13.0,
            },
            {
                "key": "plate_3_wide", "name": "HP Plate 3 (Wide)",
                "PreDly": 0.13, "DampA": 0.50, "DampB": 0.45,
                "Width": 1.0, "LowCut": 0.12,  # HPF 180Hz
                "send_db": -12.0,
            },
        ]
        for ps in plate_specs:
            aux = self._tracks.create(name=ps["name"])
            # Pro-Q 3 HPF
            eq_idx = self._fx.add(aux, "FabFilter Pro-Q 3")
            hpf_hz = 180 if "plate_1" in ps["key"] or "plate_3" in ps["key"] else 250
            if eq_idx >= 0:
                try:
                    hpf_intent = {
                        "bands": [{"band_type": "hp", "freq_hz": hpf_hz,
                                   "gain_db": 0.0, "q": 0.71,
                                   "reason": f"Hewitt HPF {hpf_hz}Hz"}],
                        "spectral_tilt": "neutral", "mud_detected": False,
                    }
                    normed = _apply_proq3_eq(hpf_intent)
                    for pn, pv in normed.items():
                        self._fx.set_param(aux, eq_idx, pn, pv)
                except Exception:
                    pass

            # 优先 UAD EMT 140，回退 ValhallaPlate
            plate_idx = self._fx.add(aux, "UAD EMT 140")
            if plate_idx < 0:
                plate_idx = self._fx.add(aux, "ValhallaPlate")
                # ValhallaPlate 参数名不同
                if plate_idx >= 0:
                    vp_params = {
                        "Decay": 0.40, "PreDelay": ps["PreDly"],
                        "Size": 0.40, "Width": ps["Width"],
                        "Type": 0.3, "Mix": 1.0,
                    }
                    for pn, pv in vp_params.items():
                        self._fx.set_param(aux, plate_idx, pn, pv)
            else:
                # UAD EMT 140 参数
                uad_params = {
                    "PreDly": ps["PreDly"], "Width": ps["Width"],
                    "Mix": 1.0, "LowCut": ps["LowCut"],
                    "DampA": ps.get("DampA", 0.55),
                    "DampB": ps.get("DampB", 0.50),
                }
                for pn, pv in uad_params.items():
                    self._fx.set_param(aux, plate_idx, pn, pv)

            send_info = self._send.create(
                src=vocal_track, dest=aux, level_db=ps["send_db"],
            )
            result["plates"][ps["key"]] = {
                "aux_index": aux, "fx_index": plate_idx, "send": send_info,
            }

        log.info("Hewitt template: 3 plates (UAD EMT 140 preferred)")
        return result

    def _master_serban(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master C: Serban Ghenea — 干净透明的 Sidechain Ducking 空间。

        5 条标准返回轨，每条挂 Pro-C 2 侧链压缩（人声触发）。
        注意：Sidechain 路由需要 REAPER 通道 3/4 接线，当前版本
        仅添加 Pro-C 2 并设置参数，sidechain 接线需手动完成。
        """
        result: dict = {"buses": {}}
        bus_specs = [
            {"key": "plate", "name": "SG Plate", "plugin": "FabFilter Pro-R",
             "params": {"Decay Rate": 0.35, "Mix": 1.0, "Predelay": 0.12,
                        "Brightness": 0.55, "Character": 0.40},
             "send_db": -12.0},
            {"key": "hall", "name": "SG Hall", "plugin": "LX480",
             "params": {
                 "E1: Reverb Time Mid (RTM)": 0.32,
                 "E1: Pre Delay (PDL)": 0.22,
                 "E1: Mix (MIX)": 1.0,
             }, "send_db": -14.0},
            {"key": "room", "name": "SG Room", "plugin": "ValhallaRoom",
             "params": {"decay": 0.10, "mix": 1.0, "predelay": 0.05},
             "send_db": -16.0},
            {"key": "slap", "name": "SG Slap", "plugin": "EchoBoy",
             "params": {"Echo1Time": 0.05, "Feedback": 0.10,
                        "Mix": 1.0, "Saturation": 0.10, "LowCut": 0.12},
             "send_db": -14.0},
            {"key": "rhythm", "name": "SG Rhythm", "plugin": "EchoBoy",
             "params": {"RhythmNote": 0.30, "Feedback": 0.20,
                        "Mix": 1.0, "Saturation": 0.10, "LowCut": 0.12},
             "send_db": -16.0},
        ]
        for bs in bus_specs:
            aux = self._tracks.create(name=bs["name"])
            # Pro-Q 3
            eq_idx = self._fx.add(aux, "FabFilter Pro-Q 3")
            if eq_idx >= 0:
                self._apply_return_eq(aux, eq_idx, bs["key"], genre)

            # 空间插件
            fx_idx = self._fx.add(aux, bs["plugin"])
            if fx_idx >= 0:
                for pn, pv in bs["params"].items():
                    self._fx.set_param(aux, fx_idx, pn, pv)

            # Sidechain 压缩: Pro-C 2
            # 注意：通道 3/4 接线需要手动设置
            sc_idx = self._fx.add(aux, "FabFilter Pro-C 2")
            if sc_idx >= 0:
                sc_params = {
                    "Threshold": 0.35, "Ratio": 0.15,  # 2:1
                    "Attack": 0.05, "Release": 0.25,
                    "Knee": 0.10, "Range": 0.10,  # ~5dB max GR
                    "Makeup Gain": 0.0,
                }
                for pn, pv in sc_params.items():
                    self._fx.set_param(aux, sc_idx, pn, pv)
                log.info(
                    "Serban sidechain: Pro-C 2 on '%s' — "
                    "手动设置通道 3/4 接线以完成 sidechain 路由", bs["name"],
                )

            send_info = self._send.create(
                src=vocal_track, dest=aux, level_db=bs["send_db"],
            )
            result["buses"][bs["key"]] = {
                "aux_index": aux, "fx_index": fx_idx, "send": send_info,
                "sidechain_fx": sc_idx,
            }

        log.info("Serban template: 5 buses + sidechain compression")
        return result

    def _master_townsend(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master D: Devin Townsend — 不对称延迟 + 廉价混响粘合。

        左右延迟不同时间 + 高 Feedback 产生雾状空间，
        Little Plate 粘合整体，Pro-Q 3 激进 EQ 过滤。
        """
        result: dict = {}

        # ── L Delay (EchoBoy SpaceEcho, 300ms, FB 40%, 硬左) ──
        l_aux = self._tracks.create(name="DT L Delay")
        l_eq = self._fx.add(l_aux, "FabFilter Pro-Q 3")
        if l_eq >= 0:
            try:
                intent = {
                    "bands": [{"band_type": "hp", "freq_hz": 400,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT HPF 400Hz"},
                              {"band_type": "lp", "freq_hz": 3000,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT LPF 3kHz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                normed = _apply_proq3_eq(intent)
                for pn, pv in normed.items():
                    self._fx.set_param(l_aux, l_eq, pn, pv)
            except Exception:
                pass
        l_eb = self._fx.add(l_aux, "EchoBoy")
        if l_eb >= 0:
            for pn, pv in {
                "Echo1Time": 0.12, "Feedback": 0.40, "Mix": 1.0,
                "Saturation": 0.25, "LowCut": 0.18,
            }.items():
                self._fx.set_param(l_aux, l_eb, pn, pv)
        l_send = self._send.create(src=vocal_track, dest=l_aux, level_db=-12.0)
        # 硬左声像
        self._send.set_pan(vocal_track, l_send.get("index", 0), -1.0)
        result["left_delay"] = {"aux_index": l_aux, "send": l_send, "pan": -1.0}

        # ── R Delay (EchoBoy SpaceEcho, 500ms, FB 40%, 硬右) ──
        r_aux = self._tracks.create(name="DT R Delay")
        r_eq = self._fx.add(r_aux, "FabFilter Pro-Q 3")
        if r_eq >= 0:
            try:
                intent = {
                    "bands": [{"band_type": "hp", "freq_hz": 400,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT HPF 400Hz"},
                              {"band_type": "lp", "freq_hz": 3000,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT LPF 3kHz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                normed = _apply_proq3_eq(intent)
                for pn, pv in normed.items():
                    self._fx.set_param(r_aux, r_eq, pn, pv)
            except Exception:
                pass
        r_eb = self._fx.add(r_aux, "EchoBoy")
        if r_eb >= 0:
            for pn, pv in {
                "Echo1Time": 0.18, "Feedback": 0.40, "Mix": 1.0,
                "Saturation": 0.25, "LowCut": 0.18,
            }.items():
                self._fx.set_param(r_aux, r_eb, pn, pv)
        r_send = self._send.create(src=vocal_track, dest=r_aux, level_db=-12.0)
        self._send.set_pan(vocal_track, r_send.get("index", 0), 1.0)
        result["right_delay"] = {"aux_index": r_aux, "send": r_send, "pan": 1.0}

        # ── Glue Verb (Little Plate 1.5s + 激进 Post-EQ) ──
        g_aux = self._tracks.create(name="DT Glue Verb")
        g_eq = self._fx.add(g_aux, "FabFilter Pro-Q 3")
        if g_eq >= 0:
            try:
                intent = {
                    "bands": [{"band_type": "hp", "freq_hz": 400,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT HPF 400Hz"},
                              {"band_type": "lp", "freq_hz": 3000,
                               "gain_db": 0.0, "q": 0.71, "reason": "DT LPF 3kHz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                normed = _apply_proq3_eq(intent)
                for pn, pv in normed.items():
                    self._fx.set_param(g_aux, g_eq, pn, pv)
            except Exception:
                pass
        g_fx = self._fx.add(g_aux, "Little Plate")
        if g_fx >= 0:
            for pn, pv in {"Decay": 0.25, "Mix": 1.0, "Low Cut": 0.18}.items():
                self._fx.set_param(g_aux, g_fx, pn, pv)
        g_send = self._send.create(src=vocal_track, dest=g_aux, level_db=-10.0)
        result["glue_reverb"] = {
            "aux_index": g_aux, "fx_index": g_fx, "send": g_send,
            "post_eq": {"hpf": 400, "lpf": 3000},
        }

        log.info("Townsend template: L/R delays + glue verb")
        return result

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
                   timeout: float = 120.0,
                   _internal: bool = False) -> dict:
        """Render project and optionally run signal analysis.

        Returns {output_path, signal_check, ...}.
        """
        # 仅公开调用时执行状态检查（内部探测渲染跳过）
        if not _internal:
            self._require_state(PipelineState.CREATED, PipelineState.MASTERED,
                                PipelineState.RENDERED)

        result = self._render.render_mix(
            output_dir=output_dir,
            bounds=bounds,
            fmt=fmt,
            sample_rate=sample_rate,
            timeout=timeout,
        )

        if not _internal and result.get("output_path"):
            self._transition_to(PipelineState.RENDERED)

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

        if not _internal and result.get("output_path"):
            sc = result.get("signal_check", {})
            self._record_audit(
                "render_mix",
                {"output_dir": output_dir, "format": fmt,
                 "sample_rate": sample_rate},
                f"path={result['output_path']} "
                f"lufs={sc.get('integrated_lufs')} "
                f"duration={sc.get('duration_sec')}s "
                f"clips={sc.get('clip_count', 0)}",
                success=sc.get("clip_passed", True),
            )

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

        # 委托给 MasteringEngine
        self._mastering._on_progress = on_progress
        result = self._undo_block(
            "Finalize Master",
            lambda: self._mastering.finalize(
                target_lufs,
                limiter_fx=limiter_fx,
                ceiling_db=ceiling_db,
                tolerance=tolerance,
                tmp_dir=tmp_dir,
            ),
        )
        if result.get("passed"):
            self._master_finalized = True
        self._transition_to(PipelineState.MASTERED)
        self._mark_stage("finalize_master")
        self._record_audit(
            "finalize_master",
            {"target_lufs": target_lufs, "ceiling_db": ceiling_db,
             "limiter": limiter_fx},
            f"passed={result.get('passed')} "
            f"achieved={result.get('achieved_lufs')}LUFS "
            f"gain={result.get('gain_db')}dB",
            success=result.get("passed", False),
        )
        return result

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
                os.path.join(tmp, "dry"), verify=False, _internal=True,
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
        pcm, sr = read_pcm(dry_path)
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
            except Exception as e:
                log.debug("Failed to clean up temp track: %s", e)

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
