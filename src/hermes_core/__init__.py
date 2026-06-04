# hermes-core: Lean 3-layer REAPER DAW automation engine

from hermes_core.bridge import ReaperBridge, DialogKiller, DialogEvent
from hermes_core.track import TrackManager, TrackInfo
from hermes_core.bus import BusManager, FolderInfo
from hermes_core.fx import FxManager
from hermes_core.send import SendManager, SendMode
from hermes_core.render import RenderManager, RenderFormat
from hermes_core.signal import SignalAnalyzer, SignalReport
from hermes_core.engine import MixingEngine
from hermes_core.profiles import MixingProfile, FXPreset
from hermes_core.exceptions import (
    HermesError,
    BridgeConnectionError,
    TrackError,
    RenderError,
    AnalysisError,
    CalibrationError,
    UnregisteredPluginError,
    UnregisteredParamError,
    InvalidStateError,
    SecurityError,
    PluginNotFoundError,
    Result,
)
from hermes_core.normalize import (
    PLUGIN_REGISTRY,
    normalize_param,
    normalize_params,
)
from hermes_core.loudness_optimizer import CompressionIntent, EqIntent, EqBandIntent
from hermes_core.dag import AudioNode, SendNode, ChainExecutor
from hermes_core.spectrum import SpectrumAnalyzer, SpectrumReport

# ── Agent Protocol ────────────────────────────────────────────
from hermes_core.agent_protocol import (
    HermesAgentAPI,
    MixRequest,
    MixGenre,
    MixResult,
    MixOptions,
    AdjustRequest,
    AdjustResult,
    AdjustmentType,
    StatusResult,
    AuditResult,
)

# ── 审计与安全 ────────────────────────────────────────────────
from hermes_core.audit import AuditLogger, AuditEntry
from hermes_core.security import PathSandbox, FileProtector, RateLimiter, DiskGuard, TempFileManager

# ── 配置与元数据 ──────────────────────────────────────────────
from hermes_core.config import HermesConfig
from hermes_core.project_meta import ProjectMeta, ProjectIndex, make_project_path, create_project_dirs

# ── 工程子系统 ─────────────────────────────────────────────────
from hermes_core.gain_staging import GainStagingEngine
from hermes_core.mastering import MasteringEngine, _get_genre_target_lufs
from hermes_core.backing import BackingProcessor
from hermes_core.reference import ReferenceMatcher, ReferenceProfile
from hermes_core.master_templates import apply_master_template, AVAILABLE_TEMPLATES
from hermes_core.chain_renderer import execute_chain, _make_chain_executor
from hermes_core.automation import (
    SectionDef, AutomationIntent, TrackAutomation,
    AutomationManager, make_pop_song_structure,
)

# ── 预览与进度 ─────────────────────────────────────────────────
from hermes_core.preview import PreviewRenderer, PreviewResult  # noqa: F811
from hermes_core.progress import ProgressReporter

# ── 插件注册表（函数式 API，避免 PLUGIN_REGISTRY 与 normalize 同名冲突）──
from hermes_core.plugin_registry import (
    resolve_plugin,
    get_plugin_name,
    get_spatial_plugins,
    list_all_plugins,
    list_all_spatial_plugin_names,
)

# ── 弹窗处理（跨平台抽象）─────────────────────────────────────
from hermes_core.dialog_handler import (
    DialogHandler, MacOSDialogHandler,
    WindowsDialogHandler, LinuxDialogHandler,
    create_dialog_handler,
)

__all__ = [
    # Bridge
    "ReaperBridge",
    "DialogKiller",
    "DialogEvent",
    # Track & Bus
    "TrackManager",
    "TrackInfo",
    "BusManager",
    "FolderInfo",
    # FX & Send
    "FxManager",
    "SendManager",
    "SendMode",
    # Render
    "RenderManager",
    "RenderFormat",
    # Signal & Spectrum
    "SignalAnalyzer",
    "SignalReport",
    "SpectrumAnalyzer",
    "SpectrumReport",
    # Engine
    "MixingEngine",
    # Profiles
    "MixingProfile",
    "FXPreset",
    # Exceptions
    "HermesError",
    "BridgeConnectionError",
    "TrackError",
    "RenderError",
    "AnalysisError",
    "CalibrationError",
    "UnregisteredPluginError",
    "UnregisteredParamError",
    "InvalidStateError",
    "SecurityError",
    "PluginNotFoundError",
    "Result",
    # Normalisation
    "PLUGIN_REGISTRY",
    "normalize_param",
    "normalize_params",
    # Compression & EQ intents
    "CompressionIntent",
    "EqIntent",
    "EqBandIntent",
    # DAG / AudioNode pipeline
    "AudioNode",
    "SendNode",
    "ChainExecutor",
    # Agent Protocol
    "HermesAgentAPI",
    "MixRequest",
    "MixGenre",
    "MixResult",
    "MixOptions",
    "AdjustRequest",
    "AdjustResult",
    "AdjustmentType",
    "StatusResult",
    "AuditResult",
    # Audit & Security
    "AuditLogger",
    "AuditEntry",
    "PathSandbox",
    "FileProtector",
    "RateLimiter",
    "DiskGuard",
    "TempFileManager",
    # Config & Metadata
    "HermesConfig",
    "ProjectMeta",
    "ProjectIndex",
    "make_project_path",
    "create_project_dirs",
    # Subsystems
    "GainStagingEngine",
    "MasteringEngine",
    "_get_genre_target_lufs",
    "BackingProcessor",
    "ReferenceMatcher",
    "ReferenceProfile",
    "apply_master_template",
    "AVAILABLE_TEMPLATES",
    "execute_chain",
    "_make_chain_executor",
    # Preview & Progress
    "PreviewRenderer",
    "PreviewResult",
    "ProgressReporter",
    # Plugin Registry (functional API)
    "resolve_plugin",
    "get_plugin_name",
    "get_spatial_plugins",
    "list_all_plugins",
    "list_all_spatial_plugin_names",
    # Dialog Handler
    "DialogHandler",
    "MacOSDialogHandler",
    "WindowsDialogHandler",
    "LinuxDialogHandler",
    "create_dialog_handler",
    # Automation
    "SectionDef",
    "AutomationIntent",
    "TrackAutomation",
    "AutomationManager",
    "make_pop_song_structure",
]
