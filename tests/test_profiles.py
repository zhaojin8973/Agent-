"""Tests for hermes_core.profiles — YAML config bridge."""

import pytest
from pathlib import Path

from hermes_core.profiles import FXPreset, MixingProfile, _resolve_fx_type, _get_compressor_preset, _EQ_BASELINE


# ════════════════════════════════════════════════════════════
# FXPreset
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFXPreset:
    def test_defaults(self):
        p = FXPreset(name="ReaEQ")
        assert p.name == "ReaEQ"
        assert p.params == {}
        assert p.alternatives == []

    def test_with_params(self):
        p = FXPreset(name="Pro-Q 3", params={"Gain": 0.5})
        assert p.params["Gain"] == 0.5

    def test_with_alternatives(self):
        p = FXPreset(name="Pro-L 2", alternatives=["Pro-L 2 VST3", "Pro-L 2 AU"])
        assert len(p.alternatives) == 2


# ════════════════════════════════════════════════════════════
# MixingProfile._parse_fx
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestParseFx:
    def test_none_returns_none(self):
        assert MixingProfile._parse_fx(None) is None

    def test_string_returns_fxpreset(self):
        result = MixingProfile._parse_fx("ReaEQ")
        assert isinstance(result, FXPreset)
        assert result.name == "ReaEQ"
        assert result.params == {}

    def test_dict_with_name(self):
        result = MixingProfile._parse_fx({"name": "ReaComp", "params": {"Ratio": 4.0}})
        assert result.name == "ReaComp"
        assert result.params["Ratio"] == 4.0

    def test_dict_with_alternatives(self):
        result = MixingProfile._parse_fx({"name": "EQ", "alternatives": ["EQ VST3"]})
        assert result.alternatives == ["EQ VST3"]

    def test_empty_dict_returns_empty_name(self):
        result = MixingProfile._parse_fx({})
        assert result.name == ""


# ════════════════════════════════════════════════════════════
# MixingProfile._from_dict
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFromDict:
    def test_minimal_dict(self):
        profile = MixingProfile._from_dict({"name": "Minimal"})
        assert profile.name == "Minimal"
        assert profile.target_lufs == -12.0
        assert profile.ceiling_db == -0.5
        assert profile.vocal_chain == []
        assert profile.backing_chain == []
        assert profile.master_limiter.name == "VST: FabFilter Pro-L 2 (FabFilter)"

    def test_full_dict(self):
        d = {
            "name": "Full",
            "target_lufs": -14.0,
            "ceiling_db": -1.0,
            "tolerance_lufs": 0.5,
            "clip_gain_ref_db": -20.0,
            "vocal_chain": [
                {"name": "Pro-Q 3", "params": {"Gain": 0.5}},
                "RVox",
            ],
            "backing_chain": [
                {"name": "API 2500"},
            ],
            "bus_reverb": {"name": "ValhallaVintageVerb", "params": {"Mix": 0.3}},
            "reverb_level_db": -10.0,
            "master_limiter": {"name": "Ozone Maximizer", "params": {"Threshold": -0.5}},
            "genre_table": {"rock": [6, 10]},
        }
        profile = MixingProfile._from_dict(d)
        assert profile.name == "Full"
        assert profile.target_lufs == -14.0
        assert profile.ceiling_db == -1.0
        assert profile.tolerance_lufs == 0.5
        assert profile.clip_gain_ref_db == -20.0
        assert len(profile.vocal_chain) == 2
        assert profile.vocal_chain[0].name == "Pro-Q 3"
        assert profile.vocal_chain[1].name == "RVox"
        assert len(profile.backing_chain) == 1
        assert profile.bus_reverb.name == "ValhallaVintageVerb"
        assert profile.bus_reverb.params["Mix"] == 0.3
        assert profile.reverb_level_db == -10.0
        assert profile.master_limiter.name == "Ozone Maximizer"
        assert "rock" in profile.genre_table

    def test_missing_limiter_falls_back(self):
        """When master_limiter is absent, default Pro-L 2 is used."""
        profile = MixingProfile._from_dict({"name": "NoLimiter"})
        assert profile.master_limiter.name == "VST: FabFilter Pro-L 2 (FabFilter)"

    def test_genre_table_defaults_when_missing(self):
        profile = MixingProfile._from_dict({"name": "Test"})
        assert "pop" in profile.genre_table
        assert "folk" in profile.genre_table
        assert profile.genre_table["pop"] == [6, 9]

    def test_backing_chain_empty_by_default(self):
        profile = MixingProfile._from_dict({"name": "Test"})
        assert profile.backing_chain == []


# ════════════════════════════════════════════════════════════
# MixingProfile.from_yaml
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFromYaml:
    def test_loads_valid_yaml(self, tmp_path):
        yaml_path = tmp_path / "profile.yaml"
        yaml_path.write_text("""
name: "Test Profile"
target_lufs: -14.0
vocal_chain:
  - name: "ReaEQ"
  - name: "ReaComp"
bus_reverb:
  name: "ReaVerbate"
genre_table:
  test_genre: [5, 8]
""")
        profile = MixingProfile.from_yaml(str(yaml_path))
        assert profile.name == "Test Profile"
        assert profile.target_lufs == -14.0
        assert len(profile.vocal_chain) == 2
        assert profile.bus_reverb.name == "ReaVerbate"

    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError):
            MixingProfile.from_yaml("/nonexistent/profile.yaml")


