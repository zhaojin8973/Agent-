"""Tests for hermes_core.plugin_registry — 统一插件注册表。

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_plugin_registry.py -v
    PYTHONPATH=src python3 -m pytest tests/test_plugin_registry.py -v -m unit
"""

import pytest

from hermes_core.plugin_registry import (
    PLUGIN_REGISTRY,
    SPATIAL_PLUGIN_MAP,
    SPATIAL_BUS_NAMES,
    resolve_plugin,
    get_plugin_name,
    list_all_plugins,
    get_spatial_plugins,
    list_all_spatial_plugin_names,
    validate_registry_consistency,
)
from hermes_core.normalize import PLUGIN_REGISTRY as _NORMALIZE_REGISTRY


# ════════════════════════════════════════════════════════════════
# Unit: 插件类别注册表结构
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRegistryStructure:
    """验证 PLUGIN_REGISTRY 的结构完整性。"""

    def test_all_categories_have_primary(self):
        """每个类别都有主选插件名。"""
        for category, entry in PLUGIN_REGISTRY.items():
            assert "primary" in entry, f"{category} 缺少 'primary' 键"
            assert isinstance(entry["primary"], str), (
                f"{category}.primary 应为字符串"
            )
            assert len(entry["primary"]) > 0, f"{category}.primary 为空字符串"

    def test_all_categories_have_fallback_key(self):
        """每个类别都有 fallback 键（值可为 None）。"""
        for category, entry in PLUGIN_REGISTRY.items():
            assert "fallback" in entry, f"{category} 缺少 'fallback' 键"

    def test_known_categories_exist(self):
        """关键类别存在。"""
        expected = {
            "eq_surgical", "eq_color", "compressor_peak", "compressor_rms",
            "deesser", "limiter_true_peak", "bus_compressor",
            "saturation", "doubler",
        }
        actual = set(PLUGIN_REGISTRY.keys())
        missing = expected - actual
        assert not missing, f"缺少类别: {missing}"


# ════════════════════════════════════════════════════════════════
# Unit: resolve_plugin
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestResolvePlugin:
    """测试 resolve_plugin() 的解析逻辑。"""

    def test_resolve_primary_when_available(self):
        """主选插件在 normalize 注册表中时返回主选。"""
        result = resolve_plugin("eq_surgical")
        assert result == "VST: FabFilter Pro-Q 3 (FabFilter)"

    def test_resolve_returns_none_for_unknown_category(self):
        """未知类别返回 None。"""
        result = resolve_plugin("nonexistent_category")
        assert result is None

    def test_resolve_deesser(self):
        """deesser 没有回退，返回主选。"""
        result = resolve_plugin("deesser")
        assert result == "VST: FabFilter Pro-DS (FabFilter)"

    def test_resolve_bus_compressor(self):
        """bus_compressor 主选在 normalize 注册表中。"""
        result = resolve_plugin("bus_compressor")
        assert result == "VST3: bx_townhouse Buss Compressor (Plugin Alliance)"

    def test_resolve_limiter_true_peak(self):
        """limiter 无回退，返回主选。"""
        result = resolve_plugin("limiter_true_peak")
        assert result == "VST: FabFilter Pro-L 2 (FabFilter)"

    def test_resolve_returns_none_for_unregistered_primary_only(self):
        """主选和回退都不在 normalize 注册表且回退为 None 时返回 None。

        saturation 的主选 'VST3: Decapitator (Soundtoys)' 不在
        normalize 的 PLUGIN_REGISTRY 中，且回退为 None。
        """
        result = resolve_plugin("saturation")
        # 主选不在 normalize 注册表中，回退为 None → 返回 None
        assert result is None


# ════════════════════════════════════════════════════════════════
# Unit: get_plugin_name
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGetPluginName:
    """测试 get_plugin_name() 的名称获取逻辑。"""

    def test_get_primary_by_default(self):
        """默认返回主选名。"""
        name = get_plugin_name("eq_surgical")
        assert name == "VST: FabFilter Pro-Q 3 (FabFilter)"

    def test_get_primary_explicit(self):
        """prefer_primary=True 返回主选名。"""
        name = get_plugin_name("eq_surgical", prefer_primary=True)
        assert name == "VST: FabFilter Pro-Q 3 (FabFilter)"

    def test_get_fallback(self):
        """prefer_primary=False 返回回退名。"""
        name = get_plugin_name("eq_surgical", prefer_primary=False)
        assert name == "ReaEQ (Cockos)"

    def test_get_fallback_when_none_returns_primary(self):
        """回退为 None 时 fallback 模式仍返回主选。"""
        name = get_plugin_name("deesser", prefer_primary=False)
        assert name == "VST: FabFilter Pro-DS (FabFilter)"

    def test_unknown_category_raises(self):
        """未知类别抛出 KeyError。"""
        with pytest.raises(KeyError):
            get_plugin_name("nonexistent")

    def test_compressor_peak_primary(self):
        name = get_plugin_name("compressor_peak")
        assert "1176" in name

    def test_compressor_peak_fallback(self):
        name = get_plugin_name("compressor_peak", prefer_primary=False)
        assert "CLA-76" in name or "1176" in name


