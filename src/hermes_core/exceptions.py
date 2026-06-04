"""
Hermes-Core exception hierarchy.

All project-specific exceptions inherit from ``HermesError`` so callers
can catch a single root type when they need to handle any known error.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class HermesError(Exception):
    """Base for all hermes-core exceptions."""


class BridgeConnectionError(HermesError):
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


class InvalidStateError(HermesError):
    """状态机违规 — 在错误的引擎状态下调用了操作。"""


class SecurityError(HermesError):
    """安全违规 — 路径穿越、权限越界等安全相关错误。"""


class PluginNotFoundError(HermesError):
    """插件未找到 — 在 REAPER 中找不到指定的插件。

    与 UnregisteredPluginError 的区别：
    - UnregisteredPluginError：插件在注册表 (PLUGIN_REGISTRY) 中未注册
    - PluginNotFoundError：在 REAPER 实例中搜索不到该插件
    """


# ════════════════════════════════════════════════════════════════
# Result：统一操作结果类型
# ════════════════════════════════════════════════════════════════


@dataclass
class Result:
    """统一操作结果类型。所有公共 API 方法返回此类型。

    Examples
    --------
    >>> Result.ok({"track_id": 1})
    Result(success=True, data={'track_id': 1}, error=None, hint=None)

    >>> Result.fail("连接超时", hint="检查 REAPER 是否运行")
    Result(success=False, data=None, error='连接超时', hint='检查 REAPER 是否运行')
    """

    success: bool
    data: dict | None = None
    error: str | None = None
    hint: str | None = None

    @classmethod
    def ok(cls, data: dict | None = None) -> Result:
        """创建成功结果。

        Parameters
        ----------
        data : dict | None
            返回的数据负载。

        Returns
        -------
        Result
            成功结果实例。
        """
        return cls(success=True, data=data)

    @classmethod
    def fail(cls, error: str, hint: str | None = None) -> Result:
        """创建失败结果。

        Parameters
        ----------
        error : str
            错误描述。
        hint : str | None
            可选的修复提示。

        Returns
        -------
        Result
            失败结果实例。
        """
        return cls(success=False, error=error, hint=hint)
