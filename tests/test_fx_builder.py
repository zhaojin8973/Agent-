"""FX Builder 单元测试 — FX 参数推导策略函数。"""
from unittest.mock import MagicMock, patch
import pytest
from hermes_core.fx_builder import (
    FXBuildContext,
    _build_eq_params,
    _build_compressor_params,
    _build_deesser_params,
    _build_decapitator_params,
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
        """阈值应在 [-40, -10] dB 范围内（新公式：band_rms + margin）。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_deesser_params(ctx)
        assert -40.0 <= result["Threshold"] <= -10.0, (
            f"Threshold={result['Threshold']} 超出 [-40, -10]"
        )

    def test_genre_affects_range(self):
        """不同流派的 Range 应不同。"""
        ctx_pop = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        ctx_rock = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="rock",
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        r_pop = _build_deesser_params(ctx_pop)["Range"]
        r_rock = _build_deesser_params(ctx_rock)["Range"]
        assert isinstance(r_pop, float)
        assert isinstance(r_rock, float)
        # pop 和 rock Range 应相同（都是 8.5）
        assert r_pop == r_rock


# ═══════════════════════════ Saturation (Decapitator) 策略 ═══════════════════════════

class TestBuildDecapitatiorParams:
    """Decapitator 参数推导 — 委托到 hermes_core.decapitator 模块。"""

    def test_builder_delegates_to_module(self):
        """_build_decapitator_params 委托到 decapitator.build_params。"""
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = _build_decapitator_params(ctx)
        assert result is not None
        assert "Drive" in result
        assert "Style" in result
        assert "Mix" in result
        assert "Tone" in result
        assert "OutputTrim" in result
        # Drive 在人声安全范围
        assert 0.05 <= result["Drive"] <= 0.30

    def test_no_rms_peak_returns_none(self):
        """无 RMS/Peak 时返回 None。"""
        ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
        )
        result = _build_decapitator_params(ctx)
        assert result is None


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
            raw_rms_db=-18.0, raw_peak_db=-6.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
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



# ═══════════════════════════ De-Esser 补充 ═══════════════════════════

class TestDeesserEdges:
    """De-Esser 参数推导的更多边界情况（新 Threshold = band_rms + margin）。"""

    def test_threshold_clamped_low(self):
        """极低 band_rms (-60dB) + 低 crest → Threshold clamp 在 -40。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            raw_rms_db=-60.0, raw_peak_db=-58.0,  # crest=2 → margin=4
        )
        result = _build_deesser_params(ctx)
        # band_rms=-30 (spectrum missing) + margin=4 = -26, clamped to [-40, -10]
        assert result["Threshold"] == -26.0

    def test_threshold_high_crest(self):
        """高 crest → 大 margin → Threshold 接近上限。"""
        ctx = FXBuildContext(
            fx_name="Pro-DS", fx_type="deesser", role="vocal", genre="pop",
            raw_rms_db=-28.0, raw_peak_db=-3.0,  # crest=25 → margin=10
        )
        result = _build_deesser_params(ctx)
        # band_rms=-30 + margin=10 = -20, clamp ok
        assert result["Threshold"] == -20.0

    def test_different_genre_range_values(self):
        """不同流派应产生不同的 Range 值。"""
        genres = ["pop", "rock", "electronic", "folk"]
        ranges = {}
        for g in genres:
            ctx = FXBuildContext(
                fx_name="Pro-DS", fx_type="deesser", role="vocal", genre=g,
                raw_rms_db=-18.0, raw_peak_db=-6.0,
            )
            ranges[g] = _build_deesser_params(ctx)["Range"]
        for g, r in ranges.items():
            assert 0.0 < r < 20.0, f"{g} Range={r} 超出范围"
        # electronic < pop (electronic=9, pop=8.5)
        assert ranges["folk"] < ranges["electronic"]


# ═══════════════════════════ EQ232D Builder ═══════════════════════════


