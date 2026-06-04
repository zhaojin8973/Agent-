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

__all__ = [
    "ReaperBridge",
    "DialogKiller",
    "DialogEvent",
    "TrackManager",
    "TrackInfo",
    "BusManager",
    "FolderInfo",
    "FxManager",
    "SendManager",
    "SendMode",
    "RenderManager",
    "RenderFormat",
    "SignalAnalyzer",
    "SignalReport",
    "MixingEngine",
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
    # Spectrum analysis
    "SpectrumAnalyzer",
    "SpectrumReport",
]
