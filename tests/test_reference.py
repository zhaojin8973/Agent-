"""测试 hermes_core.reference — ReferenceProfile 和 ReferenceMatcher。

所有测试使用 Mock，不依赖真实 REAPER 或音频文件。
"""

import os
import tempfile
import wave

import pytest

from hermes_core.reference import ReferenceProfile, ReferenceMatcher


@pytest.mark.unit
class TestReferenceProfile:
    """ReferenceProfile 数据类测试。"""

    def test_default_values(self):
        profile = ReferenceProfile(path="/tmp/test.wav")
        assert profile.path == "/tmp/test.wav"
        assert profile.duration_sec == 0.0
        assert profile.integrated_lufs is None
        assert profile.short_term_lufs is None
        assert profile.true_peak_db is None
        assert profile.spectral_tilt_db is None
        assert profile.low_mid_balance_db is None
        assert profile.presence_band_db is None
        assert profile.dynamic_range_db is None

    def test_full_construction(self):
        profile = ReferenceProfile(
            path="/tmp/ref.wav",
            duration_sec=180.0,
            integrated_lufs=-12.0,
            short_term_lufs=-10.0,
            true_peak_db=-0.5,
            spectral_tilt_db=-1.5,
            low_mid_balance_db=2.0,
            presence_band_db=-2.0,
            dynamic_range_db=8.0,
        )
        assert profile.integrated_lufs == -12.0
        assert profile.dynamic_range_db == 8.0
        assert profile.low_mid_balance_db == 2.0

    def test_dataclass_is_mutable(self):
        """ReferenceProfile 是可变的（非 frozen），方便分析填充。"""
        profile = ReferenceProfile(path="/tmp/test.wav")
        profile.integrated_lufs = -14.0
        profile.spectral_tilt_db = -3.0
        assert profile.integrated_lufs == -14.0
        assert profile.spectral_tilt_db == -3.0


@pytest.mark.unit
class TestAnalyze:
    """ReferenceMatcher.analyze 分析测试。"""

    def test_raises_file_not_found(self):
        """不存在的文件抛出 FileNotFoundError。"""
        matcher = ReferenceMatcher()
        with pytest.raises(FileNotFoundError, match="文件不存在"):
            matcher.analyze("/nonexistent/path/ref.wav")

    def test_analyzes_valid_wav_file(self):
        """有效的 WAV 文件应返回填充了 duration 的 profile。"""
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                # 1 秒立体声 16-bit 48kHz = 48000 frames * 2 ch * 2 bytes = 192000 bytes
                wf.writeframes(b"\x00\x00" * 48000 * 2)

        try:
            profile = matcher.analyze(tmp_path)
            assert isinstance(profile, ReferenceProfile)
            assert profile.path == tmp_path
            assert profile.duration_sec == pytest.approx(1.0, abs=0.1)
            assert profile.integrated_lufs is not None
            assert profile.spectral_tilt_db is not None
            assert profile.dynamic_range_db is not None
        finally:
            os.unlink(tmp_path)

    def test_analyze_handles_non_wav_file(self):
        """非 WAV 文件（如文本文件）应优雅降级，返回占位数据。"""
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False, mode="w") as tmp:
            tmp.write("this is not a wav file")
            tmp_path = tmp.name

        try:
            profile = matcher.analyze(tmp_path)
            assert isinstance(profile, ReferenceProfile)
            assert profile.duration_sec == 0.0
            # 占位值应被填充
            assert profile.integrated_lufs == -14.0
        finally:
            os.unlink(tmp_path)

    def test_analyze_caches_result(self):
        """分析结果应被缓存。"""
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            profile = matcher.analyze(tmp_path)
            cached = matcher.get_cached(tmp_path)
            assert cached is not None
            assert cached.integrated_lufs == profile.integrated_lufs
        finally:
            os.unlink(tmp_path)

    def test_analyze_returns_reference_profile(self):
        """返回值必须是 ReferenceProfile 实例。"""
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            profile = matcher.analyze(tmp_path)
            assert isinstance(profile, ReferenceProfile)
        finally:
            os.unlink(tmp_path)


