"""
延迟模块（ValhallaDelay / EchoBoy）— BPM 驱动三路延迟参数测试。
v3: ValhallaDelay 首选，已校准参数范围。
"""
import pytest
from hermes_core.delay import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


_ALL_GENRES = ["folk", "ballad", "pop", "rock", "rap", "electronic",
               "chinese_folk_bel_canto"]

_DELAY_TYPES = ["slap", "throw", "pingpong"]

_VD_KEYS = {
    "DelayL_Ms", "DelayR_Ms", "DelayLSync", "DelayRSync",
    "DelayLNote", "DelayRNote",
    "DelayStyle", "Mode", "Era",
    "Feedback", "DriveIn", "Age", "Diffusion", "DiffSize",
    "LowCut", "HighCut", "Width", "Mix",
    "ModRate", "ModDepth", "Ducking", "Bypass",
}


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp(self):
        r = normalize_params({"DelayL_Ms": 0.5, "Feedback": 2.0, "Mix": -0.5})
        assert r["DelayL_Ms"] == 0.5
        assert r["Feedback"] == 1.0
        assert r["Mix"] == 0.0


@pytest.mark.unit
class TestBuildParams:
    # ── 固定值 ──
    def test_keys_complete(self):
        for dtype in _DELAY_TYPES:
            r = build_params(_FakeCtx("pop"), bpm=120, delay_type=dtype)
            for key in _VD_KEYS:
                assert key in r, f"{dtype}: 缺少 '{key}'"

    def test_mix_always_100(self):
        for dtype in _DELAY_TYPES:
            for g in ["pop", "folk", "ballad"]:
                r = build_params(_FakeCtx(g), bpm=120, delay_type=dtype)
                assert r["Mix"] == 1.0

    # ── Slap — 固定时间 ──
    def test_slap_time_independent_of_bpm(self):
        r60 = build_params(_FakeCtx("pop"), bpm=60, delay_type="slap")
        r180 = build_params(_FakeCtx("pop"), bpm=180, delay_type="slap")
        assert r60["DelayL_Ms"] == r180["DelayL_Ms"]

    def test_slap_bpm_none_still_works(self):
        r = build_params(_FakeCtx("pop"), bpm=None, delay_type="slap")
        assert r["Bypass"] == 0.0
        assert r["DelayL_Ms"] > 0.0

    def test_slap_feedback_zero(self):
        for g in _ALL_GENRES:
            r = build_params(_FakeCtx(g), bpm=120, delay_type="slap")
            assert r["Feedback"] == 0.0, f"slap/{g} Feedback={r['Feedback']}"

    # ── Throw / PingPong — 音符制 ──
    def test_throw_note_based(self):
        r60 = build_params(_FakeCtx("pop"), bpm=60, delay_type="throw")
        r120 = build_params(_FakeCtx("pop"), bpm=120, delay_type="throw")
        # 60 BPM: 500ms, 120 BPM: 250ms
        assert abs(r60["DelayL_Ms"] - 0.5000) < 0.01
        assert abs(r120["DelayL_Ms"] - 0.2500) < 0.01

    def test_pingpong_note_based(self):
        r60 = build_params(_FakeCtx("pop"), bpm=60, delay_type="pingpong")
        r120 = build_params(_FakeCtx("pop"), bpm=120, delay_type="pingpong")
        # 60 BPM: 750ms → norm ~0.63 (非线性区间)
        # 120 BPM: 375ms → norm = 0.375 (线性区间)
        assert abs(r60["DelayL_Ms"] - 0.6304) < 0.01
        assert abs(r120["DelayL_Ms"] - 0.3750) < 0.01

    def test_throw_pingpong_independent_of_genre_mult(self):
        r_rap = build_params(_FakeCtx("rap"), bpm=120, delay_type="throw")
        r_bal = build_params(_FakeCtx("ballad"), bpm=120, delay_type="throw")
        assert r_rap["DelayL_Ms"] == r_bal["DelayL_Ms"]

    # ── Bypass ──
    def test_throw_bypass_when_no_bpm(self):
        r = build_params(_FakeCtx("pop"), bpm=None, delay_type="throw")
        assert r["Bypass"] == 1.0

    def test_pingpong_bypass_when_no_bpm(self):
        r = build_params(_FakeCtx("pop"), bpm=None, delay_type="pingpong")
        assert r["Bypass"] == 1.0

    def test_slap_never_bypass(self):
        r1 = build_params(_FakeCtx("pop"), bpm=None, delay_type="slap")
        r2 = build_params(_FakeCtx("pop"), bpm=120, delay_type="slap")
        assert r1["Bypass"] == 0.0
        assert r2["Bypass"] == 0.0

    # ── DelayStyle ──
    def test_pingpong_style(self):
        r_s = build_params(_FakeCtx("pop"), bpm=120, delay_type="slap")
        r_t = build_params(_FakeCtx("pop"), bpm=120, delay_type="throw")
        r_p = build_params(_FakeCtx("pop"), bpm=120, delay_type="pingpong")
        assert r_s["DelayStyle"] == 0.0       # Single
        assert r_t["DelayStyle"] == 0.0
        assert abs(r_p["DelayStyle"] - 0.625) < 0.01  # PingPong

    # ── 延迟类型排序 ──
    def test_slap_shortest_pingpong_longest(self):
        r_s = build_params(_FakeCtx("pop"), bpm=120, delay_type="slap")
        r_t = build_params(_FakeCtx("pop"), bpm=120, delay_type="throw")
        r_p = build_params(_FakeCtx("pop"), bpm=120, delay_type="pingpong")
        assert r_s["DelayL_Ms"] < r_t["DelayL_Ms"] < r_p["DelayL_Ms"]

    # ── Feedback ──
    def test_throw_feedback_range(self):
        for g in _ALL_GENRES:
            r = build_params(_FakeCtx(g), bpm=120, delay_type="throw")
            assert 0.06 <= r["Feedback"] <= 0.14, f"throw/{g} fb={r['Feedback']}"

    def test_pingpong_feedback_range(self):
        for g in _ALL_GENRES:
            r = build_params(_FakeCtx(g), bpm=120, delay_type="pingpong")
            assert 0.10 <= r["Feedback"] <= 0.21, f"pp/{g} fb={r['Feedback']}"

    # ── 范围 ──
    def test_all_in_range(self):
        for g in _ALL_GENRES:
            for dtype in _DELAY_TYPES:
                r = build_params(_FakeCtx(g), bpm=120, delay_type=dtype)
                for k, v in r.items():
                    assert 0.0 <= v <= 1.0, f"{g}/{dtype}/{k}={v}"

    def test_extreme_bpm_still_in_range(self):
        for bpm in [30, 240]:
            for dtype in ["throw", "pingpong"]:
                r = build_params(_FakeCtx("pop"), bpm=bpm, delay_type=dtype)
                for k, v in r.items():
                    assert 0.0 <= v <= 1.0, f"bpm={bpm}/{dtype}/{k}={v}"

    # ── 回退 ──
    def test_default_delay_type_is_slap(self):
        r = build_params(_FakeCtx("pop"), bpm=120)
        r_s = build_params(_FakeCtx("pop"), bpm=120, delay_type="slap")
        for k in _VD_KEYS:
            assert r[k] == r_s[k], f"default vs slap: {k}"

    def test_unknown_genre_falls_back_to_pop(self):
        r = build_params(_FakeCtx("unknown_genre"), bpm=120, delay_type="slap")
        r_pop = build_params(_FakeCtx("pop"), bpm=120, delay_type="slap")
        for k in _VD_KEYS:
            assert r[k] == r_pop[k], f"unknown/{k}={r[k]} vs pop/{k}={r_pop[k]}"

    # ── 滤波器流派排序 ──
    def test_filter_by_genre_ordering(self):
        for dtype in _DELAY_TYPES:
            r_folk = build_params(_FakeCtx("folk"), bpm=120, delay_type=dtype)
            r_elec = build_params(_FakeCtx("electronic"), bpm=120, delay_type=dtype)
            assert r_folk["LowCut"] <= r_elec["LowCut"]
            assert r_folk["HighCut"] <= r_elec["HighCut"]
