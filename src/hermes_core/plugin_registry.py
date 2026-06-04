"""
统一插件注册表 — 所有插件名映射、优选链和回退链的单一数据源。

项目中所有对 REAPER 插件的引用都应通过此注册表获取名称，
而非在代码中硬编码插件字符串。

本模块从 normalize.py 导入现有的 PLUGIN_REGISTRY（参数规格注册表），
并在其上构建面向处理类别的更高层映射（类别 → 主选/回退插件名）。
"""

from __future__ import annotations

from hermes_core.normalize import PLUGIN_REGISTRY as _NORMALIZE_REGISTRY


# ════════════════════════════════════════════════════════════════
# 信号链插件分类映射：类别 → {主选, 回退}
#
# 键（类别）表示处理角色，值字典包含：
#   - primary  : 首选插件名（必须与 normalize.py 中的键一致）
#   - fallback : 回退插件名（或 None 表示无回退）
# ════════════════════════════════════════════════════════════════

PLUGIN_REGISTRY: dict[str, dict[str, str | None]] = {
    "eq_surgical": {
        "primary": "VST: FabFilter Pro-Q 3 (FabFilter)",
        "fallback": "ReaEQ (Cockos)",
    },
    "eq_color": {
        "primary": "VST3: SSLEQ Mono (Waves)",
        "fallback": "ReaEQ (Cockos)",
    },
    "compressor_peak": {
        "primary": "VST3: CLA-76 Mono (Waves)",
        "fallback": "FabFilter Pro-C 2 (FabFilter)",
    },
    "compressor_rms": {
        "primary": "VST3: RVox Mono (Waves)",
        "fallback": "FabFilter Pro-C 2 (FabFilter)",
    },
    "deesser": {
        "primary": "VST: FabFilter Pro-DS (FabFilter)",
        "fallback": None,
    },
    "limiter_true_peak": {
        "primary": "VST: FabFilter Pro-L 2 (FabFilter)",
        "fallback": None,
    },
    "bus_compressor": {
        "primary": "VST3: bx_townhouse Buss Compressor (Plugin Alliance)",
        "fallback": "Waves SSL G-Master Buss Compressor (Waves)",
    },
    "saturation": {
        "primary": "VST3: Decapitator (Soundtoys)",
        "fallback": None,
    },
    "doubler": {
        "primary": "VST3: MicroShift (Soundtoys)",
        "fallback": None,
    },
}

# ════════════════════════════════════════════════════════════════
# 空间效果器插件 — bus 类型 → [插件名子串列表（按优先级排序）]
# 从 engine.py _SPATIAL_PLUGIN 迁移至此，作为统一数据源。
# 子串用于 TrackFX_AddByName 匹配。
# ════════════════════════════════════════════════════════════════

SPATIAL_PLUGIN_MAP: dict[str, list[str]] = {
    "plate":   ["Little Plate", "UAD EMT 140", "ValhallaPlate"],
    "hall":    ["LX480", "ValhallaVintageVerb"],
    "room":    ["ValhallaRoom", "FabFilter Pro-R"],
    "slap":    ["EchoBoy", "ValhallaDelay"],
    "rhythm":  ["EchoBoy", "ValhallaDelay"],
}

# 返回轨名称（中文，REAPER 中可读）
SPATIAL_BUS_NAMES: dict[str, str] = {
    "plate":   "Plate Verb",
    "hall":    "Hall Verb",
    "room":    "Room Verb",
    "slap":    "Slap Delay",
    "rhythm":  "Rhythm Delay",
}

# 空间 bus 类型分类
_REVERB_BUS_TYPES = frozenset({"plate", "hall", "room"})
_DELAY_BUS_TYPES = frozenset({"slap", "rhythm"})


# ════════════════════════════════════════════════════════════════
# 公共 API
# ════════════════════════════════════════════════════════════════


