"""Shadow Hills Mastering Compressor 测试 — crest+流派偏移驱动阈值。"""
import pytest
from hermes_core.shadow_hills import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre
        self.fx_name = "VST3: Shadow Hills Mastering Compressor (Plugin Alliance)"


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp_all_values(self):
        r = normalize_params({"Optical Threshold 1": 0.3, "Optical Gain 1": 0.2})
        for v in r.values():
            assert 0.0 <= v <= 1.0


@pytest.mark.unit
class TestBuildParams:
    # ── 民美：crest=17.4, offset=-0.05 → 保留动态，少压 ──
    def test_chinese_folk_bel_canto_light(self):
        """民美 crest=17.4 → thresh=0.45-0.139-0.05=0.261 → panel 7, 1.5dB。"""
        r = build_params(_FakeCtx("chinese_folk_bel_canto"), post_crest_db=17.4)
        assert r["Optical Threshold 1"] == pytest.approx(0.26, abs=0.02)
        assert r["Transformer 1"] == 1.0  # Nickel

    def test_folk_light(self):
        """folk offset=-0.05，和民美一样保守。"""
        r = build_params(_FakeCtx("folk"), post_crest_db=15.0)
        assert r["Optical Threshold 1"] < 0.32  # 偏低压

    # ── ballad：适中偏保守 ──
    def test_ballad_moderate(self):
        """ballad offset=-0.02 → 适中偏保守。"""
        r = build_params(_FakeCtx("ballad"), post_crest_db=12.0)
        assert 0.25 <= r["Optical Threshold 1"] <= 0.38

    # ── pop：标准 ──
    def test_pop_standard(self):
        """pop offset=0 → crest=12 → thresh=0.45-0.096=0.354 → panel 9, 3dB。"""
        r = build_params(_FakeCtx("pop"), post_crest_db=12.0)
        assert r["Optical Threshold 1"] == pytest.approx(0.35, abs=0.02)
        assert r["Transformer 1"] == 0.5  # Iron

    def test_pop_dynamic(self):
        """pop 动态人声(crest=18) → thresh=0.45-0.144=0.306 → panel 8, 2dB。"""
        r = build_params(_FakeCtx("pop"), post_crest_db=18.0)
        assert r["Optical Threshold 1"] == pytest.approx(0.31, abs=0.02)

    # ── rock：可多压 ──
    def test_rock_heavier(self):
        """rock offset=+0.05 → crest=10 → thresh=0.45-0.08+0.05=0.42 → panel 10, 4dB。"""
        r = build_params(_FakeCtx("rock"), post_crest_db=10.0)
        assert r["Optical Threshold 1"] == pytest.approx(0.42, abs=0.02)
        assert r["Transformer 1"] == 0.0  # Steel

    # ── electronic：最激进 ──
    def test_electronic_max(self):
        """electronic offset=+0.08 → crest=8。"""
        r = build_params(_FakeCtx("electronic"), post_crest_db=8.0)
        assert r["Optical Threshold 1"] >= 0.35
        assert r["Transformer 1"] == 0.0  # Steel

    # ── 阈值范围约束 ──
    def test_thresh_clamped(self):
        """极端值被钳制。"""
        r_hi = build_params(_FakeCtx("electronic"), post_crest_db=0.0)
        assert r_hi["Optical Threshold 1"] <= 0.42
        r_lo = build_params(_FakeCtx("folk"), post_crest_db=30.0)
        assert r_lo["Optical Threshold 1"] >= 0.22

    # ── 增益联动 ──
    def test_gain_tracks_threshold(self):
        """压越多（thresh高）→ 补越多（gain高）。"""
        r_lo = build_params(_FakeCtx("folk"), post_crest_db=20.0)    # 低 thresh
        r_hi = build_params(_FakeCtx("electronic"), post_crest_db=8.0)  # 高 thresh
        assert r_hi["Optical Gain 1"] > r_lo["Optical Gain 1"]

    # ── 离散级始终旁路 ──
    def test_discrete_bypass(self):
        r = build_params(_FakeCtx("rock"), post_crest_db=15.0)
        assert r["Discrete Bypass 1"] == 0.0

    # ── 未知流派 ──
    def test_unknown_genre(self):
        r = build_params(_FakeCtx("jazz"), post_crest_db=12.0)
        assert 0.22 <= r["Optical Threshold 1"] <= 0.42