# ════════════════════════════════════════════════════════════
# MixingProfile.all_fx_names
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestAllFxNames:
    def test_dedupes_duplicate_names(self):
        """FX appearing in multiple chains is listed only once."""
        profile = MixingProfile()
        profile.vocal_chain = [FXPreset(name="Pro-Q 3")]
        profile.backing_chain = [FXPreset(name="Pro-Q 3")]  # same
        profile.master_limiter = FXPreset(name="Pro-L 2")
        names = profile.all_fx_names()
        assert names == ["Pro-Q 3", "Pro-L 2"]  # deduped

    def test_no_reverb_returns_names_without_none(self):
        profile = MixingProfile()
        profile.vocal_chain = [FXPreset(name="EQ")]
        profile.bus_reverb = None
        profile.master_limiter = FXPreset(name="Limiter")
        names = profile.all_fx_names()
        assert "EQ" in names
        assert "Limiter" in names
        assert len(names) == 2

    def test_preserves_order(self):
        profile = MixingProfile()
        profile.vocal_chain = [FXPreset(name="C")]
        profile.backing_chain = [FXPreset(name="A")]
        profile.bus_reverb = FXPreset(name="B")
        profile.master_limiter = FXPreset(name="D")
        names = profile.all_fx_names()
        assert names == ["C", "A", "B", "D"]


# ════════════════════════════════════════════════════════════
# Edge cases
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestEdgeCases:
    def test_bus_reverb_as_string(self):
        """bus_reverb can be a plain string, not just a dict."""
        profile = MixingProfile._from_dict({
            "name": "Test",
            "bus_reverb": "ReaVerbate",
        })
        assert profile.bus_reverb.name == "ReaVerbate"
        assert profile.bus_reverb.params == {}

    def test_master_limiter_as_string(self):
        """master_limiter can be a plain string."""
        profile = MixingProfile._from_dict({
            "name": "Test",
            "master_limiter": "Pro-L 2",
        })
        assert profile.master_limiter.name == "Pro-L 2"

    def test_description_is_preserved(self):
        profile = MixingProfile._from_dict({
            "name": "Test",
            "description": "A test profile for rock mixing",
        })
        assert "rock mixing" in profile.description


# ════════════════════════════════════════════════════════════
# FXPreset.fx_type
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFxType:
    def test_fx_type_from_yaml(self):
        """fx_type can be set via 'type' field in YAML."""
        fx = MixingProfile._parse_fx({"name": "1176", "type": "fet"})
        assert fx.fx_type == "fet"

    def test_fx_type_default_empty(self):
        fx = FXPreset(name="SomeFX")
        assert fx.fx_type == ""


# ════════════════════════════════════════════════════════════
# _resolve_fx_type
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestResolveFxType:
    def test_declared_type_wins(self):
        assert _resolve_fx_type("My Plugin", "vca") == "vca"

    def test_1176_alias(self):
        assert _resolve_fx_type("Universal Audio 1176LN", "") == "fet"

    def test_la2a_alias(self):
        assert _resolve_fx_type("Teletronix LA-2A", "") == "opto"

    def test_rvox_alias(self):
        assert _resolve_fx_type("Waves RVox (Waves)", "") == "rvox"

    def test_proc_alias(self):
        assert _resolve_fx_type("FabFilter Pro-C 2", "") == "vca"

    def test_eq_alias(self):
        assert _resolve_fx_type("FabFilter Pro-Q 3", "") == "eq"

    def test_reverb_alias(self):
        assert _resolve_fx_type("ValhallaVintageVerb", "") == "reverb"

    def test_unknown_returns_empty(self):
        assert _resolve_fx_type("SomeUnknownPlugin", "") == ""


# ════════════════════════════════════════════════════════════
# _get_compressor_preset
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCompressorPresets:
    def test_vocal_pop(self):
        p = _get_compressor_preset("vocal", "pop")
        assert p["attack_ms"] == 5.0
        assert p["release_ms"] == 80.0

    def test_vocal_folk(self):
        p = _get_compressor_preset("vocal", "folk")
        assert p["attack_ms"] == 10.0

    def test_backing_rock(self):
        p = _get_compressor_preset("backing", "rock")
        assert p["attack_ms"] == 5.0
        assert p["release_ms"] == 100.0

    def test_unknown_genre_falls_back(self):
        p = _get_compressor_preset("vocal", "jazz")
        assert p["attack_ms"] == 5.0  # default

    def test_unknown_role_falls_back(self):
        p = _get_compressor_preset("drums", "pop")
        assert "attack_ms" in p


# ════════════════════════════════════════════════════════════
# _EQ_BASELINE
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestEQBaseline:
    def test_vocal_has_bands(self):
        bands = _EQ_BASELINE["vocal"]
        assert len(bands) >= 1
        assert bands[0]["type"] == "hp"

    def test_backing_has_bands(self):
        bands = _EQ_BASELINE["backing"]
        assert len(bands) >= 1
        assert bands[0]["freq_hz"] == 40.0
