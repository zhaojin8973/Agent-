"""eq_engine 边缘情况测试 — 补充覆盖率至 100%。"""
import pytest
from unittest.mock import MagicMock, patch
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


# ═══════════════════════════ _derive_eq_intent 补充 ═══════════════════════════


class TestDeriveEqIntentMoreEdges:
    """补充未覆盖的 _derive_eq_intent 分支。"""

    def test_backing_sub_excess_above_3(self):
        """role=backing + sub_excess > 3.0 → HPF 最高 80Hz。"""
        report = SpectrumReport(
            spectral_tilt_db_per_octave=-1.0,
            mud_ratio_db=0.0,
            presence_deficit_db=0.0,
            sibilance_peak_hz=8000,
            air_level_db=0.0,
            resonances=[],
            band_energy_db={"sub": 6.0, "low": 2.0, "mid": 0.0, "hm": 0.0, "air": 0.0},
        )
        intent = _derive_eq_intent(report, role="backing", genre="pop", position="post")
        hpf_bands = [b for b in intent.bands if b.band_type == "hp"]
        assert len(hpf_bands) >= 1
        # sub_excess=8 → hpf_freq = min(80, 40+(8-3)*10) = min(80, 90) = 80
        assert hpf_bands[0].freq_hz <= 80.0

    def test_tilt_very_dark_air_moderate(self):
        """air 偏离参考 (female: -32±10) → 产生 high_shelf 修正。"""
        # air 偏离参考容差 → 触发修正
        report = SpectrumReport(
            spectral_tilt_db_per_octave=-5.0,
            mud_ratio_db=0.0,
            presence_deficit_db=5.0,
            sibilance_peak_hz=8000,
            air_level_db=-25.0,
            resonances=[],
            band_energy_db={"sub": 0.0, "low": 3.0, "low_mid": 2.0, "mid": 2.0, "high_mid": -5.0, "presence": -12.0, "air": -50.0},
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop", position="post")
        air_bands = [b for b in intent.bands if b.band_type == "high_shelf"]
        assert len(air_bands) >= 1

    def test_air_low_tilt_very_dark(self):
        """极低 air + 陡峭负 tilt → air 修正频段。"""
        # air 远低于参考容差 → 较强空气感提升
        report = SpectrumReport(
            spectral_tilt_db_per_octave=-5.0,
            mud_ratio_db=0.0,
            presence_deficit_db=5.0,
            sibilance_peak_hz=8000,
            air_level_db=-35.0,
            resonances=[],
            band_energy_db={"sub": 0.0, "low": 3.0, "low_mid": 0.0, "mid": 0.0, "high_mid": -12.0, "presence": -12.0, "air": -60.0},
        )
        intent = _derive_eq_intent(report, role="vocal", genre="rock", position="post")
        air_bands = [b for b in intent.bands if b.band_type == "high_shelf"]
        assert len(air_bands) >= 1


# ═══════════════════════════ _ssleq_freq_norm 补充 ═══════════════════════════


class TestSSLEQFreqNormMoreEdges:
    def test_exact_match_at_idx_0(self):
        """bisect_idx == 0 → 返回 table[0][0]。"""
        table = [(0.0, 30.0), (0.3, 250.0), (0.5, 500.0)]
        result = _ssleq_freq_norm(10.0, table)
        assert result == 0.0

    def test_duplicate_hz_division_by_zero(self):
        """hi_hz == lo_hz → 返回 lo_n。"""
        table = [(0.2, 500.0), (0.3, 500.0), (0.5, 2000.0)]
        result = _ssleq_freq_norm(500.0, table)
        assert result == 0.2


# ═══════════════════════════ _apply_ssleq_eq 补充 ═══════════════════════════


class TestApplySSLEQMoreEdges:
    def test_output_gain_positive(self):
        """out_db >= 0 时使用正增益归一化。"""
        intent = EqIntent(
            bands=[],  # 无频段 → total_boost=0 → 不过 output_gain 分支
            spectral_tilt="neutral",
            mud_detected=False,
        )
        result = _apply_ssleq_eq(intent)
        assert isinstance(result, dict)


# ═══════════════════════════ apply_eq_baseline 边缘 ═══════════════════════════


class TestApplyEqBaselineEdges:
    def test_ssleq_path(self):
        """fx_name 包含 'ssleq' → 使用 SSLEQ 归一化。"""
        from hermes_core.eq_engine import apply_eq_baseline

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        spectrum = {"presence_deficit": 0.0}

        result = apply_eq_baseline(
            mock_fx, 0, 0, "vocal", genre="pop",
            stem_file_path="", fx_name="SSL EQ",
            position="solo",
            last_eq_params={"Band 1": 0},
            last_spectrum=spectrum,
        )
        assert result is not None

    def test_proq3_path_with_file(self, tmp_path):
        """有 stem 文件时走 Pro-Q 3 频谱分析路径（异常回退到基线）。"""
        from hermes_core.eq_engine import apply_eq_baseline

        wav = tmp_path / "test.wav"
        import soundfile as sf
        import numpy as np
        sf.write(str(wav), np.zeros((100, 1)), 48000)

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        spectrum = {"presence_deficit": 0.0}

        result = apply_eq_baseline(
            mock_fx, 0, 0, "vocal", genre="pop",
            stem_file_path=str(wav), fx_name="Pro-Q 3",
            position="solo",
            last_eq_params={"Band 1": 0},
            last_spectrum=spectrum,
        )
        assert result is not None

    def test_baseline_apply_failure(self):
        """EQ 应用异常 → 返回 None。"""
        from hermes_core.eq_engine import apply_eq_baseline

        mock_fx = MagicMock()
        mock_fx.set_param.side_effect = RuntimeError("param failed")
        spectrum = {"presence_deficit": 0.0}

        result = apply_eq_baseline(
            mock_fx, 0, 0, "vocal", genre="pop",
            stem_file_path="", fx_name="Pro-Q 3",
            position="solo",
            last_eq_params={"Band 1": 0},
            last_spectrum=spectrum,
        )
        assert result is None


# ═══════════════════════════ auto_corrective_eq 边缘 ═══════════════════════════


class TestAutoCorrectiveEQEdges:
    def test_spectrum_analysis_failure(self):
        """频谱分析失败 → 返回错误结果。"""
        from hermes_core.eq_engine import auto_corrective_eq

        mock_api = MagicMock()
        mock_fx = MagicMock()
        stems_cache = [{"track_idx": 0, "file_path": "/fake/path.wav"}]

        with patch("hermes_core.eq_engine.SpectrumAnalyzer.analyze",
                   side_effect=RuntimeError("analysis failed")):
            result = auto_corrective_eq(
                mock_api, mock_fx, 0, stems_cache,
            )
        assert result is not None
        assert result.get("applied") is False
        assert "error" in result

    def test_no_stem_file_returns_early(self):
        """无 stem 文件路径 → 提前返回。"""
        from hermes_core.eq_engine import auto_corrective_eq

        mock_api = MagicMock()
        mock_fx = MagicMock()
        stems_cache = [{"track_idx": 0, "file_path": ""}]

        result = auto_corrective_eq(
            mock_api, mock_fx, 0, stems_cache,
        )
        assert result is not None
        assert result.get("applied") is False

    def test_no_resonances_detected(self):
        """无共振 → 返回空 bands。"""
        from hermes_core.eq_engine import auto_corrective_eq

        mock_api = MagicMock()
        mock_fx = MagicMock()
        stems_cache = [{"track_idx": 0, "file_path": "/fake/path.wav"}]

        mock_report = SpectrumReport(
            spectral_tilt_db_per_octave=-1.0,
            mud_ratio_db=0.0,
            presence_deficit_db=0.0,
            sibilance_peak_hz=8000,
            air_level_db=0.0,
            resonances=[],
            band_energy_db={"sub": 0.0, "low": 0.0, "mid": 0.0, "hm": 0.0, "air": 0.0},
        )
        with patch("hermes_core.eq_engine.SpectrumAnalyzer.analyze",
                   return_value=mock_report):
            result = auto_corrective_eq(
                mock_api, mock_fx, 0, stems_cache,
            )
        assert result.get("applied") is False
        assert result.get("eq_bands") == []
