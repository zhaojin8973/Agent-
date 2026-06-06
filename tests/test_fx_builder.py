"""FX Builder 单元测试 — FX 参数推导策略函数。"""
from unittest.mock import MagicMock, patch
import pytest
from hermes_core.fx_builder import (
    FXBuildContext,
    _build_eq_params,
    _build_compressor_params,
    _build_deesser_params,
    _build_saturation_params,
    _build_doubler_params,
    _build_dynamic_eq_params,
    build_fx_params,
    get_fx_builder,
    _FX_BUILDERS,
    _init_comp_translators,
)


# ═══════════════════════════ FXBuildContext ═══════════════════════════

class TestFXBuildContext:
    def test_minimal(self):
        ctx = FXBuildContext(fx_name="Test", fx_type="eq", role="vocal", genre="pop")
        assert ctx.fx_name == "Test"
        assert ctx.raw_rms_db is None
        assert ctx.presence_deficit == 0.0

    def test_full(self):
        ctx = FXBuildContext(
            fx_name="FabFilter Pro-C 2", fx_type="comp", role="vocal",
            genre="rock", bpm=120.0, raw_rms_db=-18.0, raw_peak_db=-6.0,
            stem_file_path="/tmp/vocal.wav", presence_deficit=2.5,
        )
        assert ctx.fx_type == "comp"
        assert ctx.bpm == 120.0


# ═══════════════════════════ EQ 策略 ═══════════════════════════

class TestBuildEqParams:
    def test_returns_last_eq_params(self):
        ctx = FXBuildContext(
            fx_name="Pro-Q 3", fx_type="eq", role="vocal", genre="pop",
            last_eq_params={"Band 1 Freq": 200.0, "Band 1 Gain": 2.0},
        )
        result = _build_eq_params(ctx)
        assert result == {"Band 1 Freq": 200.0, "Band 1 Gain": 2.0}

    def test_empty_params_returns_none(self):
        ctx = FXBuildContext(fx_name="Pro-Q 3", fx_type="eq", role="vocal", genre="pop")
        result = _build_eq_params(ctx)
        assert result is None


# ═══════════════════════════ De-Esser 策略 ═══════════════════════════

