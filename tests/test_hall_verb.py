"""Hall Verb 测试 — Seventh Heaven（预设优先）+ ValhallaVintageVerb。"""
import pytest
from hermes_core.hall_verb import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp(self):
        r = normalize_params({"Decay Time": 0.5, "Dry/Wet Mix": 2.0})
        assert r["Decay Time"] == 0.5
        assert r["Dry/Wet Mix"] == 1.0


@pytest.mark.unit
class TestBuildParams:
    # ── 固定值 ──
    def test_mix_always_100(self):
        for genre in ["pop", "folk", "ballad"]:
            params = build_params(_FakeCtx(genre))
            mix_key = "Dry/Wet Mix" if "Dry/Wet Mix" in params else "Mix"
            assert params[mix_key] == 1.0

    def test_bypass_off(self):
        for g in ["folk", "pop"]:
            params = build_params(_FakeCtx(g))
            if "Bypass" in params:
                assert params["Bypass"] == 0.0

    # ── 插件选择 ──
    def test_seventh_heaven_genres(self):
        """folk/ballad/民美 → Seventh Heaven 仅覆盖时值+预设。"""
        for g in ["folk", "ballad", "chinese_folk_bel_canto"]:
            params = build_params(_FakeCtx(g))
            assert "Content Bank" in params, f"{g}: missing preset bank"
            assert "Content Preset" in params, f"{g}: missing preset index"
            assert "Decay Time" in params, f"{g}: missing Decay Time"
            assert "Pre-delay" in params, f"{g}: missing Pre-delay"
            # 其他参数（Ducker/EarlyLate/VLF等）由预设决定，不覆盖

    def test_vintage_verb_genres(self):
        """pop/rock/rap/electronic → ValhallaVintageVerb 参数格式。"""
        for g in ["pop", "rock", "rap", "electronic"]:
            params = build_params(_FakeCtx(g))
            assert "Decay" in params, f"{g}: missing VV key"
            assert "ReverbMode" in params

    # ── 预设选择 ──
    def test_preset_selection(self):
        """不同 SH 流派选不同预设。"""
        p_folk = build_params(_FakeCtx("folk"))
        p_bal = build_params(_FakeCtx("ballad"))
        # folk → Chambers, ballad → Halls (different banks)
        assert (p_folk["Content Bank"], p_folk["Content Preset"]) != \
               (p_bal["Content Bank"], p_bal["Content Preset"])

    # ── 预设参数不覆盖 — Ducker/音色等由预设决定 ──
    def test_no_tone_overrides_in_sh(self):
        """Seventh Heaven 仅覆盖时值，不覆盖音色/Ducker。"""
        for g in ["folk", "ballad"]:
            p = build_params(_FakeCtx(g))
            assert "Ducker Enable" not in p, f"{g}: ducker should use preset default"
            assert "Early / Late Level" not in p, f"{g}: EarlyLate should use preset default"

    # ── RTM: 校准曲线 ──
    def test_rtm_seventh_heaven(self):
        r = build_params(_FakeCtx("ballad"), bpm=120)
        # 锚点 120→2.0s × 1.00 → 2.0s → norm≈0.22
        assert 0.15 <= r["Decay Time"] <= 0.30

    def test_rtm_vintage_verb(self):
        r = build_params(_FakeCtx("pop"), bpm=120)
        # 锚点 120→2.0s × 0.90 → 1.8s → norm≈0.036
        assert 0.02 <= r["Decay"] <= 0.06

    def test_genre_multiplier(self):
        """同 BPM 不同流派 → RTM 按倍率差异。"""
        r_rap = build_params(_FakeCtx("rap"), bpm=120)
        r_pop = build_params(_FakeCtx("pop"), bpm=120)
        r_bal = build_params(_FakeCtx("ballad"), bpm=120)
        assert r_rap["Decay"] < r_pop["Decay"] < r_bal["Decay Time"]

    # ── BPM 驱动 ──
    def test_slower_bpm_longer_rtm(self):
        r_slow = build_params(_FakeCtx("pop"), bpm=60)
        r_fast = build_params(_FakeCtx("pop"), bpm=180)
        assert r_fast["Decay"] < r_slow["Decay"]

    # ── VintageVerb 模式 ──
    def test_concert_hall_mode(self):
        for g in ["pop", "electronic"]:
            assert build_params(_FakeCtx(g))["ReverbMode"] == 0.75

    def test_chamber_mode(self):
        for g in ["rock", "rap"]:
            assert build_params(_FakeCtx(g))["ReverbMode"] == 0.50

    # ── 范围验证 ──
    def test_all_in_range(self):
        for g in ["folk", "ballad", "pop", "rock", "rap", "electronic",
                   "chinese_folk_bel_canto"]:
            params = build_params(_FakeCtx(g), bpm=120)
            for k, v in params.items():
                assert 0.0 <= v <= 1.0, f"{g}/{k}={v}"
