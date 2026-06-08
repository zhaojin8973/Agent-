"""Tests for EQ intent derivation and Pro-Q 3 translation.

These tests run without REAPER.  They verify:

1. EqIntent derivation rules (HPF, resonance cuts, mud, presence, air, genre).
2. Pro-Q 3 physical parameter translation.
3. Edge cases: empty/boundary spectrum reports.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_eq_intent.py -v
"""

import pytest

from hermes_core.spectrum import SpectrumReport, Resonance
from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
from hermes_core.eq_engine import _derive_eq_intent, _apply_proq3_eq
from hermes_core.engine import _GENRE_EQ_TWEAKS


# ══════════════════════════════════════════════════════════════
# Test helpers
# ══════════════════════════════════════════════════════════════

def _make_report(**overrides) -> SpectrumReport:
    """Build a SpectrumReport with sensible defaults, overridden as needed."""
    defaults = {
        "band_energy_db": {
            "sub": -50.0, "low": -30.0, "low_mid": -25.0,
            "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
        },
        "spectral_tilt_db_per_octave": -1.5,
        "resonances": [],
        "mud_ratio_db": -5.0,       # low_mid - mid = -25 - (-20) = -5
        "presence_deficit_db": 4.0,  # mid - presence = -20 - (-24) = 4
        "sibilance_peak_hz": 6500.0,  # default sibilance peak in 4-12 kHz range
        "air_level_db": -35.0,
    }
    defaults.update(overrides)
    return SpectrumReport(**defaults)


# ══════════════════════════════════════════════════════════════
# HPF tests
# ══════════════════════════════════════════════════════════════

class TestHpfDerivation:
    """Verify HPF frequency selection."""

    def test_vocal_default_hpf(self):
        """Vocal HPF defaults to 80 Hz with normal sub energy."""
        report = _make_report()
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        hpf = next(b for b in intent.bands if b.band_type == "hp")
        assert hpf.freq_hz == 80.0, f"Default vocal HPF should be 80 Hz, got {hpf.freq_hz}"

    def test_vocal_hpf_raised_for_excess_sub(self):
        """HPF rises when sub energy is high."""
        report = _make_report(band_energy_db={
            "sub": -10.0, "low": -20.0, "low_mid": -20.0,
            "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
        })
        # sub_excess = -10 - (-20) = 10 dB → well above 3 dB threshold
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        hpf = next(b for b in intent.bands if b.band_type == "hp")
        assert hpf.freq_hz > 80.0, f"HPF should rise above 80 Hz, got {hpf.freq_hz}"
        assert hpf.freq_hz <= 150.0, f"HPF should cap at 150 Hz, got {hpf.freq_hz}"

    def test_backing_default_hpf(self):
        """Backing HPF defaults to 40 Hz."""
        report = _make_report()
        intent = _derive_eq_intent(report, role="backing", genre="pop")
        hpf = next(b for b in intent.bands if b.band_type == "hp")
        assert hpf.freq_hz == 40.0, (
            f"Default backing HPF should be 40 Hz, got {hpf.freq_hz}"
        )


# ══════════════════════════════════════════════════════════════
# Resonance cut tests
# ══════════════════════════════════════════════════════════════