# ════════════════════════════════════════════════════════════════
# Unit: list_all_plugins
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestListAllPlugins:
    """测试 list_all_plugins() 的列表生成逻辑。"""

    def test_returns_list(self):
        """返回值是列表类型。"""
        result = list_all_plugins()
        assert isinstance(result, list)

    def test_no_duplicates(self):
        """列表中没有重复插件名。"""
        result = list_all_plugins()
        assert len(result) == len(set(result)), "插件列表包含重复项"

    def test_sorted(self):
        """列表按字母排序。"""
        result = list_all_plugins()
        assert result == sorted(result), "插件列表未排序"

    def test_includes_all_categories(self):
        """包含所有类别的主选和回退（非 None）。"""
        result = list_all_plugins()
        result_set = set(result)
        for entry in PLUGIN_REGISTRY.values():
            for key in ("primary", "fallback"):
                name = entry.get(key)
                if name and isinstance(name, str):
                    assert name in result_set, f"缺少: {name}"

    def test_does_not_include_none(self):
        """None 值不出现。"""
        result = list_all_plugins()
        assert None not in result
        assert "" not in result


# ════════════════════════════════════════════════════════════════
# Unit: 空间插件映射
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSpatialPlugins:
    """测试空间插件映射 SPATIAL_PLUGIN_MAP。"""

    def test_all_bus_types_exist(self):
        """所有 bus 类型都在映射中（含 electronic 专属 + MicroShift + Delay×3）。"""
        expected = {"plate", "hall", "room", "slap", "throw", "pingpong",
                    "microshift", "blackhole", "supernova"}
        assert set(SPATIAL_PLUGIN_MAP.keys()) == expected

    def test_plate_plugins(self):
        plugins = get_spatial_plugins("plate")
        assert len(plugins) >= 3
        assert "ValhallaPlate" in plugins

    def test_hall_plugins(self):
        plugins = get_spatial_plugins("hall")
        assert len(plugins) >= 2
        assert "ValhallaVintageVerb" in plugins

    def test_room_plugins(self):
        plugins = get_spatial_plugins("room")
        assert len(plugins) >= 2
        assert "ValhallaRoom" in plugins

    def test_slap_plugins(self):
        plugins = get_spatial_plugins("slap")
        assert len(plugins) >= 1
        assert "ValhallaDelay" in plugins

    def test_throw_plugins(self):
        plugins = get_spatial_plugins("throw")
        assert len(plugins) >= 1
        assert "ValhallaDelay" in plugins

    def test_pingpong_plugins(self):
        plugins = get_spatial_plugins("pingpong")
        assert len(plugins) >= 1
        assert "ValhallaDelay" in plugins

    def test_unknown_bus_type_raises(self):
        with pytest.raises(KeyError):
            get_spatial_plugins("unknown_bus")

    def test_spatial_bus_names_complete(self):
        """SPATIAL_BUS_NAMES 包含所有 bus 类型。"""
        assert set(SPATIAL_BUS_NAMES.keys()) == {"plate", "hall", "room", "slap", "throw", "pingpong", "microshift", "blackhole", "supernova"}
        assert all(isinstance(v, str) and len(v) > 0 for v in SPATIAL_BUS_NAMES.values())


# ════════════════════════════════════════════════════════════════
# Unit: list_all_spatial_plugin_names
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestListAllSpatialPluginNames:
    """测试 list_all_spatial_plugin_names()。"""

    def test_returns_list(self):
        result = list_all_spatial_plugin_names()
        assert isinstance(result, list)
        assert len(result) > 0

    def test_no_duplicates(self):
        result = list_all_spatial_plugin_names()
        assert len(result) == len(set(result)), "包含重复项"

    def test_sorted(self):
        result = list_all_spatial_plugin_names()
        assert result == sorted(result), "未排序"


# ════════════════════════════════════════════════════════════════
# Unit: 一致性验证
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestValidateRegistryConsistency:
    """测试 validate_registry_consistency() 一致性验证。"""

    def test_returns_dict_with_expected_keys(self):
        result = validate_registry_consistency()
        assert "missing" in result
        assert "ok" in result

    def test_reports_missing_entries(self):
        """不在 normalize 注册表中的主选/回退应被报告。

        'saturation' 的 primary (Decapitator) 不在 normalize 注册表中。
        """
        result = validate_registry_consistency()
        missing = result["missing"]

        # saturation 的主选不在 normalize 注册表中
        saturation_missing = any("saturation" in m for m in missing)

        assert saturation_missing, "saturation 应被报告为缺失"
        assert result["ok"] is False, "存在缺失时 ok 应为 False"

    def test_registered_plugins_not_in_missing(self):
        """已在 normalize 注册表中的插件不应被报告为缺失。"""
        result = validate_registry_consistency()
        missing = result["missing"]

        # eq_surgical 主选在 normalize 中
        eq_surgical_missing = [m for m in missing if "eq_surgical" in m]
        assert len(eq_surgical_missing) == 0, (
            f"eq_surgical 不应缺失: {eq_surgical_missing}"
        )
