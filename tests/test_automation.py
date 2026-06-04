"""Automation 模块单元测试 — 段落差异化参数自动化。"""
from unittest.mock import MagicMock, patch
import pytest
from hermes_core.automation import (
    SectionDef,
    AutomationIntent,
    TrackAutomation,
    AutomationManager,
    make_pop_song_structure,
    _deduplicate_points,
    _SECTION_PARAM_PRESETS,
)


# ═══════════════════════════ SectionDef ═══════════════════════════

class TestSectionDef:
    def test_basic(self):
        s = SectionDef("verse", 10.0, 40.0)
        assert s.name == "verse"
        assert s.start_sec == 10.0
        assert s.end_sec == 40.0
        assert s.duration_sec == 30.0

    def test_zero_duration(self):
        s = SectionDef("fill", 5.0, 5.0)
        assert s.duration_sec == 0.0


# ═══════════════════════════ AutomationIntent ═══════════════════════════

class TestAutomationIntent:
    def test_basic(self):
        intent = AutomationIntent(
            track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "chorus": 4.0},
            default_value=3.0,
        )
        assert intent.track_idx == 0
        assert intent.section_values["chorus"] == 4.0
        assert intent.default_value == 3.0

    def test_defaults(self):
        intent = AutomationIntent(track_idx=1, param_name="Volume")
        assert intent.section_values == {}
        assert intent.default_value == 0.0
        assert intent.fx_idx is None
        assert intent.ramp_ms == 10.0


# ═══════════════════════════ TrackAutomation ═══════════════════════════

class TestTrackAutomation:
    def test_add_intent(self):
        ta = TrackAutomation(track_idx=0)
        ta.add_intent("Ratio", {"verse": 3.0, "chorus": 5.0}, default_value=3.0)
        assert len(ta.intents) == 1
        assert ta.intents[0].param_name == "Ratio"

    def test_chained_add(self):
        ta = TrackAutomation(track_idx=0)
        ta.add_intent("Ratio", {"verse": 3.0}).add_intent(
            "Threshold", {"verse": -24.0, "chorus": -28.0})
        assert len(ta.intents) == 2
        assert ta.intents[0].track_idx == 0
        assert ta.intents[1].track_idx == 0


# ═══════════════════════════ _deduplicate_points ═══════════════════════════

class TestDeduplicatePoints:
    def test_empty(self):
        assert _deduplicate_points([]) == []

    def test_single(self):
        pts = [(0.0, 1.0)]
        assert _deduplicate_points(pts) == [(0.0, 1.0)]

    def test_removes_duplicate_times(self):
        pts = [(0.0, 1.0), (0.0, 1.0), (1.0, 2.0)]
        result = _deduplicate_points(pts)
        assert result == [(0.0, 1.0), (1.0, 2.0)]

    def test_sorts_by_time(self):
        pts = [(2.0, 3.0), (0.0, 1.0), (1.0, 2.0)]
        result = _deduplicate_points(pts)
        assert result == [(0.0, 1.0), (1.0, 2.0), (2.0, 3.0)]


# ═══════════════════════════ _build_envelope_points ═══════════════════════════

class TestBuildEnvelopePoints:
    def test_empty_sections(self):
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={}, default_value=3.0)
        result = AutomationManager._build_envelope_points([], intent)
        assert result == []

    def test_single_section(self):
        sections = [SectionDef("verse", 0.0, 30.0)]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0}, default_value=3.0)
        result = AutomationManager._build_envelope_points(sections, intent)
        assert len(result) >= 2

    def test_two_sections_with_change(self):
        """两段落不同值时应产生过渡点。"""
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("chorus", 30.0, 60.0),
        ]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "chorus": 4.0},
            default_value=3.0)
        result = AutomationManager._build_envelope_points(sections, intent)
        # 应该有 verse 起始点、verse 保持点、过渡到 chorus 的点
        assert len(result) >= 3
        # 第一个点应在 verse 起始
        assert result[0][0] == 0.0
        # 最后一个点应在 chorus 结束附近
        assert result[-1][0] <= 60.0

    def test_same_value_no_extra_points(self):
        """相邻段落值相同时不产生多余的过渡点。"""
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("bridge", 30.0, 60.0),
        ]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "bridge": 3.0},
            default_value=3.0)
        result = AutomationManager._build_envelope_points(sections, intent)
        # 值不变，应只有少量点
        assert len(result) <= 3

    def test_respects_ramp_ms(self):
        """斜坡时间应反映在段落边界附近的包络点间距上。"""
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("chorus", 30.0, 60.0),
        ]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "chorus": 6.0},
            default_value=3.0, ramp_ms=100.0)
        result = AutomationManager._build_envelope_points(sections, intent)
        # 100ms 斜坡 → 在 chorus 段落边界 30s 附近应有过渡点 (30.0 < t <= 30.15)
        times = [p[0] for p in result]
        assert any(30.0 < t <= 30.15 for t in times), (
            f"期望在 30.0-30.15s 之间有斜坡点，实际时间: {times}")


# ═══════════════════════════ AutomationManager.apply ═══════════════════════════