@pytest.mark.unit
class TestGenerateMatchParams:
    """generate_match_params 匹配参数生成测试。"""

    def _make_ref(self, **overrides) -> ReferenceProfile:
        """创建测试用 ReferenceProfile。"""
        defaults = {
            "path": "/tmp/ref.wav",
            "duration_sec": 200.0,
            "integrated_lufs": -12.0,
            "short_term_lufs": -10.0,
            "true_peak_db": -0.3,
            "spectral_tilt_db": -2.0,
            "low_mid_balance_db": 0.5,
            "presence_band_db": -3.0,
            "dynamic_range_db": 8.0,
        }
        defaults.update(overrides)
        return ReferenceProfile(**defaults)

    def test_returns_all_expected_keys(self):
        matcher = ReferenceMatcher()
        ref = self._make_ref()
        params = matcher.generate_match_params(ref)

        assert "target_lufs" in params
        assert "eq_adjustments" in params
        assert "compression_hint" in params
        assert "stereo_width_hint" in params
        assert "spectral_targets" in params

    def test_uses_reference_lufs_when_no_override(self):
        matcher = ReferenceMatcher()
        ref = self._make_ref(integrated_lufs=-12.0)
        params = matcher.generate_match_params(ref)

        assert params["target_lufs"] == -12.0

    def test_uses_custom_target_lufs(self):
        matcher = ReferenceMatcher()
        ref = self._make_ref()
        params = matcher.generate_match_params(ref, target_lufs=-16.0)

        assert params["target_lufs"] == -16.0

    def test_falls_back_to_default_lufs_when_none(self):
        """当 reference 和 target_lufs 都为 None 时使用 -14.0。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(integrated_lufs=None)
        params = matcher.generate_match_params(ref)

        assert params["target_lufs"] == -14.0

    def test_eq_adjustments_from_spectral_tilt(self):
        """频谱斜率应产生 high_shelf EQ 调整建议。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(spectral_tilt_db=-4.0)
        params = matcher.generate_match_params(ref)

        shelf_adjustments = [
            a for a in params["eq_adjustments"]
            if a["band"] == "high_shelf"
        ]
        assert len(shelf_adjustments) > 0
        # -4.0 * 0.5 = -2.0 dB 补偿
        assert shelf_adjustments[0]["gain_db_hint"] == pytest.approx(-2.0)

    def test_eq_adjustments_from_low_mid_balance(self):
        """低频/中频平衡应产生 bell EQ 调整建议。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(low_mid_balance_db=3.0)
        params = matcher.generate_match_params(ref)

        bell_adjustments = [
            a for a in params["eq_adjustments"]
            if a["band"] == "bell" and a["freq_hz"] == 300.0
        ]
        assert len(bell_adjustments) > 0
        # -3.0 * 0.3 = -0.9 dB
        assert bell_adjustments[0]["gain_db_hint"] == pytest.approx(-0.9)

    def test_eq_adjustments_for_low_presence(self):
        """临场感不足时应建议提升。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(presence_band_db=-5.0)
        params = matcher.generate_match_params(ref)

        presence_adjustments = [
            a for a in params["eq_adjustments"]
            if a.get("freq_hz") == 3000.0
        ]
        assert len(presence_adjustments) > 0
        # abs(-5.0) * 0.5 = 2.5, capped at 3.0
        assert presence_adjustments[0]["gain_db_hint"] == 2.5

    def test_no_presence_adjustment_when_adequate(self):
        """临场感足够时不产生提升建议。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(presence_band_db=-1.0)  # -1 > -2 阈值
        params = matcher.generate_match_params(ref)

        presence_adjustments = [
            a for a in params["eq_adjustments"]
            if a.get("freq_hz") == 3000.0
        ]
        assert len(presence_adjustments) == 0

    def test_compression_hint_for_high_dynamic_range(self):
        """高动态范围 (>12 dB) 应使用 light 压缩。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(dynamic_range_db=14.0)
        params = matcher.generate_match_params(ref)

        assert params["compression_hint"]["amount"] == "light"
        assert params["compression_hint"]["ratio"] == 2.0
        assert params["compression_hint"]["target_gr_db"] == 2.0

    def test_compression_hint_for_low_dynamic_range(self):
        """低动态范围 (<6 dB) 应使用 heavy 压缩。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(dynamic_range_db=4.0)
        params = matcher.generate_match_params(ref)

        assert params["compression_hint"]["amount"] == "heavy"
        assert params["compression_hint"]["ratio"] == 4.0
        assert params["compression_hint"]["target_gr_db"] == 5.0

    def test_compression_hint_default_medium(self):
        """中等动态范围使用 medium 压缩。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(dynamic_range_db=9.0)
        params = matcher.generate_match_params(ref)

        assert params["compression_hint"]["amount"] == "medium"

    def test_spectral_targets_contain_reference_data(self):
        matcher = ReferenceMatcher()
        ref = self._make_ref(dynamic_range_db=7.5, spectral_tilt_db=-3.0)
        params = matcher.generate_match_params(ref)

        targets = params["spectral_targets"]
        assert targets["reference_lufs"] == -12.0
        assert targets["reference_dynamic_range"] == 7.5
        assert targets["reference_spectral_tilt"] == -3.0

    def test_handles_none_fields_gracefully(self):
        """所有字段为 None 时不应抛出异常。"""
        matcher = ReferenceMatcher()
        ref = ReferenceProfile(path="/tmp/empty.wav")
        params = matcher.generate_match_params(ref)

        assert params["target_lufs"] == -14.0  # 回退默认值
        assert isinstance(params["eq_adjustments"], list)
        # None fields 不应产生对应的 EQ 调整
        assert len(params["eq_adjustments"]) == 0

    def test_presence_boost_capped_at_3db(self):
        """临场感提升不应超过 3.0 dB。"""
        matcher = ReferenceMatcher()
        ref = self._make_ref(presence_band_db=-20.0)  # 非常低
        params = matcher.generate_match_params(ref)

        presence_adjustment = next(
            (a for a in params["eq_adjustments"] if a.get("freq_hz") == 3000.0),
            None,
        )
        assert presence_adjustment is not None
        assert presence_adjustment["gain_db_hint"] <= 3.0


