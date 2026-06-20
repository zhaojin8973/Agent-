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


# ═══════════════════════════ get_default_vocal_chain ═══════════════════════════

class TestGetDefaultVocalChain:
    """测试默认 9 段人声处理链。"""

    def test_returns_nine_stages(self):
        from hermes_core.profiles import get_default_vocal_chain, FXPreset
        chain = get_default_vocal_chain()
        assert len(chain) == 9
        for fx in chain:
            assert isinstance(fx, FXPreset)

    def test_order_is_correct(self):
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        types = [fx.fx_type for fx in chain]
        # Vocal A 链: eq → fet → saturation → deesser → color_eq_232d
        #   → rvox → harmonic → tube_opto_sh → air_eq
        assert "eq" in types[:2]      # EQ 在前两位
        assert "deesser" in types
        assert "air_eq" in types
        assert types[-1] == "air_eq"


# ═══════════════════════════ all_fx_names with bus_delay ═══════════════════════

class TestAllFxNamesBusDelay:
    """测试 all_fx_names 包含 bus_delay 的情况。"""

    def test_includes_bus_delay(self):
        from hermes_core.profiles import MixingProfile, FXPreset
        eb = FXPreset(name="EchoBoy", fx_type="delay")
        profile = MixingProfile(
            vocal_chain=[FXPreset(name="EQ", fx_type="eq")],
            backing_chain=[],
            bus_reverb=None,
            bus_delay=eb,
        )
        names = profile.all_fx_names()
        assert "EchoBoy" in names


# ════════════════════════════════════════════════════════════
# get_default_vocal_chain (模块级函数)
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGetDefaultVocalChainModule:
    """测试模块级 get_default_vocal_chain 函数。"""

    def test_returns_nine_stages(self):
        """应返回 9 个处理阶段。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        assert len(chain) == 9

    def test_order_is_hpf_first(self):
        """第一个应为 HPF EQ。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        assert chain[0].fx_type == "eq"
        assert chain[0].eq_position == "pre"

    def test_contains_all_expected_types(self):
        """应包含所有 9 种处理类型。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        types = [p.fx_type for p in chain]
        assert "eq" in types
        assert "saturation" in types
        assert "fet" in types
        assert "deesser" in types
        assert "color_eq_232d" in types
        assert "rvox" in types
        assert "harmonic" in types
        assert "tube_opto_sh" in types
        assert "air_eq" in types

    def test_all_presets_have_name(self):
        """每个预设都应有名称。"""
        from hermes_core.profiles import get_default_vocal_chain
        chain = get_default_vocal_chain()
        for preset in chain:
            assert preset.name != ""
            assert preset.fx_type != ""


# ════════════════════════════════════════════════════════════
# MixingProfile.for_genre (classmethod)
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMixingProfileForGenre:
    """测试 MixingProfile.for_genre 类方法。默认 variant="a" (Vocal A 无 UAD)。"""

    def test_pop_genre_loads_vocal_a_by_default(self):
        """pop 流派默认加载 Vocal A (无 UAD) YAML。"""
        profile = MixingProfile.for_genre(genre="pop")
        assert isinstance(profile, MixingProfile)
        assert len(profile.vocal_chain) > 0
        # Vocal A 链不含 UAD 插件
        names = " ".join(fx.name for fx in profile.vocal_chain)
        assert "UAD" not in names

    def test_rock_genre_loads_from_yaml(self):
        """rock 流派应从 YAML 加载。"""
        profile = MixingProfile.for_genre(genre="rock")
        assert isinstance(profile, MixingProfile)
        assert len(profile.vocal_chain) > 0

    def test_folk_genre_loads_from_yaml(self):
        """folk 流派应从 YAML 加载。"""
        profile = MixingProfile.for_genre(genre="folk")
        assert isinstance(profile, MixingProfile)

    def test_electronic_genre_loads_from_yaml(self):
        """electronic 流派加载。"""
        profile = MixingProfile.for_genre(genre="electronic")
        assert isinstance(profile, MixingProfile)

    def test_chinese_bel_canto_loads_from_yaml(self):
        """chinese_folk_bel_canto 流派加载。"""
        profile = MixingProfile.for_genre(genre="chinese_folk_bel_canto")
        assert isinstance(profile, MixingProfile)

    def test_unknown_genre_falls_back_to_vocal_a_pop(self):
        """未知流派 → _GENRE_YAML_MAP 回退到 vocal_a_pop。"""
        profile = MixingProfile.for_genre(genre="unknown_xyz")
        assert isinstance(profile, MixingProfile)
        assert len(profile.vocal_chain) > 0

    def test_vocal_b_with_empty_variant(self):
        """variant="" 加载无前缀 Vocal B (有 UAD)。"""
        profile = MixingProfile.for_genre(genre="pop", variant="")
        assert isinstance(profile, MixingProfile)
        assert len(profile.vocal_chain) > 0
        # Vocal B 链应包含 UAD 插件
        names = " ".join(fx.name for fx in profile.vocal_chain)
        assert "UAD" in names

    def test_variant_a_is_default(self):
        """不传 variant 默认走 Vocal A (无 UAD)。"""
        profile = MixingProfile.for_genre(genre="pop")
        assert isinstance(profile, MixingProfile)
        names = " ".join(fx.name for fx in profile.vocal_chain)
        assert "UAD" not in names
        assert "CLA-76" in names
