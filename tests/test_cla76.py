"""Tests for CLA-76 parameter translation and normalization.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_cla76.py -v
"""

import pytest

from hermes_core.normalize import normalize_params, normalize_param, PLUGIN_REGISTRY
from hermes_core.loudness_optimizer import CompressionIntent


# ══════════════════════════════════════════════════════════════
# CLA-76 plugin registration
# ══════════════════════════════════════════════════════════════

CLA76_NAME = "VST3: CLA-76 Mono (Waves)"


def test_cla76_registered():
    """CLA-76 must be registered in PLUGIN_REGISTRY."""
    assert CLA76_NAME in PLUGIN_REGISTRY, (
        f"{CLA76_NAME} not found in PLUGIN_REGISTRY"
    )
    assert PLUGIN_REGISTRY[CLA76_NAME]["type"] == "fet"


def test_cla76_core_params_exist():
    """Input, Output, Attack, Release must be registered."""
    params = PLUGIN_REGISTRY[CLA76_NAME]["params"]
    for p in ("Input", "Output", "Attack", "Release"):
        assert p in params, f"Missing param: {p}"


# ══════════════════════════════════════════════════════════════
# Input / Output normalization (linear -48..0 dB)
# ══════════════════════════════════════════════════════════════

class TestInputNormalization:
    """CLA-76 Input range is -48..0 dB, linear."""

    def test_0db_is_norm_1(self):
        assert normalize_param(CLA76_NAME, "Input", 0.0) == pytest.approx(1.0)

    def test_minus48_is_norm_0(self):
        assert normalize_param(CLA76_NAME, "Input", -48.0) == pytest.approx(0.0)

    def test_midpoint(self):
        assert normalize_param(CLA76_NAME, "Input", -24.0) == pytest.approx(0.5)

    def test_clamps_below(self):
        assert normalize_param(CLA76_NAME, "Input", -60.0) == 0.0

    def test_clamps_above(self):
        assert normalize_param(CLA76_NAME, "Input", 10.0) == 1.0


class TestOutputNormalization:
    """CLA-76 Output range is -48..0 dB, linear."""

    def test_0db_is_norm_1(self):
        assert normalize_param(CLA76_NAME, "Output", 0.0) == pytest.approx(1.0)

    def test_minus48_is_norm_0(self):
        assert normalize_param(CLA76_NAME, "Output", -48.0) == pytest.approx(0.0)


# ══════════════════════════════════════════════════════════════
# Attack / Release normalization (1-7 knob, linear)
# ══════════════════════════════════════════════════════════════

class TestAttackNormalization:
    """CLA-76 Attack range is 1.0-7.0 knob positions."""

    def test_1_is_norm_0(self):
        assert normalize_param(CLA76_NAME, "Attack", 1.0) == pytest.approx(0.0)

    def test_7_is_norm_1(self):
        assert normalize_param(CLA76_NAME, "Attack", 7.0) == pytest.approx(1.0)

    def test_midpoint(self):
        assert normalize_param(CLA76_NAME, "Attack", 4.0) == pytest.approx(0.5)

    def test_clamp_low(self):
        assert normalize_param(CLA76_NAME, "Attack", 0.0) == 0.0

    def test_clamp_high(self):
        assert normalize_param(CLA76_NAME, "Attack", 10.0) == 1.0


class TestReleaseNormalization:
    """CLA-76 Release range is 1.0-7.0 knob positions."""

    def test_1_is_norm_0(self):
        assert normalize_param(CLA76_NAME, "Release", 1.0) == pytest.approx(0.0)

    def test_7_is_norm_1(self):
        assert normalize_param(CLA76_NAME, "Release", 7.0) == pytest.approx(1.0)


# ══════════════════════════════════════════════════════════════
# ms → CLA-76 knob conversion
# ══════════════════════════════════════════════════════════════

class TestMsToCla76Attack:
    """ms → CLA-76 Attack knob position (1-7, CW=fast)."""

    def _call(self, ms):
        from hermes_core.engine import _ms_to_cla76_attack
        return _ms_to_cla76_attack(ms)

    def test_very_fast(self):
        """0.02ms → knob 7 (fastest)."""
        assert self._call(0.02) == pytest.approx(7.0, abs=0.5)

    def test_bpm_130(self):
        """3ms → knob 4.0 (FAST BPM)."""
        assert self._call(3.0) == pytest.approx(4.0)

    def test_bpm_100(self):
        """5ms → knob 3.0 (MED BPM)."""
        assert self._call(5.0) == pytest.approx(3.0)

    def test_bpm_60(self):
        """10ms → knob ~2.25 (usable slow, not extreme)."""
        assert self._call(10.0) == pytest.approx(2.25, abs=0.5)

    def test_clamp_fastest(self):
        assert self._call(0.001) <= 7.0

    def test_clamp_slowest(self):
        assert self._call(100.0) >= 1.0


