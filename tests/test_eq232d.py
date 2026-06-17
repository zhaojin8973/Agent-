"""
EQ232D 单元测试 — v3 参考模板偏差驱动。
"""

import pytest
from hermes_core.vocal_ref import get_ref, relativize, deviation, is_outside
from hermes_core.eq232d import (
    lo_cps_val, low_boost, low_atten, hi_boost, hi_atten, hi_bw,
    kcs_bst_val, kcs_att_val,
    build_params, normalize_params,
)


# ════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════

class _FakeCtx:
    def __init__(self, genre="pop", presence_deficit=0.0):
        self.genre = genre
        self.presence_deficit = presence_deficit
        self.raw_rms_db = None
        self.raw_peak_db = None
        self.fx_name = "Bettermaker EQ232D (Plugin Alliance)"
        self.fx_type = "color_eq_232d"
        self.role = "vocal"
        self.bpm = None
        self.stem_file_path = ""
        self.last_eq_params = {}
        self.eq_position = "solo"


def _spec(band_energy=None, air_level_db=None, **kw):
    """构造 spectrum dict。"""
    d = {"band_energy_db": band_energy or {}, "air_level_db": air_level_db}
    d.update(kw)
    return d


# 望归民美女声 mid-chain 实际数据（用于集成验证）
_WANGGUI_SPECTRUM = _spec(
    band_energy={"sub": -100.0, "low": -90.3, "low_mid": -68.2,
                 "mid": -56.4, "high_mid": -56.3, "presence": -76.3, "air": -81.7},
    air_level_db=-81.7, mud_ratio=-12.1, sibilance_peak_hz=4008.0,
)


# ════════════════════════════════════════════════════════════════
# normalize_params
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestNormalizeParams:
    def test_passthrough(self):
        assert normalize_params({"LO BOOST 1": 0.35}) == {"LO BOOST 1": 0.35}
    def test_clamp_low(self):
        assert normalize_params({"LO BOOST 1": -0.5}) == {"LO BOOST 1": 0.0}
    def test_clamp_high(self):
        assert normalize_params({"HI BOOST 1": 1.5}) == {"HI BOOST 1": 1.0}


# ════════════════════════════════════════════════════════════════
# 参考模板
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestReference:
    def test_female_ref(self):
        ref = get_ref("female")
        assert ref["air"][0] == -32.0
        assert ref["presence"][0] == -20.0

    def test_male_ref_darker(self):
        ref = get_ref("male")
        assert ref["air"][0] < get_ref("female")["air"][0]  # 男声高频更暗
        assert ref["presence"][0] < get_ref("female")["presence"][0]

    def test_fallback_female(self):
        assert get_ref("") == get_ref("female")

    def testrelativize(self):
        rel = relativize({"mid": -50.0, "air": -80.0, "low": -60.0})
        assert rel["mid"] == 0.0
        assert rel["air"] == -30.0
        assert rel["low"] == -10.0

    def test_deviation(self):
        assert deviation(-35.0, -32.0) == -3.0  # 比参考暗 3dB
        assert deviation(-25.0, -32.0) == 7.0   # 比参考亮 7dB

    def testis_outside(self):
        assert is_outside(-12.0, 10.0)  # -12 超出 ±10 容差
        assert not is_outside(-5.0, 10.0)
        assert is_outside(11.0, 10.0)


# ════════════════════════════════════════════════════════════════
# lo_cps_val
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestLoCPS:
    def test_female_100hz(self):
        assert lo_cps_val(gender="female", genre="pop") == 1.0

    def test_male_rock_60hz(self):
        assert lo_cps_val(gender="male", genre="rock") == pytest.approx(0.667, abs=0.01)

    def test_male_pop_100hz(self):
        assert lo_cps_val(gender="male", genre="pop") == 1.0


# ════════════════════════════════════════════════════════════════
# low_boost — 偏差驱动
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestLowBoost:
    def test_normal_spectrum(self):
        """正常低频 → 中等 boost。"""
        # low_rel = -10 (正常范围 [-18,-2])
        v = low_boost("pop", gender="female",
                      spectrum=_spec(band_energy={"mid": -50, "low": -60}))
        assert 0.05 < v < 0.20

    def test_thin_vocal_more_boost(self):
        """低频不足 → 增强。low_rel = -25 (远低于正常)"""
        v_thin = low_boost("pop", gender="female",
                           spectrum=_spec(band_energy={"mid": -50, "low": -75}))
        v_norm = low_boost("pop", gender="female",
                           spectrum=_spec(band_energy={"mid": -50, "low": -60}))
        assert v_thin > v_norm

    def test_thick_vocal_less_boost(self):
        """低频过多 → 减弱。"""
        v = low_boost("pop", gender="female",
                      spectrum=_spec(band_energy={"mid": -50, "low": -48}))
        assert v < 0.10

    def test_no_spectrum_fallback(self):
        v = low_boost("pop", gender="female", spectrum=None)
        assert v == pytest.approx(0.175, abs=0.01)  # 0.250 × 0.7(女声)

    def test_female_multiplier(self):
        """女声 ×0.7。"""
        v_f = low_boost("pop", gender="female", spectrum=None)
        v_m = low_boost("pop", gender="male", spectrum=None)
        assert v_f < v_m