def resolve_plugin(category: str) -> str | None:
    """返回指定类别的主选插件名，如果不可用则返回回退插件名。

    先检查主选插件是否在 normalize 注册表中存在；
    若不存在则尝试回退插件。两者都不存在时返回主选名
    （调用方可自行判断）。

    Parameters
    ----------
    category : str
        插件类别，如 ``"eq_surgical"``, ``"compressor_peak"`` 等。

    Returns
    -------
    str or None
        插件名称字符串，类别不存在时返回 None。

    Examples
    --------
    >>> name = resolve_plugin("eq_surgical")
    >>> name
    'VST: FabFilter Pro-Q 3 (FabFilter)'
    """
    entry = PLUGIN_REGISTRY.get(category)
    if entry is None:
        return None
    primary = entry.get("primary")
    if primary and isinstance(primary, str) and primary in _NORMALIZE_REGISTRY:
        return primary
    fallback = entry.get("fallback")
    if fallback and isinstance(fallback, str) and fallback in _NORMALIZE_REGISTRY:
        return fallback
    # 两者都不在 normalize 注册表中 — 无可用插件
    return None


def get_plugin_name(category: str, prefer_primary: bool = True) -> str:
    """获取插件名称字符串。

    Parameters
    ----------
    category : str
        插件类别。
    prefer_primary : bool
        True 返回主选名称，False 返回回退名称。

    Returns
    -------
    str
        插件名称字符串。若回退为 None 且 ``prefer_primary=False``，
        返回主选名。

    Raises
    ------
    KeyError
        若 *category* 不在注册表中。

    Examples
    --------
    >>> get_plugin_name("eq_surgical")
    'VST: FabFilter Pro-Q 3 (FabFilter)'
    >>> get_plugin_name("eq_surgical", prefer_primary=False)
    'ReaEQ (Cockos)'
    """
    entry = PLUGIN_REGISTRY[category]
    if prefer_primary:
        return entry["primary"]  # type: ignore[return-value]
    fallback = entry.get("fallback")
    if fallback is not None:
        return fallback  # type: ignore[return-value]
    return entry["primary"]  # type: ignore[return-value]


def list_all_plugins() -> list[str]:
    """列出所有信号链插件（主选 + 回退去重）。

    Returns
    -------
    list[str]
        去重排序后的所有插件名称列表。
    """
    names: set[str] = set()
    for entry in PLUGIN_REGISTRY.values():
        for key in ("primary", "fallback"):
            name = entry.get(key)
            if name and isinstance(name, str):
                names.add(name)
    return sorted(names)


def get_spatial_plugins(bus_type: str) -> list[str]:
    """返回指定空间 bus 类型的插件优先级列表。

    Parameters
    ----------
    bus_type : str
        ``"plate"``, ``"hall"``, ``"room"``, ``"slap"``, ``"rhythm"``。

    Returns
    -------
    list[str]
        插件名称子串列表（用于 TrackFX_AddByName 子串匹配）。

    Raises
    ------
    KeyError
        若 *bus_type* 不在空间映射中。
    """
    return SPATIAL_PLUGIN_MAP[bus_type]


def list_all_spatial_plugin_names() -> list[str]:
    """列出所有空间插件名称子串（去重）。

    Returns
    -------
    list[str]
        去重排序后的所有空间插件名称列表。
    """
    names: set[str] = set()
    for candidates in SPATIAL_PLUGIN_MAP.values():
        for name in candidates:
            names.add(name)
    return sorted(names)


def validate_registry_consistency() -> dict[str, object]:
    """验证 plugin_registry 中引用的插件名在 normalize 注册表中存在。

    返回不一致报告，可用于启动时自检。

    Returns
    -------
    dict
        ``{"missing": [描述, ...], "ok": True/False}``
    """
    missing: list[str] = []
    for category, entry in PLUGIN_REGISTRY.items():
        for key in ("primary", "fallback"):
            name = entry.get(key)
            if name and isinstance(name, str) and name not in _NORMALIZE_REGISTRY:
                missing.append(f"{category}.{key}: '{name}'")
    return {"missing": missing, "ok": len(missing) == 0}
