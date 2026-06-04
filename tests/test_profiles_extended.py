"""测试所有扩展流派 Profile YAML 文件的完整性和有效性。

覆盖文件存在性、YAML 可解析性、核心字段完整性、参数合理范围。
"""

import pytest
import yaml
from pathlib import Path

from hermes_core.profiles import MixingProfile


# ── 所有应存在的流派 profile 文件 ──
_EXPECTED_GENRES = [
    "rock",
    "ballad",
    "hiphop",
    "rnb",
    "electronic",
    "folk",
    "jazz",
    "chinese_bel_canto",
]

_PROFILES_DIR = Path(__file__).resolve().parent.parent / "profiles"


def _profile_path(genre: str) -> Path:
    return _PROFILES_DIR / f"vocal_{genre}.yaml"


# ════════════════════════════════════════════════════════════
# 文件存在性
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFileExistence:
    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_file_exists(self, genre: str):
        """验证每个流派 profile 文件存在。"""
        path = _profile_path(genre)
        assert path.is_file(), f"Missing profile: {path}"


# ════════════════════════════════════════════════════════════
# YAML 可解析性 + MixingProfile 加载
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestYamlParsing:
    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_yaml_is_valid(self, genre: str):
        """验证 YAML 文件可被 yaml.safe_load 解析。"""
        path = _profile_path(genre)
        raw = yaml.safe_load(path.read_text())
        assert isinstance(raw, dict), f"{genre}: YAML root is not a dict"

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_loads_as_mixing_profile(self, genre: str):
        """验证可通过 MixingProfile.from_yaml 加载。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert isinstance(profile, MixingProfile), f"{genre}: not a MixingProfile"


# ════════════════════════════════════════════════════════════
# 核心字段完整性
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCoreFields:
    # 流派标识到显示名称关键词的映射（处理 Hip-Hop, R&B 等特殊情况）
    _GENRE_KEYWORDS: dict[str, str] = {
        "hiphop": "hip",
        "rnb": "r&b",
    }

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_has_name(self, genre: str):
        """验证 name 字段非空，且与流派相关。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert profile.name, f"{genre}: name is empty"
        keyword = self._GENRE_KEYWORDS.get(genre, genre.replace("_", " "))
        assert keyword.lower() in profile.name.lower(), (
            f"{genre}: name '{profile.name}' does not contain keyword '{keyword}'"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_has_description(self, genre: str):
        """验证 description 字段非空。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert profile.description, f"{genre}: description is empty"

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_vocal_chain_not_empty(self, genre: str):
        """验证 vocal_chain 至少有一个插件。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert len(profile.vocal_chain) >= 1, f"{genre}: vocal_chain is empty"

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_has_bus_reverb(self, genre: str):
        """验证 bus_reverb 已定义。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert profile.bus_reverb is not None, f"{genre}: bus_reverb is None"
        assert profile.bus_reverb.name, f"{genre}: bus_reverb name is empty"

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_has_master_limiter(self, genre: str):
        """验证 master_limiter 已定义。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert profile.master_limiter is not None, f"{genre}: master_limiter is None"
        assert profile.master_limiter.name, f"{genre}: master_limiter name is empty"


# ════════════════════════════════════════════════════════════
# 参数范围验证
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestParameterRanges:
    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_target_lufs_in_range(self, genre: str):
        """验证 target_lufs 在 -16 到 -8 dB 合理范围内。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert -16.0 <= profile.target_lufs <= -8.0, (
            f"{genre}: target_lufs={profile.target_lufs} out of range [-16, -8]"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_clip_gain_ref_db_is_standard(self, genre: str):
        """验证 clip_gain_ref_db 为行业标准 -18 dBFS。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert profile.clip_gain_ref_db == -18.0, (
            f"{genre}: clip_gain_ref_db={profile.clip_gain_ref_db}, expected -18.0"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_ceiling_db_reasonable(self, genre: str):
        """验证 ceiling_db 在合理保护范围内。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert -2.0 <= profile.ceiling_db <= 0.0, (
            f"{genre}: ceiling_db={profile.ceiling_db} out of range [-2.0, 0.0]"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_reverb_level_db_reasonable(self, genre: str):
        """验证 reverb_level_db 在合理混响发送范围内。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert -24.0 <= profile.reverb_level_db <= -4.0, (
            f"{genre}: reverb_level_db={profile.reverb_level_db} out of range [-24, -4]"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_tolerance_lufs_reasonable(self, genre: str):
        """验证 tolerance_lufs 在合理容差范围内。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert 0.1 <= profile.tolerance_lufs <= 1.0, (
            f"{genre}: tolerance_lufs={profile.tolerance_lufs} out of range [0.1, 1.0]"
        )


# ════════════════════════════════════════════════════════════
# 流派特征一致性
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGenreCharacteristics:
    """验证各流派的 target_lufs 符合流派特征约定。

    根据 genre_tables._GENRE_TARGET_LUFS 和设计文档：
    - 动态流派 (folk, ballad, jazz): -13 到 -14 dB
    - 商业流派 (rock, pop, rnb): -10 到 -11 dB
    - 高密度流派 (electronic, hiphop): -9 dB
    - 民族美声: -12 dB
    """

    # ── 动态流派 ──
    def test_ballad_is_dynamic(self):
        profile = MixingProfile.from_yaml(str(_profile_path("ballad")))
        assert profile.target_lufs <= -12.0, (
            f"ballad target_lufs={profile.target_lufs} should be <= -12 (dynamic)"
        )

    def test_folk_is_dynamic(self):
        profile = MixingProfile.from_yaml(str(_profile_path("folk")))
        assert profile.target_lufs <= -12.0, (
            f"folk target_lufs={profile.target_lufs} should be <= -12 (dynamic)"
        )

    def test_jazz_is_most_dynamic(self):
        profile = MixingProfile.from_yaml(str(_profile_path("jazz")))
        assert profile.target_lufs <= -13.0, (
            f"jazz target_lufs={profile.target_lufs} should be <= -13 (most dynamic)"
        )

    # ── 商业流派 ──
    def test_rock_is_competitive(self):
        profile = MixingProfile.from_yaml(str(_profile_path("rock")))
        assert -11.0 <= profile.target_lufs <= -9.0, (
            f"rock target_lufs={profile.target_lufs} should be competitive"
        )

    def test_rnb_is_balanced(self):
        profile = MixingProfile.from_yaml(str(_profile_path("rnb")))
        assert -12.0 <= profile.target_lufs <= -10.0, (
            f"rnb target_lufs={profile.target_lufs} should be balanced"
        )

    # ── 高密度流派 ──
    def test_electronic_is_loud(self):
        profile = MixingProfile.from_yaml(str(_profile_path("electronic")))
        assert profile.target_lufs >= -10.0, (
            f"electronic target_lufs={profile.target_lufs} should be >= -10 (loud)"
        )

    def test_hiphop_is_loud(self):
        profile = MixingProfile.from_yaml(str(_profile_path("hiphop")))
        assert profile.target_lufs >= -10.0, (
            f"hiphop target_lufs={profile.target_lufs} should be >= -10 (loud)"
        )

    # ── 民族美声 ──
    def test_chinese_bel_canto_is_moderate(self):
        profile = MixingProfile.from_yaml(str(_profile_path("chinese_bel_canto")))
        assert -13.0 <= profile.target_lufs <= -11.0, (
            f"chinese_bel_canto target_lufs={profile.target_lufs} should be moderate"
        )


# ════════════════════════════════════════════════════════════
# genre_table 完整性
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGenreTable:
    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_genre_table_has_self_entry(self, genre: str):
        """验证每个 profile 的 genre_table 包含自身流派条目。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        assert genre in profile.genre_table, (
            f"{genre}: genre_table missing self entry, keys={list(profile.genre_table.keys())}"
        )

    @pytest.mark.parametrize("genre", _EXPECTED_GENRES)
    def test_genre_table_values_are_valid_range(self, genre: str):
        """验证 genre_table 值为 [min, max] 且 min <= max。"""
        profile = MixingProfile.from_yaml(str(_profile_path(genre)))
        for k, v in profile.genre_table.items():
            assert isinstance(v, list) and len(v) == 2, (
                f"{genre}: genre_table['{k}']={v} is not a 2-element list"
            )
            assert 0 <= v[0] <= v[1] <= 20, (
                f"{genre}: genre_table['{k}']={v} range invalid (expect 0 <= min <= max <= 20)"
            )


# ════════════════════════════════════════════════════════════
# 总文件数
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTotalCount:
    def test_total_profile_count(self):
        """验证 profiles/ 目录下至少有 9 个 vocal_*.yaml 文件（1 + 8 新增）。"""
        profiles = sorted(_PROFILES_DIR.glob("vocal_*.yaml"))
        assert len(profiles) >= 9, (
            f"Expected >= 9 vocal_*.yaml files, got {len(profiles)}: {[p.name for p in profiles]}"
        )