class TestMsToCla76Release:
    """ms → CLA-76 Release knob position (1-7, CW=fast)."""

    def _call(self, ms):
        from hermes_core.engine import _ms_to_cla76_release
        return _ms_to_cla76_release(ms)

    def test_bpm_130(self):
        """60ms → knob ~7 (fastest)."""
        k = self._call(60.0)
        assert 6.0 <= k <= 7.0

    def test_bpm_100(self):
        """100ms → knob ~6."""
        k = self._call(100.0)
        assert 5.5 <= k <= 7.0

    def test_bpm_60(self):
        """200ms → knob ~5."""
        k = self._call(200.0)
        assert 4.0 <= k <= 6.0

    def test_very_slow(self):
        """1100ms → knob 1 (slowest)."""
        assert self._call(1100.0) == pytest.approx(1.0, abs=0.5)

    def test_clamp_fastest(self):
        assert self._call(10.0) <= 7.0

    def test_clamp_slowest(self):
        assert self._call(2000.0) >= 1.0


# ══════════════════════════════════════════════════════════════
# _apply_cla76_params tests
# ══════════════════════════════════════════════════════════════

class TestApplyCla76Params:
    """Verify CLA-76-specific translator produces valid values."""

    def test_output_follows_gr(self):
        """Output should be negative, proportional to GR target."""
        from hermes_core.engine import _apply_cla76_params
        intent = CompressionIntent(
            amount="heavy", gr_target_db=8.2,
            crest_factor_db=20.6, rms_db=-18.0, peak_db=-0.1,
        )
        preset = {"attack_ms": 3.0, "release_ms": 5.7}
        result = _apply_cla76_params(intent, preset)
        assert result["Output"] < 0.0, "Output should be negative for heavy GR"
        assert -48.0 <= result["Output"] <= 0.0

    def test_output_more_negative_for_more_gr(self):
        """More GR → more Output attenuation."""
        from hermes_core.engine import _apply_cla76_params
        heavy = _apply_cla76_params(
            CompressionIntent(amount="heavy", gr_target_db=8.0,
                              crest_factor_db=20.0, rms_db=-18.0, peak_db=-2.0),
            {"attack_ms": 3.0, "release_ms": 5.0},
        )
        light = _apply_cla76_params(
            CompressionIntent(amount="light", gr_target_db=2.0,
                              crest_factor_db=8.0, rms_db=-18.0, peak_db=-10.0),
            {"attack_ms": 3.0, "release_ms": 5.0},
        )
        assert heavy["Output"] < light["Output"], (
            f"Heavy Output ({heavy['Output']}) should be less than light ({light['Output']})"
        )

    def test_input_in_valid_range(self):
        """CLA-76 Input must be in -48..0 range."""
        from hermes_core.engine import _apply_cla76_params
        intent = CompressionIntent(
            amount="heavy", gr_target_db=8.2,
            crest_factor_db=20.6, rms_db=-18.0, peak_db=-0.1,
        )
        preset = {"attack_ms": 3.0, "release_ms": 5.7}
        result = _apply_cla76_params(intent, preset)
        assert -48.0 <= result["Input"] <= 0.0

    def test_input_accounts_for_peak(self):
        """Same GR, peak closer to 0 → less Input needed."""
        from hermes_core.engine import _apply_cla76_params
        near_peak = _apply_cla76_params(
            CompressionIntent(amount="medium", gr_target_db=4.0,
                              crest_factor_db=12.0, rms_db=-18.0, peak_db=-2.0),
            {"attack_ms": 3.0, "release_ms": 5.0},
        )
        far_peak = _apply_cla76_params(
            CompressionIntent(amount="medium", gr_target_db=4.0,
                              crest_factor_db=12.0, rms_db=-18.0, peak_db=-12.0),
            {"attack_ms": 3.0, "release_ms": 5.0},
        )
        assert far_peak["Input"] > near_peak["Input"], (
            f"peak=-12 Input ({far_peak['Input']}) should be > peak=-2 Input ({near_peak['Input']})"
        )