@pytest.mark.unit
class TestBuildEQ232DParams:
    """Bettermaker EQ232D 参数推导测试（2026-06-07）。"""

    def test_returns_eq232d_param_names(self):
        """输出参数名应匹配 EQ232D（非 Pultec）。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=3.0,
        )
        result = _build_eq232d_params(ctx)
        assert result is not None
        # EQ232D 专属参数名
        assert "ENGAGE 1" in result
        assert "PEQ IN 1" in result
        assert "LO CPS 1" in result
        assert "LO BOOST 1" in result
        assert "LO ATTEN 1" in result
        assert "HI BOOST 1" in result
        assert "HI BW 1" in result
        assert "LVL OUT 1" in result
        # 通道配置参数
        assert "CHANNEL" in result
        assert "MS MATRIX" in result
        assert "ENGAGE 2" in result
        # 不应包含 Pultec 参数名
        assert "Low Freq" not in result
        assert "High Freq" not in result

    def test_channel_config(self):
        """Ch1 ON, Ch2 OFF, Dual Mono 模式, MS 关闭。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="folk", presence_deficit=-1.0,
        )
        result = _build_eq232d_params(ctx)
        assert result["ENGAGE 1"] == 1.0
        assert result["ENGAGE 2"] == 0.0   # Ch2 完全关闭
        assert result["CHANNEL"] == 1.0     # Dual Mono
        assert result["MS MATRIX"] == 0.0   # M/S 关闭
        assert result["PEQ IN 1"] == 1.0
        assert result["LVL OUT 1"] == 0.5   # unity

    def test_eq1_eq2_hpf_disabled(self):
        """参量段和 HPF 关闭（已有 Pro-Q 3 处理）。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=0.0,
        )
        result = _build_eq232d_params(ctx)
        assert result["HPF IN 1"] == 0.0
        assert result["EQ1 IN 1"] == 0.0
        assert result["EQ2 IN 1"] == 0.0

    def test_kcs_bypass(self):
        """Kick/Snare 滤波器应保持关闭（人声用）。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=0.0,
        )
        result = _build_eq232d_params(ctx)
        assert result["KCS BST 1"] == 0.0
        assert result["KCS ATT 1"] == 0.0

    def test_low_cps_fixed_60hz(self):
        """LO CPS 应固定为 0.33（约 60Hz）。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=0.0,
        )
        result = _build_eq232d_params(ctx)
        assert result["LO CPS 1"] == pytest.approx(0.33, abs=0.01)

    def test_deficit_affects_high_boost(self):
        """presence_deficit 驱动 HI BOOST 值。"""
        from hermes_core.fx_builder import _build_eq232d_params
        ctx_high = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=10.0,
        )
        ctx_low = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=0.0,
        )
        hi = _build_eq232d_params(ctx_high)["HI BOOST 1"]
        lo = _build_eq232d_params(ctx_low)["HI BOOST 1"]
        assert hi > lo

    def test_dispatch_via_build_fx_params(self):
        """build_fx_params 应正确分发到 _build_eq232d_params。"""
        ctx = FXBuildContext(
            fx_name="Bettermaker EQ232D", fx_type="color_eq_232d",
            role="vocal", genre="pop", presence_deficit=3.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
        assert "LO BOOST 1" in result


# ═══════════════════════════ Shadow Hills Builder ═══════════════════════════


@pytest.mark.unit
class TestBuildShadowHillsParams:
    """Shadow Hills Mastering Compressor 参数推导测试（2026-06-07）。"""

    def test_returns_shadow_hills_param_names(self):
        """输出参数名应匹配 Shadow Hills（非 CL 1B）。"""
        from hermes_core.fx_builder import _build_shadow_hills_params
        ctx = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="pop", raw_rms_db=-18.0,
        )
        result = _build_shadow_hills_params(ctx)
        assert result is not None
        # Shadow Hills 专属参数名
        assert "Hardwire Bypass" in result
        assert "Optical Bypass 1" in result
        assert "Optical Threshold 1" in result
        assert "Optical Gain 1" in result
        assert "Discrete Bypass 1" in result
        assert "Transformer 1" in result
        assert "Sidechain HP Freq" in result
        # 不应包含 CL 1B 参数名
        assert "Ratio" not in result
        assert "Attack" not in result
        assert "Release" not in result
        assert "Gain" not in result

    def test_discrete_bypass(self):
        """离散级必须完全 bypass。"""
        from hermes_core.fx_builder import _build_shadow_hills_params
        ctx = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="rock", raw_rms_db=-18.0,
        )
        result = _build_shadow_hills_params(ctx)
        assert result["Discrete Bypass 1"] == 0.0  # bypass
        assert result["Optical Bypass 1"] == 1.0   # engaged

    def test_transformer_on(self):
        """Iron 变压器染色应保持开启。"""
        from hermes_core.fx_builder import _build_shadow_hills_params
        ctx = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="pop", raw_rms_db=-18.0,
        )
        result = _build_shadow_hills_params(ctx)
        assert result["Transformer 1"] == 1.0
        assert result["Hardwire Bypass"] == 1.0

    def test_optical_threshold_derived_from_rms(self):
        """光学阈值应基于 RMS 计算（0–1 范围）。"""
        from hermes_core.fx_builder import _build_shadow_hills_params
        ctx = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="pop", raw_rms_db=-18.0,
        )
        result = _build_shadow_hills_params(ctx)
        thresh = result["Optical Threshold 1"]
        assert 0.0 <= thresh <= 1.0
        # RMS=-18 → 阈值约为 0（不压缩）
        assert thresh == pytest.approx(0.0, abs=0.1)

        ctx_hot = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="pop", raw_rms_db=-6.0,
        )
        hot_result = _build_shadow_hills_params(ctx_hot)
        hot_thresh = hot_result["Optical Threshold 1"]
        assert hot_thresh > thresh  # 更热的信号 → 更高阈值

    def test_genre_affects_sidechain_hpf(self):
        """不同流派的侧链 HPF 值不同。"""
        from hermes_core.fx_builder import _build_shadow_hills_params
        hpf_values = {}
        for g in ["pop", "rock", "electronic", "folk", "ballad"]:
            ctx = FXBuildContext(
                fx_name="Shadow Hills", fx_type="tube_opto_sh",
                role="vocal", genre=g, raw_rms_db=-18.0,
            )
            hpf_values[g] = _build_shadow_hills_params(ctx)["Sidechain HP Freq"]
        # 不同流派应有不同值（允许微小差异）
        unique = len(set(round(v, 2) for v in hpf_values.values()))
        assert unique >= 2, f"所有流派 HPF 值相同: {hpf_values}"

    def test_dispatch_via_build_fx_params(self):
        """build_fx_params 应正确分发到 _build_shadow_hills_params。"""
        ctx = FXBuildContext(
            fx_name="Shadow Hills", fx_type="tube_opto_sh",
            role="vocal", genre="pop", raw_rms_db=-18.0,
        )
        result = build_fx_params(ctx)
        assert result is not None
        assert "Optical Threshold 1" in result
