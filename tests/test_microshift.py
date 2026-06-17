"""MicroShift 独立模块测试。"""
import pytest
from hermes_core.microshift import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp(self):
        r = normalize_params({"Detune": 0.5, "Delay": 0.5})
        for v in r.values():
            assert 0.0 <= v <= 1.0


@pytest.mark.unit
class TestBuildParams:
    def test_mix_always_100(self):
        r = build_params(_FakeCtx("pop"))
        assert r["Mix"] == 1.0

    def test_input_gain_unity(self):
        r = build_params(_FakeCtx("pop"))
        assert r["InputGain"] == pytest.approx(0.333, abs=0.01)

    # ── Style ──
    def test_style_folk_I(self):
        assert build_params(_FakeCtx("folk"))["Style"] == 0.0

    def test_style_electronic_III(self):
        assert build_params(_FakeCtx("electronic"))["Style"] == 1.0

    def test_style_pop_I(self):
        assert build_params(_FakeCtx("pop"))["Style"] == 0.0

    # ── Focus ──
    def test_focus_folk_low(self):
        """folk Focus 低（保守）。"""
        assert build_params(_FakeCtx("folk"))["Focus"] <= 0.15

    def test_focus_electronic_high(self):
        """electronic Focus 高（只展宽高频）。"""
        assert build_params(_FakeCtx("electronic"))["Focus"] >= 0.35

    # ── Detune/Delay: crest 驱动 ──
    def test_detune_decreases_with_crest(self):
        """crest 高 → Detune 降。"""
        r_lo = build_params(_FakeCtx("pop"), post_crest_db=5.0)
        r_hi = build_params(_FakeCtx("pop"), post_crest_db=20.0)
        assert r_hi["Detune"] < r_lo["Detune"]

    def test_detune_in_range(self):
        r = build_params(_FakeCtx("pop"), post_crest_db=17.4)
        assert 0.25 <= r["Detune"] <= 0.70
