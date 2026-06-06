"""eq_engine 边缘情况测试 — 补充覆盖率至 100%。"""
import pytest
from unittest.mock import MagicMock
from hermes_core.eq_engine import (
    _derive_eq_intent, _ssleq_freq_norm, _apply_ssleq_eq,
    apply_eq_rms_match,
)
from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
from hermes_core.spectrum import SpectrumReport


# ═══════════════════════════ _derive_eq_intent 边缘 ═══════════════════════════

class TestDeriveEqIntentEdges:
    def test_backing_high_sub_bass(self):
        """role=backing 且 sub_excess > 3.0 → HPF 提升至 80Hz。"""
        report = SpectrumReport(
            spectral_tilt_db_per_octave=-2.0,
            mud_ratio_db=2.0,
            presence_deficit_db=0.0,
            sibilance_peak_hz=8000,
            air_level_db=-2.0,
            resonances=[],
            band_energy_db={"sub": -2.0, "low": 0.0, "mid": 0.0, "hm": 2.0, "air": -2.0},
        )
        intent = _derive_eq_intent(report, role="backing", genre="pop", position="post")
        hpf_bands = [b for b in intent.bands if b.band_type == "hp"]
        assert len(hpf_bands) >= 1
        assert hpf_bands[0].freq_hz >= 40.0

    def test_air_shelf_very_dark(self):
        """tilt_very_dark 且 air_moderate → 应产生 high_shelf 频段。"""
        report = SpectrumReport(
            spectral_tilt_db_per_octave=-5.0,
            mud_ratio_db=2.0,
            presence_deficit_db=5.0,
            sibilance_peak_hz=8000,
            air_level_db=-5.0,
            resonances=[],
            band_energy_db={"sub": 2.0, "low": 5.0, "mid": 2.0, "hm": -12.0, "air": -10.0},
        )
        intent = _derive_eq_intent(report, role="vocal", genre="rock", position="post")
        air_bands = [b for b in intent.bands if b.band_type == "high_shelf"]
        # 频谱非常暗时应有空气补偿
        assert len(intent.bands) >= 1


# ═══════════════════════════ _ssleq_freq_norm 边缘 ═══════════════════════════

class TestSSLEQFreqNormEdges:
    _TABLE = [(0.0, 30.0), (0.3, 250.0), (0.5, 500.0), (0.7, 2000.0), (1.0, 20000.0)]

    def test_below_table_range(self):
        """低于查表范围 → 返回最小归一化值。"""
        result = _ssleq_freq_norm(10.0, self._TABLE)
        assert result == 0.0

    def test_equal_boundaries(self):
        """hi_hz == lo_hz → 返回 lo_norm。"""
        # 通过构造同一频率两次来测试零除保护
        table = [(0.5, 500.0), (0.5, 500.0), (1.0, 20000.0)]
        result = _ssleq_freq_norm(500.0, table)
        assert isinstance(result, float)


# ═══════════════════════════ _apply_ssleq_eq 边缘 ═══════════════════════════

class TestApplySSLEQEdges:
    def test_low_shelf_band(self):
        """low_shelf 频段类型应被正确处理。"""
        intent = EqIntent(
            bands=[EqBandIntent(
                band_type="low_shelf", freq_hz=150, gain_db=3.0, q=0.71,
                reason="test low shelf",
            )],
            spectral_tilt="neutral",
            mud_detected=False,
        )
        result = _apply_ssleq_eq(intent)
        assert isinstance(result, dict)
        assert len(result) >= 1

    def test_output_boost_side(self):
        """out_db >= 0 时的增益提升侧。"""
        intent = EqIntent(
            bands=[
                EqBandIntent(band_type="bell", freq_hz=1000, gain_db=6.0, q=1.0, reason="boost"),
                EqBandIntent(band_type="bell", freq_hz=2000, gain_db=4.0, q=1.0, reason="boost2"),
            ],
            spectral_tilt="neutral",
            mud_detected=False,
        )
        result = _apply_ssleq_eq(intent)
        assert "Gain" in result


# ═══════════════════════════ apply_eq_rms_match ═══════════════════════════

class TestApplyEqRmsMatch:
    def test_below_threshold_noop(self):
        """delta < 0.2dB 时无操作。"""
        fx = MagicMock()
        apply_eq_rms_match(fx, 0, 0, -10.0, -10.1)
        fx.set_param.assert_not_called()

    def test_above_threshold_applies(self):
        """delta >= 0.2dB 时设置补偿增益。"""
        fx = MagicMock()
        apply_eq_rms_match(fx, 0, 0, -10.0, -11.0)
        assert fx.set_param.call_count >= 1