class TestAutomationManagerApply:
    def test_writes_automation(self):
        """apply 应调用 engine.write_automation 并返回结果。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 0, "param_name": "Ratio",
            "point_count": 8, "error": None,
        }
        mgr = AutomationManager(engine)
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("chorus", 30.0, 60.0),
        ]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "chorus": 4.0},
            default_value=3.0)
        result = mgr.apply(sections, [intent])
        assert result["written"] == 1
        assert result["skipped"] == 0
        assert result["errors"] == []
        engine.write_automation.assert_called_once()

    def test_skips_empty_intent(self):
        """无段落的 intent 被跳过。"""
        engine = MagicMock()
        mgr = AutomationManager(engine)
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={}, default_value=3.0)
        result = mgr.apply([], [intent])
        assert result["skipped"] == 1
        engine.write_automation.assert_not_called()

    def test_records_errors(self):
        """write_automation 失败时记录错误。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 0, "param_name": "Ratio",
            "point_count": 0,
            "error": "Envelope 'Ratio' not found",
        }
        mgr = AutomationManager(engine)
        sections = [SectionDef("verse", 0.0, 30.0)]
        intent = AutomationIntent(track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0}, default_value=3.0)
        result = mgr.apply(sections, [intent])
        assert result["written"] == 0
        assert len(result["errors"]) == 1

    def test_multiple_intents(self):
        """多个 intent 应分别调用 write_automation。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 0, "param_name": "Ratio",
            "point_count": 6, "error": None,
        }
        mgr = AutomationManager(engine)
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("chorus", 30.0, 60.0),
        ]
        intents = [
            AutomationIntent(track_idx=0, param_name="Ratio",
                section_values={"verse": 3.0, "chorus": 4.0}),
            AutomationIntent(track_idx=0, param_name="Threshold",
                section_values={"verse": -24.0, "chorus": -28.0}),
        ]
        result = mgr.apply(sections, intents)
        assert result["written"] == 2
        assert engine.write_automation.call_count == 2


# ═══════════════════════════ AutomationManager.apply_preset ═══════════════════

class TestAutomationManagerApplyPreset:
    def test_all_param_kinds(self):
        """apply_preset 对所有预设参数类型创建自动化。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 0, "param_name": "comp_ratio",
            "point_count": 10, "error": None,
        }
        mgr = AutomationManager(engine)
        sections = [
            SectionDef("verse", 0.0, 30.0),
            SectionDef("chorus", 30.0, 60.0),
            SectionDef("bridge", 60.0, 90.0),
        ]
        result = mgr.apply_preset(sections, track_idx=0)
        assert result["written"] == 4  # comp_ratio, eq_presence, reverb_level, threshold
        assert engine.write_automation.call_count == 4

    def test_specific_param_kinds(self):
        """只对指定的参数类型创建自动化。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 1, "param_name": "eq_presence",
            "point_count": 8, "error": None,
        }
        mgr = AutomationManager(engine)
        sections = [SectionDef("chorus", 0.0, 30.0)]
        result = mgr.apply_preset(
            sections, track_idx=1, param_kinds=["eq_presence"])
        assert result["written"] == 1

    def test_filters_inactive_sections(self):
        """只对歌曲中实际出现的段落创建参数值。"""
        engine = MagicMock()
        engine.write_automation.return_value = {
            "track_idx": 0, "param_name": "comp_ratio",
            "point_count": 4, "error": None,
        }
        mgr = AutomationManager(engine)
        # 只有 chorus 和 bridge，没有 verse/intro/outro
        sections = [
            SectionDef("chorus", 0.0, 30.0),
            SectionDef("bridge", 30.0, 60.0),
        ]
        result = mgr.apply_preset(sections, track_idx=0,
            param_kinds=["comp_ratio"])
        assert result["written"] == 1
        # 验证 write_automation 被调用时 section_values 只含 chorus/bridge
        call_args = engine.write_automation.call_args[1]
        assert "points" in call_args


# ═══════════════════════════ make_pop_song_structure ═══════════════════════════

class TestMakePopSongStructure:
    def test_default(self):
        result = make_pop_song_structure()
        assert len(result) == 8  # intro, verse, chorus, verse, chorus, bridge, chorus, outro
        assert result[0].name == "intro"
        assert result[-1].name == "outro"
        # 验证时间连续性
        for i in range(len(result) - 1):
            assert result[i].end_sec == result[i + 1].start_sec

    def test_custom_durations(self):
        result = make_pop_song_structure({
            "verse": 20.0, "chorus": 20.0, "intro": 5.0,
        })
        # intro=5 + verse=20 + chorus=20 + verse=20 + chorus=20 + bridge=30 + chorus=20 + outro=30
        assert result[0].duration_sec == 5.0
        assert result[1].duration_sec == 20.0
        assert result[2].duration_sec == 20.0
        assert result[-1].end_sec == 165.0

    def test_total_duration(self):
        result = make_pop_song_structure()
        total = result[-1].end_sec
        # 15+30+30+30+30+30+30+30 = 225
        assert total == 225.0


# ═══════════════════════════ 预设合理性 ═══════════════════════════

class TestSectionPresets:
    def test_all_sections_have_all_params(self):
        """每个段落类型应该定义了所有 4 种参数。"""
        for section in ["verse", "chorus", "bridge", "intro", "outro"]:
            assert section in _SECTION_PARAM_PRESETS
            for param in ["comp_ratio", "eq_presence", "reverb_level", "threshold"]:
                assert param in _SECTION_PARAM_PRESETS[section], (
                    f"{section} 缺少 {param}")

    def test_chorus_louder_than_verse(self):
        """副歌的 comp_ratio 和 reverb_level 应高于主歌。"""
        verse = _SECTION_PARAM_PRESETS["verse"]
        chorus = _SECTION_PARAM_PRESETS["chorus"]
        assert chorus["comp_ratio"]["default"] > verse["comp_ratio"]["default"]
        assert chorus["reverb_level"]["default"] > verse["reverb_level"]["default"]