@pytest.mark.unit
class TestCompare:
    """compare 对比分析测试。"""

    def test_compare_returns_all_keys(self):
        """对比结果包含所有预期键。"""
        matcher = ReferenceMatcher()
        ref = ReferenceProfile(
            path="/tmp/ref.wav",
            integrated_lufs=-12.0,
            spectral_tilt_db=-2.0,
            dynamic_range_db=8.0,
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            mix_path = tmp.name
            with wave.open(mix_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            result = matcher.compare(mix_path, ref)
            assert "match_quality" in result
            assert "lufs_diff_db" in result
            assert "spectral_diff_db" in result
            assert "eq_suggestions" in result
            assert "overall_score" in result
        finally:
            os.unlink(mix_path)

    def test_compare_with_missing_mix_file(self):
        """混音文件不存在时返回 poor 质量。"""
        matcher = ReferenceMatcher()
        ref = ReferenceProfile(path="/tmp/ref.wav")
        result = matcher.compare("/nonexistent/mix.wav", ref)

        assert result["match_quality"] == "poor"
        assert result["overall_score"] == 0.0
        assert result["eq_suggestions"] == []

    def test_overall_score_between_0_and_1(self):
        """综合评分应在 0-1 范围内。"""
        matcher = ReferenceMatcher()
        ref = ReferenceProfile(
            path="/tmp/ref.wav",
            integrated_lufs=-12.0,
            spectral_tilt_db=-2.0,
            dynamic_range_db=8.0,
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            mix_path = tmp.name
            with wave.open(mix_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            result = matcher.compare(mix_path, ref)
            assert 0.0 <= result["overall_score"] <= 1.0
        finally:
            os.unlink(mix_path)

    def test_match_quality_good(self):
        """相同文件自对比应得到 good 或 fair 质量。"""
        from tests.conftest import make_test_signal

        matcher = ReferenceMatcher()
        wav = make_test_signal(
            os.path.join(tempfile.mkdtemp(), "ref.wav"),
            duration_sec=3.0, level_db=-12.0,
        )
        ref = matcher.analyze(wav)
        # 同一文件对比 — 应至少为 fair 或 better
        result = matcher.compare(wav, ref)
        assert result["match_quality"] in ("good", "fair"), \
            f"自对比应为 good/fair，实际: {result['match_quality']}"

    def test_lufs_diff_suggestion_generated(self):
        """响度差异超出阈值时应产生调整建议。"""
        matcher = ReferenceMatcher()
        ref = ReferenceProfile(
            path="/tmp/ref.wav",
            integrated_lufs=-20.0,  # 很低的参考响度
            spectral_tilt_db=-2.0,
            dynamic_range_db=10.0,
        )
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            mix_path = tmp.name
            with wave.open(mix_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            result = matcher.compare(mix_path, ref)
            # 混音 LUFS (-14) vs 参考 (-20) → 差异 +6 LUFS
            lufs_suggestions = [
                s for s in result["eq_suggestions"]
                if s["parameter"] == "output_gain"
            ]
            assert len(lufs_suggestions) > 0
        finally:
            os.unlink(mix_path)


@pytest.mark.unit
class TestCache:
    """缓存管理测试。"""

    def test_get_cached_returns_none_for_unknown_path(self):
        matcher = ReferenceMatcher()
        assert matcher.get_cached("/unknown/path.wav") is None

    def test_get_cached_returns_profile_after_analyze(self):
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            profile = matcher.analyze(tmp_path)
            cached = matcher.get_cached(tmp_path)
            assert cached is profile
        finally:
            os.unlink(tmp_path)

    def test_clear_cache_removes_all(self):
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(44100)
                wf.writeframes(b"\x00\x00" * 44100)

        try:
            matcher.analyze(tmp_path)
            assert matcher.cached_count == 1
            matcher.clear_cache()
            assert matcher.cached_count == 0
            assert matcher.get_cached(tmp_path) is None
        finally:
            os.unlink(tmp_path)

    def test_cached_count_tracks_multiple_references(self):
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp1, \
             tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp2:
            for tmp in (tmp1, tmp2):
                with wave.open(tmp.name, "wb") as wf:
                    wf.setnchannels(1)
                    wf.setsampwidth(2)
                    wf.setframerate(44100)
                    wf.writeframes(b"\x00\x00" * 44100)

        try:
            matcher.analyze(tmp1.name)
            matcher.analyze(tmp2.name)
            assert matcher.cached_count == 2
        finally:
            os.unlink(tmp1.name)
            os.unlink(tmp2.name)


@pytest.mark.unit
class TestCalculateMatchScore:
    """_calculate_match_score 静态方法测试。"""

    def test_perfect_match_returns_1_0(self):
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=0.0,
            spectral_diff=0.0,
            ref_dr=8.0,
            mix_dr=8.0,
        )
        assert score == pytest.approx(1.0)

    def test_large_lufs_diff_reduces_score(self):
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=6.0,  # 6 dB 差异 → 响度评分为 0
            spectral_diff=0.0,
            ref_dr=8.0,
            mix_dr=8.0,
        )
        assert score < 0.7  # 响度占 0.4 权重，0 分 → 最高 0.6

    def test_large_spectral_diff_reduces_score(self):
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=0.0,
            spectral_diff=12.0,  # 12 dB 差异 → 频谱评分为 0
            ref_dr=8.0,
            mix_dr=8.0,
        )
        assert score < 0.8  # 频谱占 0.3 权重

    def test_dynamic_range_diff_reduces_score(self):
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=0.0,
            spectral_diff=0.0,
            ref_dr=8.0,
            mix_dr=20.0,  # 12 dB 差异 → DR 评分为 0
        )
        assert score < 0.8  # DR 占 0.3 权重

    def test_handles_none_spectral_diff(self):
        """spectral_diff 为 None 时使用中性分 0.5。"""
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=0.0,
            spectral_diff=None,
            ref_dr=8.0,
            mix_dr=8.0,
        )
        # 响度 1.0 * 0.4 + 频谱 0.5 * 0.3 + DR 1.0 * 0.3 = 0.4 + 0.15 + 0.3 = 0.85
        assert score == pytest.approx(0.85)

    def test_handles_none_dynamic_range(self):
        """ref_dr 或 mix_dr 为 None 时使用中性分 0.5。"""
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=0.0,
            spectral_diff=0.0,
            ref_dr=None,
            mix_dr=8.0,
        )
        # 响度 1.0 * 0.4 + 频谱 1.0 * 0.3 + DR 0.5 * 0.3 = 0.4 + 0.3 + 0.15 = 0.85
        assert score == pytest.approx(0.85)

    def test_all_poor_scores(self):
        """所有维度都很差时总分应接近 0。"""
        score = ReferenceMatcher._calculate_match_score(
            lufs_diff=20.0,
            spectral_diff=30.0,
            ref_dr=4.0,
            mix_dr=20.0,
        )
        assert score < 0.2

    def test_score_within_valid_range(self):
        """评分始终在 0.0-1.0 范围内。"""
        test_cases = [
            (0.0, 0.0, 8.0, 8.0),
            (3.0, 4.0, 10.0, 7.0),
            (-5.0, -2.0, 6.0, 12.0),
            (0.0, None, None, None),
            (10.0, 15.0, 2.0, 18.0),
        ]
        for lufs_diff, spec_diff, ref_dr, mix_dr in test_cases:
            score = ReferenceMatcher._calculate_match_score(
                lufs_diff=lufs_diff,
                spectral_diff=spec_diff,
                ref_dr=ref_dr,
                mix_dr=mix_dr,
            )
            assert 0.0 <= score <= 1.0, (
                f"Score {score} out of range for ({lufs_diff}, {spec_diff}, "
                f"{ref_dr}, {mix_dr})"
            )


@pytest.mark.unit
class TestIntegration:
    """端到端流程测试（仍使用 Mock — 无 REAPER 依赖）。"""

    def test_full_workflow_analyze_generate_compare(self):
        """完整的分析 → 生成匹配参数 → 对比工作流。"""
        matcher = ReferenceMatcher()
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp_path = tmp.name
            with wave.open(tmp_path, "wb") as wf:
                wf.setnchannels(2)
                wf.setsampwidth(2)
                wf.setframerate(48000)
                wf.writeframes(b"\x00\x00" * 48000 * 2)  # 2 秒

        try:
            # 1. 分析参考曲目
            ref = matcher.analyze(tmp_path)
            assert isinstance(ref, ReferenceProfile)

            # 2. 生成匹配参数
            params = matcher.generate_match_params(ref, target_lufs=-16.0)
            assert params["target_lufs"] == -16.0
            assert len(params["eq_adjustments"]) >= 0

            # 3. 对比混音
            result = matcher.compare(tmp_path, ref)
            assert result["match_quality"] in ("good", "fair", "poor")
            assert result["overall_score"] >= 0.0

            # 4. 验证缓存
            assert matcher.cached_count == 1
        finally:
            os.unlink(tmp_path)
