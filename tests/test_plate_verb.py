"""Plate Verb 校准后测试。所有 norm 值基于 REAPER API 实时校准(2026-06-11)。"""
import pytest
from hermes_core.plate_verb import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp(self):
        r = normalize_params({"E1: Size (SIZ)": 0.5, "E1: Mix (MIX)": 2.0})
        assert r["E1: Size (SIZ)"] == 0.5
        assert r["E1: Mix (MIX)"] == 1.0


@pytest.mark.unit
class TestBuildParams:
    # ── 固定值 ──
    def test_mix_always_100(self):
        for genre in ["pop", "folk"]:
            assert build_params(_FakeCtx(genre))["E1: Mix (MIX)"] == 1.0

    def test_algorithm_plate_room(self):
        for g in ["pop", "ballad", "folk", "electronic", "chinese_folk_bel_canto"]:
            assert build_params(_FakeCtx(g))["E1: Algorithm"] == 0.5, g

    # ── RTM: BPM 锚点插值，120 BPM → 1.5s → norm≈0.67 ──
    def test_rtm_at_120_bpm(self):
        r = build_params(_FakeCtx("pop"), bpm=120)
        # 锚点 120→1.5s → norm≈0.67
        assert 0.60 <= r["E1: Reverb Time Mid (RTM)"] <= 0.80

    def test_rtm_at_slow_bpm(self):
        r = build_params(_FakeCtx("ballad"), bpm=58)
        # 58 BPM 插值 ≈ 2.28s → norm≈0.77
        assert 0.70 <= r["E1: Reverb Time Mid (RTM)"] <= 0.85

    def test_rtm_at_fast_bpm(self):
        r = build_params(_FakeCtx("rap"), bpm=180)
        # 180 BPM 插值 ≈ 0.90s → norm≈0.52
        assert 0.45 <= r["E1: Reverb Time Mid (RTM)"] <= 0.65

    # ── SHAPE: 板混响必须低 ──
    def test_shape_always_low(self):
        for g in ["pop", "folk", "rock", "rap", "chinese_folk_bel_canto"]:
            assert build_params(_FakeCtx(g))["E1: Shape (SHP)"] <= 0.08, g

    # ── BPM → RTM（锚点插值：慢歌长、快歌短，连续过渡）──
    def test_slower_bpm_longer_rtm(self):
        r_slow = build_params(_FakeCtx("pop"), bpm=60)
        r_fast = build_params(_FakeCtx("pop"), bpm=180)
        assert r_fast["E1: Reverb Time Mid (RTM)"] < r_slow["E1: Reverb Time Mid (RTM)"]

    def test_rtm_continuous_by_bpm(self):
        """BPM 连续变化 → RTM 连续变化，无段边界跳变。"""
        r58 = build_params(_FakeCtx("pop"), bpm=58)
        r59 = build_params(_FakeCtx("pop"), bpm=59)
        assert abs(r58["E1: Reverb Time Mid (RTM)"] -
                   r59["E1: Reverb Time Mid (RTM)"]) < 0.01

    def test_genre_multiplier(self):
        """同 BPM 不同流派 → RTM 按倍率差异。"""
        r_rap = build_params(_FakeCtx("rap"), bpm=120)
        r_pop = build_params(_FakeCtx("pop"), bpm=120)
        r_bal = build_params(_FakeCtx("ballad"), bpm=120)
        rtm = lambda r: r["E1: Reverb Time Mid (RTM)"]
        assert rtm(r_rap) < rtm(r_pop) < rtm(r_bal)

    # ── DIF 线性 ──
    def test_dif_linear(self):
        assert build_params(_FakeCtx("rap"))["E1: Diffusion (DIF)"] < \
               build_params(_FakeCtx("electronic"))["E1: Diffusion (DIF)"]

    # ── 参数完整性 ──
    def test_all_13_params(self):
        expected = [
            "E1: Algorithm", "E1: Size (SIZ)", "E1: Reverb Time Mid (RTM)",
            "E1: Shape (SHP)", "E1: Spread (SPR)", "E1: Pre Delay (PDL)",
            "E1: Width (WID)", "E1: High Frequency Cutoff (HFC)",
            "E1: Low Frequency Cutoff (LFC)", "E1: Diffusion (DIF)",
            "E1: Bass Multiply (BAS)", "E1: Decay Optimization (DCO)",
            "E1: Mix (MIX)",
        ]
        r = build_params(_FakeCtx("pop"))
        for key in expected:
            assert key in r, f"Missing {key}"

    def test_all_in_range(self):
        for g in ["folk", "ballad", "pop", "rock", "rap", "electronic",
                   "chinese_folk_bel_canto"]:
            r = build_params(_FakeCtx(g), bpm=120)
            for k, v in r.items():
                assert 0.0 <= v <= 1.0, f"{g}/{k}={v}"
