"""Oxford Inflator 独立模块测试 — 两层结构（post-RVox crest 纠错 + 流派美化）。"""
import pytest
from hermes_core.inflator import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre
        self.fx_name = "VST3: Oxford Inflator (Sonnox)"


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp_all_values(self):
        """所有参数归一化到 [0, 1] 范围。"""
        r = normalize_params({
            "Input Gain": 0.333, "Effect": 0.35, "Curve": 0.30,
            "Output Gain": 1.0, "In": 1.0, "Band Split": 0.0,
            "Clip 0dB": 1.0,
        })
        for v in r.values():
            assert 0.0 <= v <= 1.0

    def test_passthrough_normalized_values(self):
        """已在 0-1 范围内的值保持原样。"""
        r = normalize_params({"Effect": 0.30, "Curve": 0.20, "Clip 0dB": 1.0})
        assert r["Effect"] == 0.30
        assert r["Curve"] == 0.20
        assert r["Clip 0dB"] == 1.0

    def test_clamp_oob(self):
        """越界值被钳制。"""
        r = normalize_params({"Effect": 1.5, "Curve": -0.5})
        assert r["Effect"] == 1.0
        assert r["Curve"] == 0.0


@pytest.mark.unit
class TestBuildParams:
    def test_pop_typical_crest(self):
        """pop 典型 crest=20dB → base=0.44 → +enhance=0.05 → 0.49。"""
        r = build_params(_FakeCtx("pop"), post_crest_db=20.0)
        assert r["Effect"] == pytest.approx(0.49, abs=0.02)
        assert r["Curve"] == pytest.approx(0.30, abs=0.02)  # pop → -20
        assert r["Clip 0dB"] == 1.0  # pop → ON

    def test_folk_conservative(self):
        """folk 保守流派 — crest=10 → base=0.22 → Effect 低。"""
        r = build_params(_FakeCtx("folk"), post_crest_db=10.0)
        assert r["Effect"] <= 0.25
        assert r["Curve"] == pytest.approx(0.20, abs=0.02)  # folk → -30
        assert r["Clip 0dB"] == 1.0  # folk → ON

    def test_electronic_aggressive(self):
        """electronic 激进流派 — crest 高 → Effect 高。"""
        r = build_params(_FakeCtx("electronic"), post_crest_db=22.0)
        # base = 22*0.022=0.484 clamp→0.45, enhance=0.10 → 0.55
        assert r["Effect"] == pytest.approx(0.55, abs=0.02)
        assert r["Curve"] == pytest.approx(0.50, abs=0.02)  # electronic → 0
        assert r["Clip 0dB"] == 0.0  # electronic → OFF (valve)

    def test_rock_clip_off(self):
        """rock 流派 Clip 0dB OFF。"""
        r = build_params(_FakeCtx("rock"), post_crest_db=18.0)
        assert r["Clip 0dB"] == 0.0

    def test_chinese_folk_bel_canto(self):
        """民美流派 — 保守 Curve。"""
        r = build_params(_FakeCtx("chinese_folk_bel_canto"), post_crest_db=20.4)
        assert r["Curve"] == pytest.approx(0.20, abs=0.02)  # 民美 → -30
        assert r["Clip 0dB"] == 1.0

    def test_ballad_curve(self):
        """ballad Curve 保守。"""
        r = build_params(_FakeCtx("ballad"), post_crest_db=15.0)
        assert r["Curve"] == pytest.approx(0.20, abs=0.02)  # ballad → -30

    def test_zero_crest_min_effect(self):
        """crest=0 时 Effect 不低于下限 0.15。"""
        r = build_params(_FakeCtx("pop"), post_crest_db=0.0)
        assert r["Effect"] >= 0.15

    def test_high_crest_capped(self):
        """crest 很高时 Effect 不超过上限 0.55。"""
        r = build_params(_FakeCtx("electronic"), post_crest_db=30.0)
        # base = 30*0.022=0.66 clamp→0.45, enhance=0.10 → 0.55
        assert r["Effect"] <= 0.55

    def test_effect_scales_with_crest(self):
        """Effect 随 post_crest_db 增大而增大。"""
        r_low = build_params(_FakeCtx("pop"), post_crest_db=5.0)
        r_high = build_params(_FakeCtx("pop"), post_crest_db=20.0)
        assert r_high["Effect"] > r_low["Effect"]

    def test_fixed_params_always_set(self):
        """固定参数始终正确。"""
        r = build_params(_FakeCtx("rock"), post_crest_db=18.0)
        assert r["Input Gain"] == pytest.approx(0.333, abs=0.01)  # unity 0 dB
        assert r["Output Gain"] == 1.0   # unity 0 dB
        assert r["Band Split"] == 0.0
        assert r["In"] == 1.0

    def test_unknown_genre_defaults(self):
        """未知流派走默认值。"""
        r = build_params(_FakeCtx("jazz"), post_crest_db=15.0)
        # 应该不崩溃，返回合理值
        assert 0.15 <= r["Effect"] <= 0.55
        assert 0.0 <= r["Curve"] <= 1.0

    def test_pop_20db_crest(self):
        """望归实测场景：pop, crest=20.4 → base=0.449 → +0.05 = 0.499。"""
        r = build_params(_FakeCtx("pop"), post_crest_db=20.4)
        assert r["Effect"] == pytest.approx(0.50, abs=0.03)
        assert r["Input Gain"] == pytest.approx(0.333, abs=0.01)
        assert r["Output Gain"] == 1.0
