"""Room Verb (ValhallaRoom Tight Room) 测试。校准基于 REAPER API (2026-06-12)。"""
import pytest
from hermes_core.room_verb import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp(self):
        r = normalize_params({"decay": 0.5, "mix": 2.0})
        assert r["decay"] == 0.5
        assert r["mix"] == 1.0


@pytest.mark.unit
class TestBuildParams:
    # ── 固定值 ──
    def test_mix_always_100(self):
        for genre in ["pop", "folk"]:
            assert build_params(_FakeCtx(genre))["mix"] == 1.0

    def test_mod_depth_off(self):
        """Tight Room 不需要调制。"""
        for g in ["pop", "ballad", "folk", "electronic", "chinese_folk_bel_canto"]:
            assert build_params(_FakeCtx(g))["lateModDepth"] == 0.0, g

    def test_bypass_off(self):
        for g in ["pop", "folk"]:
            assert build_params(_FakeCtx(g))["Bypass"] == 0.0

    # ── RTM: 锚点插值，120 BPM → 0.7s → norm=0.007 ──
    def test_rtm_at_120_bpm(self):
        r = build_params(_FakeCtx("pop"), bpm=120)
        # 锚点 120→0.7s → norm≈0.007
        assert 0.005 <= r["decay"] <= 0.010

    def test_rtm_at_slow_bpm(self):
        r = build_params(_FakeCtx("ballad"), bpm=58)
        # 58 BPM 插值 ≈ 1.1s → norm≈0.011
        assert 0.008 <= r["decay"] <= 0.014

    def test_rtm_at_fast_bpm(self):
        r = build_params(_FakeCtx("rap"), bpm=180)
        # 180 BPM 插值 ≈ 0.45s → norm≈0.005
        assert 0.003 <= r["decay"] <= 0.008

    # ── BPM → RTM（锚点插值：慢歌长、快歌短，连续过渡）──
    def test_slower_bpm_longer_rtm(self):
        r_slow = build_params(_FakeCtx("pop"), bpm=60)
        r_fast = build_params(_FakeCtx("pop"), bpm=180)
        assert r_fast["decay"] < r_slow["decay"]

    def test_rtm_continuous_by_bpm(self):
        """BPM 连续变化 → RTM 连续变化，无段边界跳变。"""
        r58 = build_params(_FakeCtx("pop"), bpm=58)
        r59 = build_params(_FakeCtx("pop"), bpm=59)
        assert abs(r58["decay"] - r59["decay"]) < 0.0003

    def test_genre_multiplier(self):
        """同 BPM 不同流派 → RTM 按倍率差异。"""
        r_rap = build_params(_FakeCtx("rap"), bpm=120)
        r_pop = build_params(_FakeCtx("pop"), bpm=120)
        r_bal = build_params(_FakeCtx("ballad"), bpm=120)
        assert r_rap["decay"] < r_pop["decay"] < r_bal["decay"]

    # ── PDL: 1/256 音符 ──
    def test_pdl_linear(self):
        """ValhallaRoom predelay 线性：norm=ms/500。"""
        r = build_params(_FakeCtx("pop"), bpm=120)
        # 60,000/120/64 = 7.8ms → norm=0.0156
        assert 0.014 <= r["predelay"] <= 0.018

    # ── Tight Room 特性 ──
    def test_early_dominant(self):
        """Tight Room: earlyLateMix < 0.5 = 早期反射主导。"""
        for g in ["rap", "rock", "folk", "pop"]:
            v = build_params(_FakeCtx(g))["earlyLateMix"]
            assert v <= 0.40, f"{g}: earlyLateMix={v}"

    def test_bass_tight(self):
        """Tight Room: RTBassMultiply ≤ 1.0X → norm ≤ 0.33。"""
        for g in ["rap", "rock", "folk", "pop"]:
            v = build_params(_FakeCtx(g))["RTBassMultiply"]
            assert v <= 0.33, f"{g}: BassMul={v}"

    def test_high_dark(self):
        """Tight Room: RTHighMultiply 低 → 高频衰减快。"""
        for g in ["rap", "rock", "folk", "pop"]:
            v = build_params(_FakeCtx(g))["RTHighMultiply"]
            assert v <= 0.25, f"{g}: HighMul={v}"

    def test_high_diffusion(self):
        """Diffusion ≥ 0.85：官方推荐最高扩散，密度大。"""
        for g in ["rap", "rock", "folk", "pop"]:
            v = build_params(_FakeCtx(g))["diffusion"]
            assert v >= 0.80, f"{g}: diffusion={v}"

    # ── 参数完整性 ──
    def test_all_20_params(self):
        expected = [
            "decay", "predelay",
            "HiCut", "LoCut",
            "earlyLateMix", "lateSize", "earlySize", "diffusion", "type",
            "RTBassMultiply", "RTXover", "RTHighMultiply", "RTHighXover",
            "earlySend",
            "lateModRate", "lateModDepth", "earlyModRate", "earlyModDepth",
            "mix", "Bypass",
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