# ════════════════════════════════════════════════════════════════
# low_atten — 偏差驱动
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestLowAtten:
    def test_muddy_more_atten(self):
        """low_mid 过高 → 增强 atten。"""
        v = low_atten(3.0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "low_mid": -40}))
        assert v > 0.18

    def test_clean_less_atten(self):
        """low_mid 偏低 → 减弱 atten。"""
        v = low_atten(3.0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "low_mid": -70}))
        assert v < 0.10

    def test_female_more_atten(self):
        v_f = low_atten(3.0, "pop", gender="female", spectrum=None)
        v_m = low_atten(3.0, "pop", gender="male", spectrum=None)
        assert v_f > v_m  # 女声多清低频

    def test_deficit_fallback(self):
        v_high = low_atten(5.0, "pop", spectrum=None)
        v_low = low_atten(0.0, "pop", spectrum=None)
        assert v_high > v_low


# ════════════════════════════════════════════════════════════════
# hi_boost — 偏差驱动
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestHiBoost:
    def test_dark_more_boost(self):
        """air 低于参考 → 更多 boost。"""
        v_dark = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -100}))  # air_rel=-50, dev=-18 → dark
        v_norm = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -82}))  # air_rel=-32 → normal
        assert v_dark > v_norm

    def test_normal_light_boost(self):
        """air 在正常范围 → 0.12。"""
        v = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -82}))
        assert v == 0.12

    def test_bright_minimal_boost(self):
        """air 高于参考 → 微量 0.08。"""
        v = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -60}))
        assert v == 0.08

    def test_ceiling(self):
        v = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -120}))
        assert v <= 0.75

    def test_floor(self):
        v = hi_boost(0, "pop", spectrum=_spec(
            band_energy={"mid": -50, "air": -50}))
        assert v >= 0.08

    def test_fallback_no_spectrum(self):
        v = hi_boost(4.0, "pop", spectrum=None)
        assert v == pytest.approx(0.16, abs=0.01)  # 4.0 × 0.040

    def test_wanggui_female(self):
        """望归民美女声实际数据。"""
        v = hi_boost(19.9, "chinese_folk_bel_canto",
                     spectrum=_WANGGUI_SPECTRUM, gender="female")
        # air_rel = -81.7 - (-56.4) = -25.3
        # 女声 air_ref = -32.0, tol = 10.0
        # dev = -25.3 - (-32.0) = 6.7 (正数 = 比正常亮!)
        # dev NOT < -tol, so it falls into normal → 0.12
        assert 0.10 <= v <= 0.25


# ════════════════════════════════════════════════════════════════
# hi_atten — 与 hi_boost 成比例
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestHiAtten:
    def test_proportional_to_boost(self):
        """boost > 0.30 才启用 HI ATTEN。"""
        assert hi_atten(0.50) == pytest.approx(0.060, abs=0.01)
        assert hi_atten(0.70) == pytest.approx(0.084, abs=0.01)

    def test_zero_when_boost_low(self):
        """boost ≤ 0.30 → HI ATTEN = 0。"""
        assert hi_atten(0.30) == 0.0
        assert hi_atten(0.12) == 0.0
        assert hi_atten(0.05) == 0.0


# ════════════════════════════════════════════════════════════════
# hi_bw / kcs
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestHiBW:
    def test_broad_for_most(self):
        for g in ("folk", "ballad", "chinese_folk_bel_canto", "pop"):
            assert hi_bw(g) == pytest.approx(0.72, abs=0.01)

    def test_medium_for_heavy(self):
        for g in ("rock", "rap", "electronic"):
            assert hi_bw(g) == pytest.approx(0.55, abs=0.01)


@pytest.mark.unit
class TestKcsFreq:
    def test_default_12k(self):
        assert kcs_bst_val("folk") == pytest.approx(0.833, abs=0.01)

    def test_rock_10k(self):
        assert kcs_bst_val("rock") == pytest.approx(0.667, abs=0.01)

    def test_att_20k(self):
        assert kcs_att_val() == 1.0


# ════════════════════════════════════════════════════════════════
# build_params — 集成
# ════════════════════════════════════════════════════════════════

@pytest.mark.unit
class TestBuildParams:
    def test_wanggui_with_spectrum(self):
        """望归民美女声 → 偏差驱动，正常 air 不过度 boost。"""
        ctx = _FakeCtx(genre="chinese_folk_bel_canto", presence_deficit=19.9)
        r = build_params(ctx, gender="female", spectrum=_WANGGUI_SPECTRUM)
        assert r["LO CPS 1"] == 1.0            # 女声 → 100Hz
        assert r["KCS BST 1"] == pytest.approx(0.833, abs=0.01)  # 12kHz
        assert r["KCS ATT 1"] == 1.0           # 20kHz
        assert r["HI BW 1"] == 0.72            # Broad
        assert 0.10 <= r["HI BOOST 1"] <= 0.25  # 正常范围，不过激
        assert r["HI ATTEN 1"] == 0.0           # boost 小，不触发
        assert r["CHANNEL"] == 0.0

    def test_ch2_mirrors_ch1(self):
        ctx = _FakeCtx()
        r = build_params(ctx)
        for k in ("LO BOOST", "LO ATTEN", "HI BOOST", "HI BW", "LVL OUT", "PEQ IN"):
            assert r[f"{k} 1"] == r[f"{k} 2"], f"{k} mismatch"

    def test_no_engage_2(self):
        assert "ENGAGE 2" not in build_params(_FakeCtx())

    def test_stereo_mode(self):
        assert build_params(_FakeCtx())["CHANNEL"] == 0.0

    def test_minimal_call(self):
        assert build_params(_FakeCtx()) is not None
        assert build_params(_FakeCtx(), gender="male") is not None

    def test_lvl_out_compensation(self):
        r_dark = build_params(_FakeCtx("pop"), spectrum=_spec(
            band_energy={"mid": -50, "air": -90}))
        r_bright = build_params(_FakeCtx("pop"), spectrum=_spec(
            band_energy={"mid": -50, "air": -50}))
        assert r_dark["LVL OUT 1"] < r_bright["LVL OUT 1"]
