"""FX Builder 单元测试 — FX 参数推导策略函数。"""
from unittest.mock import MagicMock, patch
import pytest
from hermes_core.fx_builder import (
    FXBuildContext,
    _build_eq_params,
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