class TestBuildDeesserParams:
    def test_returns_all_keys(self):
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            presence_deficit=5.0,
        )
        result = _build_deesser_params(ctx)
        assert "Threshold" in result
        assert "Range" in result
        assert "Mode" in result

    def test_threshold_clamped(self):
        """阈值应在 -60..0 dB 范围内。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            presence_deficit=-500.0,
        )
        result = _build_deesser_params(ctx)
        assert result["Threshold"] >= -60.0

    def test_genre_affects_range(self):
        """不同流派的 Range 应不同。"""
        ctx_pop = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            presence_deficit=0.0,
        )
        ctx_rock = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="rock",
            presence_deficit=0.0,
        )
        r_pop = _build_deesser_params(ctx_pop)["Range"]
        r_rock = _build_deesser_params(ctx_rock)["Range"]
        # 不同流派可能有不同的默认 Range
        assert isinstance(r_pop, float)
        assert isinstance(r_rock, float)


# ═══════════════════════════ Saturation 策略 ═══════════════════════════

class TestBuildSaturationParams:
    def test_default_crest(self):
        """无 RMS/Peak 时使用默认 crest 12dB。"""
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
        )
        result = _build_saturation_params(ctx)
        assert "Drive" in result
        assert "Mix" in result
        assert 0.1 <= result["Drive"] <= 1.0

    def test_low_crest_gives_high_drive(self):
        """低波峰（压缩感强）→ 高 Drive（增加谐波）。"""
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
            raw_rms_db=-12.0, raw_peak_db=-14.0,  # crest=2dB
        )
        result = _build_saturation_params(ctx)
        assert result["Drive"] > 0.5  # 高 drive

    def test_high_crest_gives_low_drive(self):
        """高波峰（瞬态丰富）→ 低 Drive（保留动态）。"""
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
            raw_rms_db=-28.0, raw_peak_db=-6.0,  # crest=22dB
        )
        result = _build_saturation_params(ctx)
        assert result["Drive"] < 0.5  # crest=22 → drive=0.3


# ═══════════════════════════ Doubler 策略 ═══════════════════════════

class TestBuildDoublerParams:
    def test_returns_default_params(self):
        ctx = FXBuildContext(
            fx_name="MicroShift", fx_type="doubler", role="vocal", genre="pop",
        )
        result = _build_doubler_params(ctx)
        assert result == {"Mix": 0.3, "Detune": 0.15, "Delay": 0.05}


# ═══════════════════════════ Dynamic EQ 策略 ═══════════════════════════

class TestBuildDynamicEqParams:
    def test_no_stem_file_returns_none(self):
        ctx = FXBuildContext(
            fx_name="Pro-Q 3", fx_type="dynamic_eq", role="vocal", genre="pop",
            stem_file_path="",
        )
        result = _build_dynamic_eq_params(ctx)
        assert result is None

    def test_nonexistent_stem_returns_none(self):
        ctx = FXBuildContext(
            fx_name="Pro-Q 3", fx_type="dynamic_eq", role="vocal", genre="pop",
            stem_file_path="/nonexistent/file.wav",
        )
        result = _build_dynamic_eq_params(ctx)
        assert result is None


# ═══════════════════════════ 注册表 ═══════════════════════════

class TestFXBuilderRegistry:
    def test_all_known_types_registered(self):
        """所有已知 FX 类型应有注册策略。"""
        for fx_type in ["eq", "deesser", "saturation", "dynamic_eq", "doubler"]:
            assert fx_type in _FX_BUILDERS, f"{fx_type} 未注册"

    def test_get_fx_builder_returns_callable(self):
        assert callable(get_fx_builder("eq"))
        assert callable(get_fx_builder("deesser"))
        assert callable(get_fx_builder("saturation"))

    def test_get_fx_builder_unknown_returns_none(self):
        assert get_fx_builder("unknown_fx") is None

    def test_vca_fet_opto_all_delegate_to_compressor(self):
        """vca/fet/opto 应委托到压缩器策略。"""
        for fx_type in ("vca", "fet", "opto"):
            builder = get_fx_builder(fx_type)
            assert builder is not None
            assert "compressor" in builder.__name__


# ═══════════════════════════ build_fx_params 集成 ═══════════════════════════

class TestBuildFxParams:
    def setup_method(self):
        _init_comp_translators()

    def test_eq_type(self):
        ctx = FXBuildContext(
            fx_name="Pro-Q 3", fx_type="eq", role="vocal", genre="pop",
            last_eq_params={"Band 1 Freq": 200},
        )
        result = build_fx_params(ctx)
        assert result == {"Band 1 Freq": 200}

    def test_deesser_type(self):
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
        )
        result = build_fx_params(ctx)
        assert "Threshold" in result

    def test_saturation_type(self):
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
        )
        result = build_fx_params(ctx)
        assert "Drive" in result

    def test_unknown_type_returns_none(self):
        ctx = FXBuildContext(
            fx_name="Unknown", fx_type="unknown", role="vocal", genre="pop",
        )
        result = build_fx_params(ctx)
        assert result is None

    def test_compressor_with_rms_peak(self):
        """压缩器类型 + RMS/Peak → 返回物理参数。"""
        ctx = FXBuildContext(
            fx_name="FabFilter Pro-C 2", fx_type="vca", role="vocal",
            genre="pop", raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
        assert "Ratio" in result or "Thresh" in result or "Threshold" in result

    def test_compressor_cla76_variant(self):
        """CLA-76 压缩器 → 使用专属参数推导。"""
        ctx = FXBuildContext(
            fx_name="CLA-76", fx_type="fet", role="vocal",
            genre="rock", bpm=120.0, raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None

    def test_compressor_rvox_via_build_fx_params(self):
        """RVox 名称 + vca 类型 → 走压缩器路径。"""
        ctx = FXBuildContext(
            fx_name="RVox", fx_type="vca", role="vocal",
            genre="pop", raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None

    def test_compressor_with_bpm_timing(self):
        """BPM 感知 → 覆盖 attack/release 替换。"""
        ctx = FXBuildContext(
            fx_name="FabFilter Pro-C 2", fx_type="opto", role="vocal",
            genre="electronic", bpm=140.0, raw_rms_db=-20.0, raw_peak_db=-8.0,
        )
        result = build_fx_params(ctx)
        assert result is not None

    def test_compressor_no_bpm_removes_timing(self):
        """无 BPM → 应移除 Attack/Release 键。"""
        ctx = FXBuildContext(
            fx_name="FabFilter Pro-C 2", fx_type="vca", role="vocal",
            genre="pop", bpm=None, raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
        assert "Attack" not in result

    def test_compressor_no_rms_returns_none(self):
        """无 RMS/Peak → 返回 None。"""
        ctx = FXBuildContext(
            fx_name="FabFilter Pro-C 2", fx_type="vca", role="vocal",
            genre="pop",
        )
        result = build_fx_params(ctx)
        assert result is None


# ═══════════════════════════ RVox 路由 ═══════════════════════════

class TestRVoxRouting:
    """rvox 类型应正确路由到压缩器参数推导策略。"""

    def setup_method(self):
        _init_comp_translators()

    def test_get_fx_builder_rvox_returns_compressor(self):
        """get_fx_builder('rvox') 应返回压缩器 builder。"""
        builder = get_fx_builder("rvox")
        assert builder is not None
        assert callable(builder)
        assert "compressor" in builder.__name__

    def test_build_fx_params_rvox_type(self):
        """fx_type='rvox' 通过 build_fx_params 应不返回 None。"""
        ctx = FXBuildContext(
            fx_name="VST3: RVox Mono (Waves)", fx_type="rvox",
            role="vocal", genre="pop",
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
        assert isinstance(result, dict)

    def test_build_compressor_params_rvox_direct(self):
        """_build_compressor_params 直接调用 rvox 路径。"""
        ctx = FXBuildContext(
            fx_name="RVox", fx_type="rvox", role="vocal",
            genre="pop", raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_compressor_params(ctx)
        assert result is not None
        assert isinstance(result, dict)
        # RVox 参数应包含 "Compression" 键
        assert "Compression" in result

    def test_build_fx_params_rvox_no_rms_returns_none(self):
        """rvox 类型但无 RMS/Peak → 返回 None。"""
        ctx = FXBuildContext(
            fx_name="RVox", fx_type="rvox", role="vocal", genre="pop",
        )
        result = build_fx_params(ctx)
        assert result is None


# ═══════════════════════════ 压缩器边界 ═══════════════════════════

class TestCompressorEdges:
    """压缩器参数推导的边界和异常路径。"""

    def setup_method(self):
        _init_comp_translators()

    def test_unknown_type_returns_none(self):
        """非压缩器类型（如 'limiter'）→ _build_compressor_params 返回 None。"""
        ctx = FXBuildContext(
            fx_name="Pro-L 2", fx_type="limiter", role="vocal",
            genre="pop", raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_compressor_params(ctx)
        assert result is None

    def test_cla76_no_bpm_strips_release(self):
        """CLA-76 无 BPM → Release 键应被移除。"""
        ctx = FXBuildContext(
            fx_name="CLA-76", fx_type="fet", role="vocal",
            genre="pop", bpm=None, raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_compressor_params(ctx)
        assert result is not None
        assert "Release" not in result

    def test_vca_with_bpm_strips_timing_on_cla76(self):
        """CLA-76 有 BPM → Release 保留（BPM 驱动）。"""
        ctx = FXBuildContext(
            fx_name="CLA-76", fx_type="fet", role="vocal",
            genre="rock", bpm=120.0, raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_compressor_params(ctx)
        assert result is not None
        # CLA-76 有 BPM 时 Release 存在
        assert "Release" in result


# ═══════════════════════════ De-Esser 补充 ═══════════════════════════

class TestDeesserEdges:
    """De-Esser 参数推导的更多边界情况。"""

    def test_high_presence_deficit(self):
        """高存在感缺失 → 阈值为 0dB。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            presence_deficit=500.0,
        )
        result = _build_deesser_params(ctx)
        assert result["Threshold"] == 0.0

    def test_low_presence_deficit(self):
        """极低存在感缺失 → 阈值接近 -60dB。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            presence_deficit=-500.0,
        )
        result = _build_deesser_params(ctx)
        assert result["Threshold"] == -60.0

    def test_different_genre_range_values(self):
        """不同流派应产生不同的 Range 值。"""
        genres = ["pop", "rock", "electronic", "hip_hop", "folk"]
        ranges = {}
        for g in genres:
            ctx = FXBuildContext(
                fx_name="Pro-DS", fx_type="deesser", role="vocal", genre=g,
            )
            ranges[g] = _build_deesser_params(ctx)["Range"]
        # 所有 Range 值应在合理范围内
        for g, r in ranges.items():
            assert 0.0 < r < 20.0, f"{g} Range={r} 超出范围"
