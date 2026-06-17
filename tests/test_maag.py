"""Maag EQ4 测试 — 只做 Air Band 抛光，body/presence 不碰。"""
import pytest
from hermes_core.maag import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre: str = "pop"):
        self.genre = genre


@pytest.mark.unit
class TestNormalizeParams:
    def test_clamp_all(self):
        r = normalize_params({"Air Gain": 0.5})
        for v in r.values():
            assert 0.0 <= v <= 1.0


@pytest.mark.unit
class TestBuildParams:
    # ── Air Band 频率 ──
    def test_folk_10k(self):
        assert build_params(_FakeCtx("folk"))["Air Band"] == 0.50

    def test_pop_20k(self):
        assert build_params(_FakeCtx("pop"))["Air Band"] == 0.75

    # ── Air Gain：望归实测 air_rel=-25.5, dev=+6.5(亮) → 最小 boost ──
    def test_air_bright_min_boost(self):
        r = build_params(_FakeCtx("pop"), gender="female", spectrum={
            "band_energy_db": {
                "mid": -54.6, "air": -80.1,
            },
        })
        # air_rel=-25.5, ref=-32, dev=+6.5 → min 1dB
        assert r["Air Gain"] <= 0.15

    # ── Air Gain：暗声 → boost ──
    def test_air_dark_boost(self):
        r = build_params(_FakeCtx("pop"), gender="female", spectrum={
            "band_energy_db": {
                "mid": -50.0, "air": -90.0,
            },
        })
        # air_rel=-40, ref=-32, dev=+8 → boost
        assert r["Air Gain"] > 0.10

    # ── 男声模板 ──
    def test_male_template(self):
        r = build_params(_FakeCtx("pop"), gender="male", spectrum={
            "band_energy_db": {"mid": -50.0, "air": -85.0},
        })
        # air_rel=-35, male ref=-37, dev=+2 → 轻度 boost
        assert 0.10 <= r["Air Gain"] <= 0.25

    # ── 160Hz/2.5kHz 始终 0dB ──
    def test_body_presence_untouched(self):
        r = build_params(_FakeCtx("pop"), gender="female", spectrum={
            "band_energy_db": {"mid": -54.6, "air": -80.1},
        })
        assert r["160 Hz"] == 0.5
        assert r["2.5 kHz"] == 0.5
        assert r["650 Hz"] == 0.5
        assert r["Sub"] == 0.5
        assert r["40 Hz"] == 0.5

    # ── 无频谱 → 保守 ──
    def test_no_spectrum_conservative(self):
        r = build_params(_FakeCtx("folk"))
        assert r["Air Gain"] <= 0.15
