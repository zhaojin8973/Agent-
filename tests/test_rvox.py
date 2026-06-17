"""RVox 独立模块测试。"""
import pytest
from hermes_core.rvox import build_params, normalize_params


class _FakeCtx:
    def __init__(self, genre="pop"):
        self.genre = genre
        self.presence_deficit = 0.0


@pytest.mark.unit
class TestNormalizeParams:
    def test_compression_clamp(self):
        r = normalize_params({"Compression": -4.3, "Gate": -120.0, "Gain": -2.6})
        assert 0 < r["Compression"] < 1
        assert r["Gate"] == 0.0
        assert 0 < r["Gain"] < 1

    def test_no_compression(self):
        r = normalize_params({"Compression": 0.0})
        assert r["Compression"] == 1.0  # 0dB = 无压缩 = 归一化 1.0


@pytest.mark.unit
class TestBuildParams:
    def test_pop(self):
        ctx = _FakeCtx("pop")
        r = build_params(ctx, gr_target_db=2.9)
        assert r["Compression"] == pytest.approx(-4.9, abs=0.2)  # 2.9×1.7≈4.9
        assert r["Gate"] == -120.0
        assert r["Gain"] == pytest.approx(-2.9, abs=0.2)

    def test_folk_low_mult(self):
        ctx = _FakeCtx("folk")
        r = build_params(ctx, gr_target_db=3.0)
        assert r["Compression"] == pytest.approx(-3.0, abs=0.2)  # 3.0×1.0=3.0

    def test_electronic_high_mult(self):
        ctx = _FakeCtx("electronic")
        r = build_params(ctx, gr_target_db=3.0)
        assert r["Compression"] == pytest.approx(-5.4, abs=0.2)  # 3.0×1.8=5.4

    def test_chinese_folk_bel_canto(self):
        ctx = _FakeCtx("chinese_folk_bel_canto")
        r = build_params(ctx, gr_target_db=2.9)
        assert r["Compression"] == pytest.approx(-4.3, abs=0.2)  # 2.9×1.5≈4.35
        assert r["Gain"] == pytest.approx(-2.6, abs=0.2)

    def test_gate_always_off(self):
        assert build_params(_FakeCtx(), gr_target_db=1.0)["Gate"] == -120.0

    def test_zero_gr(self):
        r = build_params(_FakeCtx(), gr_target_db=0.0)
        assert r["Compression"] == 0.0
        assert r["Gain"] == 0.0
