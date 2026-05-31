"""Tests for hermes_core.normalize — plugin parameter normalisation layer."""

import pytest
from hermes_core.normalize import (
    PLUGIN_REGISTRY,
    normalize_param,
    normalize_params,
    _normalize_linear,
    _normalize_from_table,
)
from hermes_core.exceptions import (
    UnregisteredPluginError,
    UnregisteredParamError,
)


# ════════════════════════════════════════════════════════════
# _normalize_linear
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLinearNormalize:
    def test_midpoint(self):
        assert _normalize_linear(0.0, -60.0, 0.0) == 1.0
        assert _normalize_linear(-30.0, -60.0, 0.0) == 0.5
        assert _normalize_linear(-60.0, -60.0, 0.0) == 0.0

    def test_clamped_low(self):
        assert _normalize_linear(-100.0, -60.0, 0.0) == 0.0

    def test_clamped_high(self):
        assert _normalize_linear(10.0, -60.0, 0.0) == 1.0

    def test_zero_range(self):
        assert _normalize_linear(5.0, 10.0, 10.0) == 0.5

    def test_positive_range(self):
        assert _normalize_linear(5.0, 0.0, 10.0) == 0.5


# ════════════════════════════════════════════════════════════
# _normalize_from_table
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTableNormalize:
    SIMPLE_TABLE = [
        (0.0, 0.0),
        (0.5, 50.0),
        (1.0, 100.0),
    ]

    def test_exact_match(self):
        assert _normalize_from_table(0.0, self.SIMPLE_TABLE) == 0.0
        assert _normalize_from_table(50.0, self.SIMPLE_TABLE) == 0.5
        assert _normalize_from_table(100.0, self.SIMPLE_TABLE) == 1.0

    def test_interpolated(self):
        assert _normalize_from_table(25.0, self.SIMPLE_TABLE) == 0.25
        assert _normalize_from_table(75.0, self.SIMPLE_TABLE) == 0.75

    def test_below_table(self):
        assert _normalize_from_table(-10.0, self.SIMPLE_TABLE) == 0.0

    def test_above_table(self):
        assert _normalize_from_table(200.0, self.SIMPLE_TABLE) == 1.0

    def test_single_knot(self):
        assert _normalize_from_table(5.0, [(0.3, 5.0)]) == 0.3

    def test_empty_table_raises(self):
        with pytest.raises(ValueError):
            _normalize_from_table(0.0, [])

    def test_bisect_boundary(self):
        """Value exactly at a knot boundary returns the exact knot."""
        table = [
            (0.0, -60.0),
            (0.3, -30.0),
            (0.7, -10.0),
            (1.0, 0.0),
        ]
        assert _normalize_from_table(-30.0, table) == 0.3
        assert _normalize_from_table(-10.0, table) == 0.7


# ════════════════════════════════════════════════════════════
# normalize_param — linear params
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeParamLinear:
    """Pro-C 2 Threshold / Ratio / Gain are linear."""

    PLUGIN = "FabFilter Pro-C 2 (FabFilter)"

    def test_threshold_linear(self):
        # range: -60..0 → -18 = (-18 - (-60)) / 60 = 42/60 = 0.7
        result = normalize_param(self.PLUGIN, "Threshold", -18.0)
        assert result == pytest.approx(0.7)

    def test_threshold_zero(self):
        assert normalize_param(self.PLUGIN, "Threshold", 0.0) == 1.0

    def test_threshold_min(self):
        assert normalize_param(self.PLUGIN, "Threshold", -60.0) == 0.0

    def test_ratio(self):
        # range: 1..20 → ratio=4.0 → (4-1)/(20-1) = 3/19 ≈ 0.1579
        result = normalize_param(self.PLUGIN, "Ratio", 4.0)
        assert result == pytest.approx(3.0 / 19.0)

    def test_gain_zero(self):
        assert normalize_param(self.PLUGIN, "Makeup Gain", 0.0) == 0.5


