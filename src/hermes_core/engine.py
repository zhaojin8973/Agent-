"""
MixingEngine — Layer 3 public API. Composes all Layer 2 modules into
a single entry point for Hermes acceptance scenarios.
"""

import logging
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
)
from hermes_core.normalize import normalize_params, compute_bus_compressor_params, PLUGIN_REGISTRY
from hermes_core.audio_utils import note_to_ms, read_pcm, numpy_mix
from hermes_core.profiles import (
    _resolve_fx_type,
    _get_compressor_preset,
    get_bpm_timing,
)
from hermes_core.dag import AudioNode, SendNode, ChainExecutor
from hermes_core.config import HermesConfig

# ── 从提取的子模块重新导出（向后兼容）──────────────────────────
from hermes_core.genre_tables import (
    # 引擎直接使用
    _DEFAULT_TARGET_LUFS,
    _PRO_L2_RANGE_DB,
    _GENRE_RVOX_MULTIPLIER,
    _GENRE_PRODS_RANGE,
    _GENRE_RETURN_EQ,  # 向后兼容重新导出（tests/test_engine.py 引用）
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
    # 向后兼容重新导出（测试中广泛引用）
    _GENRE_VOCAL_TO_BACKING,
    _PEAK_CEILING_DB,
    _GENRE_TARGET_LUFS,
    _CLIP_GAIN_REF_DB,
    _GENRE_CREST_GR_RATIO,
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
    apply_eq_rms_match as _apply_eq_rms_match_impl,
    apply_eq_baseline as _apply_eq_baseline_impl,
    auto_corrective_eq as _auto_corrective_eq_impl,
)
from hermes_core.mastering import MasteringEngine, _get_genre_target_lufs, _master_error, _friendly_hint  # noqa: F401（向后兼容重新导出）
from hermes_core.gain_staging import GainStagingEngine
from hermes_core.spatial_engine import (
    _compute_spatial_sends,
    _resolve_spatial_plugin_key,
    _apply_abbey_road_eq,
    _apply_spatial_params,
    _apply_return_eq,
)
from hermes_core.master_templates import (
    apply_master_template as _apply_master_template_impl,
    _master_cla as _master_cla_impl,
    _master_hewitt as _master_hewitt_impl,
    _master_serban as _master_serban_impl,
    _master_townsend as _master_townsend_impl,
    AVAILABLE_TEMPLATES,
)
from hermes_core.chain_renderer import (
    _micro_render_node as _micro_render_node_impl,
    _make_chain_executor as _make_chain_executor_impl,
    execute_chain as _execute_chain_impl,
    _init_translators,
)
from hermes_core.fx_builder import (
    FXBuildContext, build_fx_params, apply_params_to_track,
    _init_comp_translators,
)

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

        # ── 惰性初始化子模块翻译器 ────────────────────────────
        _init_translators()
        _init_comp_translators()

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

        参数推导委托到 :mod:`hermes_core.fx_builder` 中的策略函数。
        REAPER 交互（FX 添加、set_param）统一在此处理。
        """
        nodes: list[AudioNode] = []
        prev: AudioNode | None = None
        sd = stem_data.get(stem_idx, {})
        spectrum = getattr(self, "_last_spectrum", {}) or {}
        eq_params = getattr(self, "_last_eq_params", {}) or {}

        for i, fx in enumerate(fx_list):
            idx = self._fx.add(track_index, fx.name)
            fx_type = _resolve_fx_type(fx.name, fx.fx_type)

            node = AudioNode(
                name=f"{role}_{fx_type}_{i}_{fx.name}",
                fx_type=fx_type,
                params={},
            )
            node.is_dirty = False

            if prev:
                prev.add_downstream(node)
            nodes.append(node)

            # ── EQ 特殊处理：调用 _apply_eq_baseline ──
            if fx_type == "eq":
                file_path = sd.get("file_path", "")
                eq_position = fx.eq_position if hasattr(fx, "eq_position") else "solo"
                self._apply_eq_baseline(
                    track_index, idx, role,
                    genre=genre, stem_file_path=file_path,
                    position=eq_position, fx_name=fx.name,
                )
                if hasattr(self, "_last_eq_params"):
                    node.params = dict(self._last_eq_params)
                log.info("Added %s to track %d [%s]", fx.name, track_index, node.name)
                prev = node
                continue

            # ── 策略推导 → 参数应用到 REAPER ──
            ctx = FXBuildContext(
                fx_name=fx.name,
                fx_type=fx_type,
                role=role,
                genre=genre,
                bpm=bpm,
                raw_rms_db=sd.get("raw_rms_db"),
                raw_peak_db=sd.get("raw_peak_db"),
                stem_file_path=sd.get("file_path", ""),
                presence_deficit=spectrum.get("presence_deficit", 0.0),
                last_eq_params=dict(eq_params),
                eq_position=fx.eq_position if hasattr(fx, "eq_position") else "solo",
            )
            physical = apply_params_to_track(self._fx, track_index, idx, ctx)
            if physical is not None:
                node.params = physical
            else:
                # 通用回退：直接使用 fx.params
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
        """Apply EQ to *track_index* / *fx_index* for the given *role*.（委托到 eq_engine）"""
        self._last_eq_params = {}
        self._last_spectrum = getattr(self, "_last_spectrum", {}) or {}
        normalized = _apply_eq_baseline_impl(
            self._fx, track_index, fx_index, role,
            genre=genre, stem_file_path=stem_file_path,
            position=position, fx_name=fx_name,
            last_eq_params=self._last_eq_params,
            last_spectrum=self._last_spectrum,
        )
        if normalized is not None:
            self._last_eq_params = dict(normalized)

    def auto_corrective_eq(self, track_idx: int) -> dict:
        """基于频谱分析的共振检测自动生成校正性 EQ。（委托到 eq_engine）"""
        return _auto_corrective_eq_impl(
            self._bridge.api, self._fx, track_idx,
            getattr(self, "_stems_cache", []),
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

    # ── post_fx_balance 内部辅助 ────────────────────────────

    @staticmethod
    def _measure_group_lufs(
        solo_render_fn, tmp_dir: str, label: str,
        stem_idx_to_track: dict, indices: list[int],
    ) -> float | None:
        """独奏渲染一组轨道并测量 LUFS。"""
        tracks = [
            stem_idx_to_track[i] for i in indices
            if i in stem_idx_to_track
        ]
        if not tracks:
            return None
        result = solo_render_fn(tracks, os.path.join(tmp_dir, f"{label}_solo"), label)
        if result.get("output_path"):
            try:
                ana = SignalAnalyzer.analyze(result["output_path"])
                return ana.integrated_lufs
            except (OSError, ValueError, RuntimeError):
                pass
        return None

    @staticmethod
    def _enforce_peak_ceiling(
        stems: list[dict], combined_peak: float | None,
        vocal_indices: list[int], backing_indices: list[int],
        apply_gain_fn, peak_ceiling_db: float,
    ) -> tuple[float, float | None]:
        """峰值超限时等比衰减两组轨道，返回 (atten_db, new_peak)。"""
        atten_db = 0.0
        if combined_peak is not None and combined_peak > peak_ceiling_db:
            atten_db = peak_ceiling_db - combined_peak
            for i, s in enumerate(stems):
                if not s.get("success") or s.get("track_index") is None:
                    continue
                if i in vocal_indices or i in backing_indices:
                    current_fader = s.get("fader_gain_db", 0.0)
                    new_fader = current_fader + atten_db
                    apply_gain_fn(s["track_index"], new_fader)
                    s["fader_gain_db"] = round(new_fader, 1)
            combined_peak = peak_ceiling_db
            log.info(
                "Peak ceiling: peak=%.1f dB → attenuated %.1f dB to hit %.1f dB",
                combined_peak - atten_db, atten_db, peak_ceiling_db,
            )
        return atten_db, combined_peak

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

        # ── Solo-render vocal/backing groups ──
        vocal_lufs = self._measure_group_lufs(
            self._solo_render, tmp, "vocal", stem_idx_to_track, vocal_indices,
        )
        backing_lufs = self._measure_group_lufs(
            self._solo_render, tmp, "backing", stem_idx_to_track, backing_indices,
        )

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
        full_indices = vocal_indices + backing_indices
        combined_lufs = self._measure_group_lufs(
            self._solo_render, tmp, "full", stem_idx_to_track, full_indices,
        )
        combined_peak = None
        if combined_lufs is not None:
            full_tracks = [
                stem_idx_to_track[i] for i in full_indices
                if i in stem_idx_to_track
            ]
            if full_tracks:
                full_result = self._solo_render(
                    full_tracks, os.path.join(tmp, "full_mix"), "full",
                )
                if full_result.get("output_path"):
                    try:
                        ana = SignalAnalyzer.analyze(full_result["output_path"])
                        combined_peak = ana.peak_db
                    except (OSError, ValueError, RuntimeError):
                        pass

        # ── Step 3: peak ceiling enforce ──
        atten_db, combined_peak = self._enforce_peak_ceiling(
            stems, combined_peak, vocal_indices, backing_indices,
            self.apply_gain, _PEAK_CEILING_DB,
        )
        if combined_lufs is not None:
            combined_lufs = combined_lufs + atten_db

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
        """Configure ReaEQ as an Abbey Road safety filter.（委托到 spatial_engine）"""
        _apply_abbey_road_eq(self._fx, aux_track, eq_fx_idx)

    # ── 空间效果器链 ──────────────────────────────────────────

    def _resolve_spatial_plugin_key(self, fx_name: str) -> str | None:
        """将 REAPER 返回的插件名匹配到 PLUGIN_REGISTRY 键。（委托到 spatial_engine）"""
        return _resolve_spatial_plugin_key(fx_name)

    def _apply_spatial_params(
        self, aux_track: int, fx_idx: int, loaded_name: str,
        bus: str, genre: str, bpm: float | None = None,
    ) -> None:
        """对流派空间插件应用预设参数。（委托到 spatial_engine）"""
        _apply_spatial_params(self._fx, aux_track, fx_idx, loaded_name, bus, genre, bpm)

    def _apply_return_eq(
        self, aux_track: int, eq_fx_idx: int, bus: str, genre: str,
    ) -> None:
        """Configure Pro-Q 3 as a return-track safety filter.（委托到 spatial_engine）"""
        _apply_return_eq(self._fx, aux_track, eq_fx_idx, bus, genre)

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
        """调度大师空间模板。（委托到 master_templates）"""
        name_lower = master_name.lower().replace(" ", "_")
        if name_lower not in {
            "cla", "chris_lord-alge", "hewitt", "ryan_hewitt",
            "serban", "serban_ghenea", "townsend", "devin_townsend",
        }:
            raise ValueError(
                f"未知大师模板 '{master_name}'。可用: {AVAILABLE_TEMPLATES}"
            )
        log.info("应用大师模板: %s", master_name)
        result = _apply_master_template_impl(
            self._bridge, self._tracks, self._fx, self._send,
            master_name, vocal_track, genre, bpm,
        )
        self._mark_stage("build_spatial_chain")
        return result

    def _master_cla(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master A: Chris Lord-Alge — 延迟送入混响。（委托到 master_templates）"""
        return _master_cla_impl(
            self._bridge, self._tracks, self._fx, self._send,
            vocal_track, genre, bpm,
        )

    def _master_hewitt(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master B: Ryan Hewitt — 三层 EMT 140 板混响。（委托到 master_templates）"""
        return _master_hewitt_impl(
            self._bridge, self._tracks, self._fx, self._send,
            vocal_track, genre, bpm,
        )

    def _master_serban(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master C: Serban Ghenea — 干净透明的 Sidechain Ducking 空间。（委托到 master_templates）"""
        return _master_serban_impl(
            self._bridge, self._tracks, self._fx, self._send,
            vocal_track, genre, bpm,
        )

    def _master_townsend(
        self, vocal_track: int, genre: str, bpm: float | None,
    ) -> dict:
        """Master D: Devin Townsend — 不对称延迟 + 廉价混响粘合。（委托到 master_templates）"""
        return _master_townsend_impl(
            self._bridge, self._tracks, self._fx, self._send,
            vocal_track, genre, bpm,
        )

    def _apply_eq_rms_match(
        self, track_index: int, fx_index: int,
        pre_rms_db: float, post_rms_db: float,
    ) -> None:
        """Compensate EQ gain change so downstream nodes see consistent RMS.（委托到 eq_engine）"""
        _apply_eq_rms_match_impl(self._fx, track_index, fx_index, pre_rms_db, post_rms_db)

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
        """Mix dry + wet WAVs in numpy with *wet_level_db* gain on wet.（委托到 audio_utils）"""
        return numpy_mix(dry_path, wet_path, wet_level_db, output_path)

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
        """Render a single :class:`AudioNode` to a cached WAV.（委托到 chain_renderer）"""
        return _micro_render_node_impl(
            self._bridge, self._tracks, self._fx,
            self._solo_render, node, input_wav, cache_dir,
        )

    def _make_chain_executor(self, cache_dir: str) -> ChainExecutor:
        """Return a :class:`ChainExecutor` wired to :meth:`_micro_render_node`."""
        return _make_chain_executor_impl(
            lambda node, inp: self._micro_render_node(node, inp, cache_dir),
            cache_dir,
        )

    def execute_chain(self, nodes: list[AudioNode],
                      cache_dir: str | None = None) -> list[AudioNode]:
        """Execute *nodes* via micro-rendering, reusing cached outputs.（委托到 chain_renderer）"""
        return _execute_chain_impl(
            lambda cdir: lambda node, inp: self._micro_render_node(node, inp, cdir),
            nodes, cache_dir,
        )

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

    # ── 段落自动化 ────────────────────────────────────────────

    def apply_section_automation(
        self,
        sections: list,
        intents: list,
        use_presets: bool = False,
        param_kinds: list[str] | None = None,
    ) -> dict:
        """应用段落差异化参数自动化。

        为不同歌曲段落（主歌/副歌/桥段等）设置差异化的混音参数，
        通过 REAPER 自动化包络写入。

        Parameters
        ----------
        sections : list[SectionDef]
            歌曲段落结构（按时间顺序）。
        intents : list[AutomationIntent]
            显式参数自动化意图。与 *use_presets* 互斥。
        use_presets : bool
            是否使用内置段落参数预设。为 True 时忽略 *intents*。
        param_kinds : list[str] | None
            使用预设时的参数类型列表。
            None 表示全部：comp_ratio, eq_presence, reverb_level, threshold。

        Returns
        -------
        dict
            ``{written: int, skipped: int, errors: list[str]}``
        """
        from hermes_core.automation import AutomationManager
        mgr = AutomationManager(self)

        if use_presets:
            # 使用第一个 intent 的 track_idx，或默认 0
            track_idx = intents[0].track_idx if intents else 0
            return mgr.apply_preset(sections, track_idx, param_kinds)

        return mgr.apply(sections, intents)
