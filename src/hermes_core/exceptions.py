"""
Hermes-Core exception hierarchy.

All project-specific exceptions inherit from ``HermesError`` so callers
can catch a single root type when they need to handle any known error.
"""


class HermesError(Exception):
    """Base for all hermes-core exceptions."""


class ConnectionError(HermesError):
    """REAPER bridge connection / reconnection failures."""


class TrackError(HermesError):
    """Track CRUD or property access failures."""


class RenderError(HermesError):
    """Render setup, execution, or output failures."""


class AnalysisError(HermesError):
    """Signal analysis failures (unsupported format, corrupt file, etc.)."""


class CalibrationError(HermesError):
    """Loudness calibration failures."""


class UnregisteredPluginError(HermesError):
    """Plugin not found in PLUGIN_REGISTRY — cannot normalise parameters."""


class UnregisteredParamError(HermesError):
    """Parameter name not registered for a known plugin."""