# ════════════════════════════════════════════════════════════
# normalize_param — table lookup
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeParamTable:
    """1176 Input uses a calibration table."""

    PLUGIN = "Universal Audio 1176LN (Universal Audio)"

    def test_input_threshold_low(self):
        """Low threshold = high gain reduction target → high knob position."""
        # threshold -26 dBFS → between knots (-26,0.70) and (-38,0.90)
        result = normalize_param(self.PLUGIN, "Input", -26.0)
        assert 0.65 < result < 0.80

    def test_input_threshold_high(self):
        """High threshold = almost no compression → low knob position."""
        result = normalize_param(self.PLUGIN, "Input", -2.0)
        assert 0.10 < result < 0.20

    def test_input_clamped_low(self):
        result = normalize_param(self.PLUGIN, "Input", -60.0)
        assert result == 1.0

    def test_input_clamped_high(self):
        result = normalize_param(self.PLUGIN, "Input", 20.0)
        assert result == 0.0

    def test_output_linear(self):
        # range: -24..24 → 0 → 24/48 = 0.5
        assert normalize_param(self.PLUGIN, "Output", 0.0) == 0.5


# ════════════════════════════════════════════════════════════
# normalize_param — RVox
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeParamRVox:
    PLUGIN = "VST3: RVox Mono (Waves)"

    def test_compression_mid(self):
        # range -36..0 → -18 → 0.5
        assert normalize_param(self.PLUGIN, "Compression", -18.0) == 0.5

    def test_compression_none(self):
        # 0 dB → no compression → norm 1.0
        assert normalize_param(self.PLUGIN, "Compression", 0.0) == 1.0

    def test_compression_max(self):
        # -36 dB → max compression → norm 0.0
        assert normalize_param(self.PLUGIN, "Compression", -36.0) == 0.0

    def test_gain(self):
        # range -36..0 → -18 → 0.5
        result = normalize_param(self.PLUGIN, "Gain", -18.0)
        assert result == 0.5

    def test_gain_unity(self):
        assert normalize_param(self.PLUGIN, "Gain", 0.0) == 1.0

    def test_gate_off(self):
        # Gate at -120 dB (-Inf) → norm 0.0
        assert normalize_param(self.PLUGIN, "Gate", -120.0) == 0.0

    def test_gate_mid(self):
        # range -120..0 → -60 → 0.5
        assert normalize_param(self.PLUGIN, "Gate", -60.0) == 0.5


# ════════════════════════════════════════════════════════════
# normalize_params — batch
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNormalizeParams:
    def test_batch(self):
        physical = {"Threshold": -18.0, "Ratio": 4.0, "Makeup Gain": 3.0}
        result = normalize_params("FabFilter Pro-C 2 (FabFilter)", physical)
        assert "Threshold" in result
        assert "Ratio" in result
        assert "Makeup Gain" in result
        assert all(0.0 <= v <= 1.0 for v in result.values())

    def test_batch_single(self):
        result = normalize_params("VST3: RVox Mono (Waves)", {"Compression": -18.0})
        assert result["Compression"] == 0.5


# ════════════════════════════════════════════════════════════
# Fail-fast exceptions
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFailFast:
    def test_unknown_plugin_raises(self):
        with pytest.raises(UnregisteredPluginError):
            normalize_param("NonexistentPlugin", "Threshold", -18.0)

    def test_unknown_param_raises(self):
        with pytest.raises(UnregisteredParamError):
            normalize_param(
                "FabFilter Pro-C 2 (FabFilter)", "NonexistentParam", 0.5
            )

    def test_unknown_plugin_in_batch_raises(self):
        with pytest.raises(UnregisteredPluginError):
            normalize_params("BadPlugin", {"Gain": 0.0})


# ════════════════════════════════════════════════════════════
# Registry integrity
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRegistryIntegrity:
    def test_all_plugins_have_type(self):
        for name, entry in PLUGIN_REGISTRY.items():
            assert "type" in entry, f"'{name}' missing 'type'"

    def test_params_are_valid_specs(self):
        for name, entry in PLUGIN_REGISTRY.items():
            for pname, spec in entry.get("params", {}).items():
                has_range = "range" in spec
                has_table = "table" in spec
                assert has_range or has_table, (
                    f"'{name}.{pname}' has no 'range' or 'table'"
                )
                if has_table:
                    assert len(spec["table"]) >= 1, (
                        f"'{name}.{pname}' table is empty"
                    )

    def test_table_rows_are_sorted(self):
        for name, entry in PLUGIN_REGISTRY.items():
            for pname, spec in entry.get("params", {}).items():
                if "table" not in spec:
                    continue
                phys = [row[1] for row in spec["table"]]
                assert phys == sorted(phys), (
                    f"'{name}.{pname}' table not sorted by physical value"
                )