class TestResonanceCuts:
    """Verify resonance → EQ cut mapping."""

    def test_non_harmonic_high_q_gets_cut(self):
        """Room mode (Q>15, non-harmonic) → bell cut."""
        res = Resonance(freq_hz=250.0, prominence_db=8.0, q_factor=22.0, is_harmonic=False)
        report = _make_report(
            resonances=[res],
            band_energy_db={
                "sub": -50.0, "low": -20.0, "low_mid": -20.0,
                "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
            },
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        # Should have a cut band for the 250 Hz resonance
        cuts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db < 0
                and b.freq_hz == 250.0]
        assert len(cuts) == 1, f"Should have 1 cut for 250 Hz room mode, got {cuts}"
        assert cuts[0].gain_db <= -6.0, f"8dB prominence → at least -6dB cut, got {cuts[0].gain_db}"

    def test_harmonic_gets_light_touch(self):
        """Harmonic peaks get at most -2 dB cut."""
        res = Resonance(freq_hz=400.0, prominence_db=10.0, q_factor=25.0, is_harmonic=True)
        report = _make_report(
            resonances=[res],
            band_energy_db={
                "sub": -50.0, "low": -20.0, "low_mid": -20.0,
                "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
            },
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        cuts = [b for b in intent.bands if b.band_type == "bell"
                and abs(b.freq_hz - 400.0) < 5]
        assert len(cuts) >= 1
        for c in cuts:
            assert c.gain_db >= -2.1, (
                f"Harmonic should have light touch (≥ -2 dB), got {c.gain_db}"
            )

    def test_low_q_skipped(self):
        """Q < 15 peak is not treated as a resonance to cut."""
        res = Resonance(freq_hz=300.0, prominence_db=7.0, q_factor=10.0, is_harmonic=False)
        report = _make_report(
            resonances=[res],
            band_energy_db={
                "sub": -50.0, "low": -20.0, "low_mid": -20.0,
                "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
            },
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        # Q=10 → not a room mode, should not get a dedicated cut
        cuts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db < -2
                and abs(b.freq_hz - 300.0) < 5]
        assert len(cuts) == 0, f"Low-Q peak should be skipped, got {cuts}"

    def test_presence_band_skip(self):
        """Resonances in 2-5 kHz (presence band) are NOT cut."""
        res = Resonance(freq_hz=3500.0, prominence_db=9.0, q_factor=20.0, is_harmonic=False)
        report = _make_report(
            resonances=[res],
            band_energy_db={
                "sub": -50.0, "low": -20.0, "low_mid": -20.0,
                "mid": -20.0, "high_mid": -22.0, "presence": -24.0, "air": -35.0,
            },
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        cuts = [b for b in intent.bands if b.gain_db < -2
                and abs(b.freq_hz - 3500.0) < 10]
        assert len(cuts) == 0, f"Presence band resonance should not be cut, got {cuts}"


# ══════════════════════════════════════════════════════════════
# Mud / Presence / Air tests
# ══════════════════════════════════════════════════════════════

class TestTonalBalance:
    """Verify mud cut, presence boost, and air shelf rules."""

    def test_mud_cut_when_muddy(self):
        """High mud_ratio → 350 Hz bell cut."""
        report = _make_report(mud_ratio_db=5.0)  # > 3.0
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        cuts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db < 0
                and abs(b.freq_hz - 350.0) < 5]
        assert len(cuts) == 1, f"Should have mud cut at 350 Hz, got {cuts}"
        assert -4.1 < cuts[0].gain_db <= -1.0, (
            f"Mud cut should be between -4 and -1 dB, got {cuts[0].gain_db}"
        )

    def test_no_mud_cut_when_clean(self):
        """Low mud_ratio → no 350 Hz cut."""
        report = _make_report(mud_ratio_db=1.0)  # < 3.0
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        cuts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db < 0
                and abs(b.freq_hz - 350.0) < 5]
        assert len(cuts) == 0, f"Clean vocal should not get mud cut, got {cuts}"

    def test_presence_boost_when_dark(self):
        """High presence_deficit → 3 kHz boost."""
        report = _make_report(presence_deficit_db=5.0)  # > 2.0
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        boosts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db > 0
                  and abs(b.freq_hz - 3000.0) < 5]
        assert len(boosts) == 1, f"Dark vocal should get presence boost, got {boosts}"
        assert 0 < boosts[0].gain_db <= 3.5, (
            f"Presence boost should be 0..3.5 dB, got {boosts[0].gain_db}"
        )

    def test_no_presence_boost_when_bright(self):
        """Low presence_deficit → no boost."""
        report = _make_report(presence_deficit_db=1.0)  # < 2.0
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        boosts = [b for b in intent.bands if b.band_type == "bell" and b.gain_db > 0
                  and abs(b.freq_hz - 3000.0) < 5]
        assert len(boosts) == 0, f"Bright vocal should not get presence boost, got {boosts}"

    def test_air_shelf_when_dull(self):
        """Very low air + steep negative tilt → high shelf at 8 kHz."""
        report = _make_report(
            air_level_db=-35.0,
            spectral_tilt_db_per_octave=-4.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        shelves = [b for b in intent.bands if b.band_type == "high_shelf"]
        assert len(shelves) == 1, f"Dull vocal should get air shelf, got {shelves}"
        assert shelves[0].gain_db > 0, f"Air shelf should be positive gain"

    def test_no_air_shelf_when_not_dull(self):
        """Normal air level → no shelf."""
        report = _make_report(
            air_level_db=-25.0,
            spectral_tilt_db_per_octave=-1.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        shelves = [b for b in intent.bands if b.band_type == "high_shelf"]
        assert len(shelves) == 0, f"Normal vocal should not get air shelf"


# ══════════════════════════════════════════════════════════════
# Genre adjustment tests
# ══════════════════════════════════════════════════════════════

class TestGenreAdjustments:
    """Verify genre-specific tweaks to EQ derivation."""

    def test_pop_gets_extra_presence(self):
        """Pop genre → extra +0.5 dB presence boost."""
        report = _make_report(presence_deficit_db=4.0)
        pop = _derive_eq_intent(report, role="vocal", genre="pop")
        folk = _derive_eq_intent(report, role="vocal", genre="folk")

        pop_boost = next(
            (b.gain_db for b in pop.bands
             if b.band_type == "bell" and abs(b.freq_hz - 3000) < 5),
            0,
        )
        folk_boost = next(
            (b.gain_db for b in folk.bands
             if b.band_type == "bell" and abs(b.freq_hz - 3000) < 5),
            0,
        )
        # Pop should have more presence boost than folk (which scales to 0.75×)
        assert pop_boost > folk_boost, (
            f"Pop presence boost ({pop_boost}) should exceed folk ({folk_boost})"
        )

    def test_rock_tolerates_more_mud(self):
        """Rock → higher mud threshold (4 dB instead of 3 dB)."""
        report = _make_report(mud_ratio_db=3.5)
        # 3.5 > 3.0 → pop would cut; 3.5 ≤ 4.0 → rock should NOT cut
        pop = _derive_eq_intent(report, role="vocal", genre="pop")
        rock = _derive_eq_intent(report, role="vocal", genre="rock")

        pop_cuts = [b for b in pop.bands if b.band_type == "bell" and b.gain_db < 0
                    and abs(b.freq_hz - 350.0) < 5]
        rock_cuts = [b for b in rock.bands if b.band_type == "bell" and b.gain_db < 0
                     and abs(b.freq_hz - 350.0) < 5]

        assert len(pop_cuts) == 1, "Pop should cut mud at 3.5 dB ratio"
        assert len(rock_cuts) == 0, "Rock should tolerate 3.5 dB mud ratio"

    def test_unknown_genre_uses_defaults(self):
        """Unknown genre falls back to default tweaks."""
        report = _make_report(presence_deficit_db=4.0)
        intent = _derive_eq_intent(report, role="vocal", genre="jazz")
        assert intent.spectral_tilt in ("dark", "neutral", "bright")
        # Should not crash — default tweaks apply


# ══════════════════════════════════════════════════════════════
# Pro-Q 3 translation tests
# ══════════════════════════════════════════════════════════════

class TestProQ3Translation:
    """Verify EqIntent → Pro-Q 3 physical parameter mapping."""

    def test_empty_intent_disables_all_bands(self):
        """An EqIntent with no bands → all 8 bands disabled."""
        intent = EqIntent(bands=[], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        for n in range(1, 9):
            assert params.get(f"Band {n} Enabled") == 0.0, (
                f"Band {n} should be disabled, got {params.get(f'Band {n} Enabled')}"
            )

    def test_hpf_maps_to_low_cut(self):
        """HPF band_type → Pro-Q 3 Low Cut (Shape=2/8=0.25)."""
        from hermes_core.eq_engine import _proq3_freq_norm, _proq3_q_norm
        band = EqBandIntent(
            band_type="hp", freq_hz=80.0, gain_db=0.0, q=0.7,
            reason="Test HPF",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        assert params["Band 1 Shape"] == pytest.approx(0.25)   # Low Cut = 2/8
        assert params["Band 1 Frequency"] == pytest.approx(_proq3_freq_norm(80.0))
        assert params["Band 1 Gain"] == pytest.approx(0.5)       # 0 dB → norm 0.5
        assert params["Band 1 Q"] == pytest.approx(_proq3_q_norm(0.7))
        assert params["Band 1 Enabled"] == 1.0
        assert params["Band 1 Used"] == 1.0

    def test_bell_maps_to_bell(self):
        """Bell band_type → Pro-Q 3 Bell (Shape=0)."""
        from hermes_core.eq_engine import _proq3_freq_norm, _proq3_q_norm
        band = EqBandIntent(
            band_type="bell", freq_hz=3000.0, gain_db=2.5, q=1.0,
            reason="Test bell",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        assert params["Band 1 Shape"] == 0.0    # Bell = 0/8
        assert params["Band 1 Frequency"] == pytest.approx(_proq3_freq_norm(3000.0))
        assert params["Band 1 Gain"] == pytest.approx((2.5 + 30.0) / 60.0)
        assert params["Band 1 Q"] == pytest.approx(_proq3_q_norm(1.0))
        assert params["Band 1 Enabled"] == 1.0
        assert params["Band 1 Speakers"] == 0.0  # Stereo (default)

    def test_high_shelf_maps_correctly(self):
        """High shelf band_type → Pro-Q 3 High Shelf (Shape=3/8=0.375)."""
        band = EqBandIntent(
            band_type="high_shelf", freq_hz=8000.0, gain_db=1.5, q=0.7,
            reason="Test air",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        assert params["Band 1 Shape"] == pytest.approx(0.375)  # High Shelf = 3/8

    def test_band_count_capped_at_8(self):
        """More than 8 bands → only first 8 are mapped."""
        bands = [
            EqBandIntent(band_type="bell", freq_hz=float(100 * i),
                         gain_db=-2.0, q=1.0, reason=f"Test {i}")
            for i in range(1, 13)  # 12 bands
        ]
        intent = EqIntent(bands=bands, spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        # Bands 1-8 should be enabled, bands after 8 should be absent or disabled
        assert params["Band 8 Enabled"] == 1.0
        assert "Band 9 Frequency" not in params or params.get("Band 9 Enabled") == 0.0

    def test_remaining_bands_disabled(self):
        """After mapping bands, unused slots should be disabled."""
        bands = [
            EqBandIntent(band_type="hp", freq_hz=80.0, gain_db=0.0, q=0.7, reason="hpf"),
            EqBandIntent(band_type="bell", freq_hz=3000.0, gain_db=2.0, q=1.0, reason="presence"),
        ]  # Only 2 bands
        intent = EqIntent(bands=bands, spectral_tilt="neutral", mud_detected=False)
        params = _apply_proq3_eq(intent)
        for n in range(3, 9):
            assert params.get(f"Band {n} Enabled") == 0.0, (
                f"Unused Band {n} should be disabled"
            )


# ══════════════════════════════════════════════════════════════
# Edge case tests
# ══════════════════════════════════════════════════════════════

class TestEdgeCases:
    """Verify boundary/edge cases don't crash."""

    def test_empty_resonances_ok(self):
        """Empty resonance list → no cuts, no crash."""
        report = _make_report(resonances=[])
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        assert isinstance(intent, EqIntent)
        assert intent.spectral_tilt in ("dark", "neutral", "bright")

    def test_max_bands_not_exceeded(self):
        """Derived EQ should never exceed 8 bands (Pro-Q 3 limit)."""
        # Create a report with many resonances + mud + dark
        resonances = [
            Resonance(freq_hz=100.0 + i * 50, prominence_db=8.0,
                      q_factor=20.0, is_harmonic=False)
            for i in range(10)
        ]
        report = _make_report(
            resonances=resonances,
            mud_ratio_db=5.0,
            presence_deficit_db=5.0,
            air_level_db=-40.0,
            spectral_tilt_db_per_octave=-5.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        assert len(intent.bands) <= 8, (
            f"Should not exceed 8 bands, got {len(intent.bands)}"
        )

    def test_intent_metadata(self):
        """EqIntent carries correct metadata."""
        report = _make_report(
            spectral_tilt_db_per_octave=-3.0,
            mud_ratio_db=4.0,
        )
        intent = _derive_eq_intent(report, role="vocal", genre="pop")
        assert intent.spectral_tilt == "dark"
        assert intent.mud_detected is True


# ════════════════════════════════════════════════════════════════
# Position-based rule splitting
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPositionSplitting:
    """Pre = conservative on boosts, Post = full rules, Solo = all."""

    def test_pre_boosts_are_conservative(self):
        """Pre-comp: same signal produces less boost than post/solo."""
        report = _make_report(
            presence_deficit_db=10.0,
            air_level_db=-40.0,
            spectral_tilt_db_per_octave=-6.0,
        )
        pre = _derive_eq_intent(report, position="pre")
        solo = _derive_eq_intent(report, position="solo")

        pre_boost = sum(max(0, b.gain_db) for b in pre.bands)
        solo_boost = sum(max(0, b.gain_db) for b in solo.bands)
        assert pre_boost <= solo_boost, (
            f"Pre boost ({pre_boost}) should be <= solo boost ({solo_boost})"
        )

    def test_post_runs_full_rules(self):
        """Post-comp runs all rules — can cut and boost."""
        report = _make_report(
            presence_deficit_db=10.0,
            air_level_db=-40.0,
            spectral_tilt_db_per_octave=-6.0,
            mud_ratio_db=5.0,
        )
        intent = _derive_eq_intent(report, position="post")
        # Post should have HPF (like solo)
        hpf_bands = [b for b in intent.bands if b.band_type == "hp"]
        assert len(hpf_bands) == 1, "Post should include HPF"
        # Post should have both cuts and boosts when signal demands both
        has_cut = any(b.gain_db < 0 for b in intent.bands if b.band_type != "hp")
        has_boost = any(b.gain_db > 0 for b in intent.bands)
        assert has_cut or has_boost, "Post should respond to signal problems"

    def test_solo_matches_post_for_full_signal(self):
        """Solo and post produce identical results for the same report."""
        report = _make_report(
            presence_deficit_db=10.0,
            air_level_db=-40.0,
            spectral_tilt_db_per_octave=-6.0,
            mud_ratio_db=5.0,
        )
        solo = _derive_eq_intent(report, position="solo")
        post = _derive_eq_intent(report, position="post")
        assert len(solo.bands) == len(post.bands)
        for sb, pb in zip(solo.bands, post.bands):
            assert sb.band_type == pb.band_type
            assert sb.gain_db == pb.gain_db

    def test_invalid_position_raises(self):
        """Unknown position raises ValueError."""
        import pytest as pt
        with pt.raises(ValueError, match="position must be one of"):
            _derive_eq_intent(_make_report(), position="middle")


# ════════════════════════════════════════════════════════════════
# SSL EQ translation
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSSLEQTranslation:
    """_apply_ssleq_eq maps EqIntent → SSL EQ 0–1 params."""

    def test_presence_maps_to_hmf(self):
        from hermes_core.eq_engine import _apply_ssleq_eq

        band = EqBandIntent(
            band_type="bell", freq_hz=3000.0, gain_db=3.0, q=1.0,
            reason="presence",
        )
        intent = EqIntent(bands=[band], spectral_tilt="dark", mud_detected=False)
        params = _apply_ssleq_eq(intent)

        # HMF should be used for bell/presence
        assert params["HMF Gain"] > 0.5, f"HMF Gain should be boosted, got {params['HMF Gain']}"
        assert 0.0 < params["HMF Frq"] < 1.0

    def test_air_maps_to_hf(self):
        from hermes_core.eq_engine import _apply_ssleq_eq

        band = EqBandIntent(
            band_type="high_shelf", freq_hz=8000.0, gain_db=1.5, q=0.7,
            reason="air",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_ssleq_eq(intent)

        assert params["HF Gain"] > 0.5
        assert 0.0 < params["HF Frq"] < 1.0

    def test_analog_always_on(self):
        from hermes_core.eq_engine import _apply_ssleq_eq

        intent = EqIntent(bands=[], spectral_tilt="neutral", mud_detected=False)
        params = _apply_ssleq_eq(intent)
        assert params["Analog"] == 1.0
        assert params["EQ IN"] == 1.0
        assert params["Bypass"] == 0.0

    def test_output_attenuation_for_boost(self):
        """Output Gain should be reduced when total boost > 0."""
        from hermes_core.eq_engine import _apply_ssleq_eq

        band = EqBandIntent(
            band_type="bell", freq_hz=3000.0, gain_db=4.0, q=1.0,
            reason="big boost",
        )
        intent = EqIntent(bands=[band], spectral_tilt="neutral", mud_detected=False)
        params = _apply_ssleq_eq(intent)

        # Total boost +4dB → output should be < 0.5 (attenuated)
        assert params["Gain"] < 0.5, f"Output should be attenuated, got {params['Gain']}"

    def test_all_params_in_range(self):
        from hermes_core.eq_engine import _apply_ssleq_eq

        bands = [
            EqBandIntent("bell", 3000.0, 3.0, 1.0, "presence"),
            EqBandIntent("high_shelf", 8000.0, 1.5, 0.7, "air"),
        ]
        intent = EqIntent(bands=bands, spectral_tilt="dark", mud_detected=False)
        params = _apply_ssleq_eq(intent)

        for pname, pval in params.items():
            assert 0.0 <= pval <= 1.0, f"{pname}={pval:.4f} not in [0,1]"