# ════════════════════════════════════════════════════════════
# EQ parameter normalisation (Pro-Q 3 + ReaEQ)
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestProQ3Normalize:
    """Pro-Q 3 params are pre-normalised by _apply_proq3_eq().  The registry
    acts as a 0–1 pass-through for all Pro-Q 3 parameters."""

    PLUGIN = "VST: FabFilter Pro-Q 3 (FabFilter)"

    def test_pass_through_params(self):
        """All Pro-Q 3 params are 0–1 pass-through."""
        for n in range(1, 9):
            for suffix in ("Used", "Enabled", "Frequency", "Gain", "Q", "Shape"):
                param = f"Band {n} {suffix}"
                assert normalize_param(self.PLUGIN, param, 0.0) == 0.0
                assert normalize_param(self.PLUGIN, param, 0.5) == 0.5
                assert normalize_param(self.PLUGIN, param, 1.0) == 1.0

    def test_output_level_registered(self):
        """Output Level is a global Pro-Q 3 param."""
        assert normalize_param(self.PLUGIN, "Output Level", 0.5) == 0.5

    def test_band_9_not_registered(self):
        """Band 9 should NOT be registered (only 8 bands)."""
        with pytest.raises(UnregisteredParamError):
            normalize_param(self.PLUGIN, "Band 9 Frequency", 0.5)


@pytest.mark.unit
class TestReaEQNormalize:
    """ReaEQ fallback EQ parameter normalisation."""

    PLUGIN = "ReaEQ (Cockos)"

    def test_freq_linear(self):
        """ReaEQ Freq: 20-20000 Hz linear."""
        result = normalize_param(self.PLUGIN, "Band 1 Freq", 10010.0)
        assert result == pytest.approx(0.5, abs=0.01)

    def test_gain_extremes(self):
        """ReaEQ: -24 to +24 dB."""
        assert normalize_param(self.PLUGIN, "Band 1 Gain", -24.0) == 0.0
        assert normalize_param(self.PLUGIN, "Band 1 Gain", 24.0) == 1.0

    def test_q_extremes(self):
        """ReaEQ: 0.01 to 10.0."""
        assert normalize_param(self.PLUGIN, "Band 1 Q", 0.01) == 0.0
        assert normalize_param(self.PLUGIN, "Band 1 Q", 10.0) == 1.0

    def test_all_4_bands_registered(self):
        """Bands 1-4 should all be registered."""
        for n in range(1, 5):
            for suffix in ("Freq", "Gain", "Q", "Type", "Enabled"):
                param = f"Band {n} {suffix}"
                result = normalize_param(self.PLUGIN, param, 0.0)
                assert 0.0 <= result <= 1.0

    def test_band_5_not_registered(self):
        """ReaEQ only registers 4 bands."""
        with pytest.raises(UnregisteredParamError):
            normalize_param(self.PLUGIN, "Band 5 Freq", 1000.0)


@pytest.mark.unit
class TestEQBatchNormalize:
    """Batch EQ — all values from _apply_proq3_eq already 0–1."""

    PLUGIN = "VST: FabFilter Pro-Q 3 (FabFilter)"

    def test_proq3_full_band_all_01(self):
        """_apply_proq3_eq outputs are all within [0, 1]."""
        from hermes_core.engine import _apply_proq3_eq
        from hermes_core.loudness_optimizer import EqIntent, EqBandIntent

        band = EqBandIntent(
            band_type="bell", freq_hz=3000.0, gain_db=2.5, q=1.0,
            reason="test",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        for pname, pval in params.items():
            assert 0.0 <= pval <= 1.0, f"{pname}={pval:.4f} not in [0,1]"
        # Output Level should be included and compensate for boost
        assert "Output Level" in params
        # +2.5 dB boost → -2.5 dB output trim for headroom protection
        assert params["Output Level"] == pytest.approx((-2.5 + 36) / 72)

    def test_proq3_empty_intent_disables_bands(self):
        """Empty intent → bands disabled, Output Level still set."""
        from hermes_core.engine import _apply_proq3_eq
        from hermes_core.loudness_optimizer import EqIntent

        intent = EqIntent(bands=[], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        for n in range(1, 9):
            assert params.get(f"Band {n} Enabled") == 0.0
        assert params["Output Level"] == 0.5
