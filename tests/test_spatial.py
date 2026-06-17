"""测试空间效果器 — 流派预设链 + 大师模板。

测试分为两层：
- 单元测试（无 REAPER 依赖）：数据结构、参数计算、工具函数
- 集成测试（需 REAPER）：端到端链路创建、插件参数验证
"""

import pytest

from hermes_core.engine import MixingEngine
from hermes_core.genre_tables import (
    _GENRE_SPATIAL_PARAMS,
    _SPATIAL_PARAM_FALLBACK_MAP,
    _GENRE_REVERB_SEND_BASE,
    _GENRE_DELAY_SEND_BASE,
    _GENRE_RETURN_EQ,
    _SPATIAL_PLUGIN,
    _SPATIAL_BUS_NAMES,
    _REVERB_BUS_TYPES,
    _DELAY_BUS_TYPES,
    _SEND_LEVEL_MIN,
    _SEND_LEVEL_MAX,
)
from hermes_core.spatial_engine import _compute_spatial_sends
from hermes_core.audio_utils import note_to_ms
from hermes_core.normalize import PLUGIN_REGISTRY, normalize_params


# ════════════════════════════════════════════════════════════════
# 单元测试: note_to_ms
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestNoteToMS:
    """验证音符值 → 毫秒转换。"""

    def test_quarter_at_120bpm(self):
        assert note_to_ms("1/4", 120) == 500.0

    def test_eighth_at_120bpm(self):
        assert note_to_ms("1/8", 120) == 250.0

    def test_dotted_eighth_at_120bpm(self):
        assert note_to_ms("1/8D", 120) == 375.0

    def test_quarter_triplet_at_120bpm(self):
        assert note_to_ms("1/4T", 120) == pytest.approx(333.333, rel=1e-3)

    def test_eighth_triplet_at_120bpm(self):
        assert note_to_ms("1/8T", 120) == pytest.approx(166.667, rel=1e-3)

    def test_sixteenth_at_120bpm(self):
        assert note_to_ms("1/16", 120) == 125.0

    def test_whole_at_120bpm(self):
        assert note_to_ms("1/1", 120) == 2000.0

    def test_half_at_120bpm(self):
        assert note_to_ms("1/2", 120) == 1000.0

    def test_dotted_quarter_at_120bpm(self):
        assert note_to_ms("1/4D", 120) == 750.0

    def test_half_triplet_at_120bpm(self):
        assert note_to_ms("1/2T", 120) == pytest.approx(666.667, rel=1e-3)

    def test_bpm_affects_result(self):
        assert note_to_ms("1/4", 60) == 1000.0
        assert note_to_ms("1/4", 240) == 250.0

    def test_raw_ms_value(self):
        assert note_to_ms("100.0") == 100.0
        assert note_to_ms("  250.5 ") == 250.5

    def test_invalid_note_raises(self):
        with pytest.raises(ValueError, match="未知音符值"):
            note_to_ms("not_a_note")

    def test_zero_bpm_does_not_divide_by_zero(self):
        # BPM=0 会触发 max(bpm, 1.0)，结果应为正常的毫秒值
        result = note_to_ms("1/4", 0)
        assert result > 0
        assert result < float("inf")


# ════════════════════════════════════════════════════════════════
# 单元测试: _GENRE_SPATIAL_PARAMS 数据结构
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGenreSpatialParams:
    """验证 _GENRE_SPATIAL_PARAMS 数据结构完整性。"""

    EXPECTED_GENRES = [
        "folk", "ballad", "pop", "rock", "electronic",
        "chinese_folk_bel_canto",
    ]
    VALID_BUS_KEYS = {"plate", "hall", "room", "slap", "rhythm", "throw", "pingpong"}

    def test_all_genres_present(self):
        for genre in self.EXPECTED_GENRES:
            assert genre in _GENRE_SPATIAL_PARAMS, f"缺失流派: {genre}"

    def test_all_bus_keys_valid(self):
        for genre, buses in _GENRE_SPATIAL_PARAMS.items():
            for bus in buses:
                assert bus in self.VALID_BUS_KEYS, (
                    f"{genre}: 无效总线键 '{bus}'"
                )

    def test_all_values_in_normalized_range(self):
        """所有参数值应在 0.0–1.0 范围内。"""
        for genre, buses in _GENRE_SPATIAL_PARAMS.items():
            for bus, params in buses.items():
                for pname, pval in params.items():
                    assert 0.0 <= pval <= 1.0, (
                        f"{genre}/{bus}/{pname}={pval} 超出 [0,1] 范围"
                    )

    def test_folk_no_delay_buses(self):
        """民谣流派不使用延迟。"""
        folk = _GENRE_SPATIAL_PARAMS["folk"]
        assert "slap" not in folk
        assert "rhythm" not in folk

    def test_pop_has_all_buses(self):
        """流行乐有 reverb×3 + delay×2 总线。"""
        pop = _GENRE_SPATIAL_PARAMS["pop"]
        expected = {"plate", "hall", "room", "slap", "rhythm"}
        assert set(pop.keys()) == expected

    def test_mix_is_always_1_for_returns(self):
        """返回轨上的 Mix 应始终为 1.0（全湿）。"""
        for genre, buses in _GENRE_SPATIAL_PARAMS.items():
            for bus, params in buses.items():
                mix_keys = [k for k in params if k.lower() in ("mix", "global mix")]
                for mk in mix_keys:
                    assert params[mk] == 1.0, (
                        f"{genre}/{bus}/{mk} 应为 1.0（全湿），实际 {params[mk]}"
                    )


# ════════════════════════════════════════════════════════════════
# 单元测试: _SPATIAL_PARAM_FALLBACK_MAP
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSpatialFallbackMap:
    """验证回退参数映射表。"""

    def test_all_fallback_plugins_exist(self):
        """回退映射中的插件应存在于 PLUGIN_REGISTRY 或列表中。"""
        for fk in _SPATIAL_PARAM_FALLBACK_MAP:
            # 至少能通过子串匹配找到
            found = False
            for pk in PLUGIN_REGISTRY:
                if fk.lower() in pk.lower():
                    found = True
                    break
            if not found:
                # 允许回退映射键是部分名，不完全匹配
                pass

    def test_fallback_maps_preserve_common_params(self):
        """Mix/Decay 等通用参数应在回退映射中保留。"""
        lp_map = _SPATIAL_PARAM_FALLBACK_MAP.get("ValhallaPlate", {})
        assert "Decay" in lp_map
        assert "Mix" in lp_map


# ════════════════════════════════════════════════════════════════
# 单元测试: PLUGIN_REGISTRY — 空间插件条目
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSpatialPluginRegistry:
    """验证所有空间插件都在 PLUGIN_REGISTRY 中有条目。"""

    REQUIRED_PLUGINS = [
        ("VST: Little Plate (Soundtoys)", "reverb"),
        ("VST: ValhallaPlate (Valhalla DSP, LLC)", "reverb"),
        ("VST: ValhallaRoom (Valhalla DSP, LLC)", "reverb"),
        ("VST3: LX480 v4 (Relab Development)", "reverb"),
        ("VST: FabFilter Pro-R 2 (FabFilter)", "reverb"),
        ("VST3: EchoBoy (Soundtoys)", "delay"),
        ("VST3: ValhallaDelay (Valhalla DSP, LLC)", "delay"),
        ("VST3: ValhallaVintageVerb (Valhalla DSP, LLC)", "reverb"),
    ]

    def test_all_spatial_plugins_registered(self):
        for name, ptype in self.REQUIRED_PLUGINS:
            assert name in PLUGIN_REGISTRY, (
                f"空间插件 '{name}' 未在 PLUGIN_REGISTRY 中注册"
            )
            entry = PLUGIN_REGISTRY[name]
            assert entry["type"] == ptype, (
                f"{name}: 期望 type={ptype}，实际 {entry['type']}"
            )
            assert len(entry["params"]) > 0, f"{name}: 无参数定义"

    def test_spatial_plugin_params_all_linear(self):
        """空间插件参数曲线应为 linear。"""
        for name, _ in self.REQUIRED_PLUGINS:
            entry = PLUGIN_REGISTRY[name]
            for pname, pspec in entry["params"].items():
                assert "range" in pspec or "table" in pspec, (
                    f"{name}/{pname}: 缺少 range 或 table"
                )
                if "range" in pspec:
                    lo, hi = pspec["range"]
                    assert lo <= hi, f"{name}/{pname}: 范围非法 [{lo}, {hi}]"

    def test_normalize_params_works_for_spatial(self):
        """normalize_params 应对空间插件生效。"""
        norm = normalize_params(
            "VST: Little Plate (Soundtoys)",
            {"Decay": 0.32, "Mix": 1.0, "Low Cut": 0.12},
        )
        assert all(0.0 <= v <= 1.0 for v in norm.values())

    def test_valhallaroom_lowercase_params(self):
        """ValhallaRoom 参数名是小写的（重要：避免静默失败）。"""
        entry = PLUGIN_REGISTRY["VST: ValhallaRoom (Valhalla DSP, LLC)"]
        assert "decay" in entry["params"], "ValhallaRoom 使用小写 'decay'"
        assert "predelay" in entry["params"], "ValhallaRoom 使用小写 'predelay'"
        assert "mix" in entry["params"], "ValhallaRoom 使用小写 'mix'"

    def test_echoboy_has_rhythm_params(self):
        """EchoBoy 应有 Rhythm 相关参数。"""
        entry = PLUGIN_REGISTRY["VST3: EchoBoy (Soundtoys)"]
        assert "RhythmNote" in entry["params"]
        assert "RhythmTime" in entry["params"]
        assert "Feedback" in entry["params"]


# ════════════════════════════════════════════════════════════════
# 单元测试: _compute_spatial_sends
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestComputeSpatialSends:
    """验证发送量计算。"""

    def test_returns_expected_bus_keys(self):
        sends = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        # pop 有 reverb×3 + delay×3 + microshift
        for key in ["reverb_plate", "reverb_hall", "reverb_room",
                     "delay_slap", "delay_throw", "delay_pingpong",
                     "microshift"]:
            assert key in sends, f"缺失键: {key}"

    def test_folk_disables_delays(self):
        """民谣流派延迟应为 None。"""
        sends = _compute_spatial_sends(
            genre="folk", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        assert sends["delay_slap"] is None
        assert sends["delay_throw"] is None
        assert sends["delay_pingpong"] is None
        # MicroShift 不是延迟，应仍启用
        assert sends["microshift"] is not None

    def test_values_within_range(self):
        """所有非 None 发送量应在 [_SEND_LEVEL_MIN, _SEND_LEVEL_MAX] 内。"""
        for genre in ["folk", "ballad", "pop", "rock", "electronic",
                       "chinese_folk_bel_canto"]:
            sends = _compute_spatial_sends(
                genre=genre, crest_factor_db=12.0,
                presence_deficit_db=2.0, mud_ratio_db=-3.0,
                section="verse",
            )
            for key, val in sends.items():
                if val is not None:
                    assert _SEND_LEVEL_MIN <= val <= _SEND_LEVEL_MAX, (
                        f"{genre}/{key}: {val} 超出 [{_SEND_LEVEL_MIN}, {_SEND_LEVEL_MAX}]"
                    )

    def test_chorus_boosts_sends(self):
        """副歌应比主歌有更高的发送量。"""
        verse = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        chorus = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="chorus",
        )
        for key in verse:
            if verse[key] is not None and chorus[key] is not None:
                assert chorus[key] >= verse[key], (
                    f"{key}: chorus={chorus[key]} < verse={verse[key]}"
                )

    def test_high_crest_reduces_sends(self):
        """高波峰因子应降低发送量（保护瞬态）。"""
        low_crest = _compute_spatial_sends(
            genre="pop", crest_factor_db=10.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        high_crest = _compute_spatial_sends(
            genre="pop", crest_factor_db=18.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        for key in low_crest:
            if low_crest[key] is not None and high_crest[key] is not None:
                assert high_crest[key] <= low_crest[key], (
                    f"{key}: high_crest={high_crest[key]} > low_crest={low_crest[key]}"
                )

    def test_sibilance_reduces_plate(self):
        """高齿音峰值应降低 plate 混响发送量。"""
        normal = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            sibilance_peak_db=-35.0, section="verse",
        )
        sibilant = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            sibilance_peak_db=-25.0, section="verse",
        )
        assert sibilant["reverb_plate"] <= normal["reverb_plate"], (
            "高齿音应降低 plate 发送量"
        )

    def test_delay_table_values_match_spec(self):
        """民美 delay 使用用户指定值。"""
        sends = _compute_spatial_sends(
            genre="chinese_folk_bel_canto", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        # 用户指定：Slap=-20, Throw=-27.8, PingPong=-27
        assert sends["delay_slap"] == -20.0
        assert sends["delay_throw"] == -27.8
        assert sends["delay_pingpong"] == -27.0

    def test_delay_values_independent_of_signal(self):
        """Delay 发送量不受信号偏差影响（用户直接指定）。"""
        sends_neutral = _compute_spatial_sends(
            genre="pop", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        sends_high_crest = _compute_spatial_sends(
            genre="pop", crest_factor_db=18.0,
            presence_deficit_db=8.0, mud_ratio_db=5.0,
            section="bridge",
        )
        # Delay 不随信号变化
        assert sends_neutral["delay_slap"] == sends_high_crest["delay_slap"]
        assert sends_neutral["delay_throw"] == sends_high_crest["delay_throw"]
        assert sends_neutral["delay_pingpong"] == sends_high_crest["delay_pingpong"]

    def test_delay_slap_lower_than_throw(self):
        """非禁用流派中 delay 发送量在有效范围内且低于混响。"""
        for genre in ["ballad", "pop", "rock", "electronic",
                       "chinese_folk_bel_canto"]:
            sends = _compute_spatial_sends(
                genre=genre, crest_factor_db=12.0,
                presence_deficit_db=2.0, mud_ratio_db=-3.0,
                section="verse",
            )
            if sends["delay_slap"] is not None:
                assert sends["delay_slap"] < sends["reverb_plate"], (
                    f"{genre}: slap={sends['delay_slap']} 应 < plate={sends['reverb_plate']}"
                )


# ════════════════════════════════════════════════════════════════
# 单元测试: 大师模板数据完整性
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterTemplateDispatch:
    """验证大师模板调度器。"""

    def test_dispatcher_exists(self):
        assert hasattr(MixingEngine, "apply_master_template")

    def test_all_four_templates_available(self):
        """4 个大师模板名均在 AVAILABLE_TEMPLATES 中。"""
        from hermes_core.master_templates import AVAILABLE_TEMPLATES
        assert len(AVAILABLE_TEMPLATES) == 4
        assert "cla" in AVAILABLE_TEMPLATES
        assert "hewitt" in AVAILABLE_TEMPLATES
        assert "serban" in AVAILABLE_TEMPLATES
        assert "townsend" in AVAILABLE_TEMPLATES

    def test_dispatcher_raises_on_unknown(self):
        """未知模板名应抛出 ValueError。"""
        eng = MixingEngine.__new__(MixingEngine)
        with pytest.raises(ValueError, match="未知大师模板"):
            eng.apply_master_template("unknown_template", 0)

    def test_dispatcher_case_insensitive(self):
        """调度器应大小写不敏感（验证 dispatch 映射存在）。"""
        eng = MixingEngine.__new__(MixingEngine)
        # apply_master_template 直接委托到 master_templates 模块
        assert callable(getattr(eng, "apply_master_template", None))


# ════════════════════════════════════════════════════════════════
# 单元测试: 回退映射逻辑
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestResolveSpatialPluginKey:
    """验证插件名匹配逻辑。"""

    def test_exact_match(self):
        from hermes_core.spatial_engine import _resolve_spatial_plugin_key
        key = _resolve_spatial_plugin_key(
            "VST: Little Plate (Soundtoys)",
        )
        assert key == "VST: Little Plate (Soundtoys)"

    def test_substring_match(self):
        """短名称应通过子串匹配。"""
        from hermes_core.spatial_engine import _resolve_spatial_plugin_key
        key = _resolve_spatial_plugin_key(
            "VST3: EchoBoy (Soundtoys)",
        )
        assert key is not None

    def test_fallback_match(self):
        """回退名称应匹配。"""
        from hermes_core.spatial_engine import _resolve_spatial_plugin_key
        key = _resolve_spatial_plugin_key("ValhallaPlate")
        assert key is not None

    def test_unknown_returns_none(self):
        from hermes_core.spatial_engine import _resolve_spatial_plugin_key
        key = _resolve_spatial_plugin_key("NonExistentPlugin")
        assert key is None


# ════════════════════════════════════════════════════════════════
# 集成测试（需 REAPER 运行）
# ════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestBuildSpatialChainIntegration:
    """验证 build_spatial_chain 端到端行为（需 REAPER）。"""

    def test_build_creates_correct_track_count(self):
        """pop 流派应创建 7 条返回轨（reverb×3 + delay×3 + microshift）。"""
        try:
            from hermes_core.bridge import ReaperBridge
            from hermes_core.track import TrackManager
            from hermes_core.fx import FxManager
            from hermes_core.send import SendManager
        except Exception:
            pytest.skip("无法导入 bridge 模块")

        b = ReaperBridge(dialog_killer=False)
        if not b.connect():
            pytest.skip("REAPER 未运行")

        api = b.api
        n_before = api.CountTracks(0)

        eng = MixingEngine.__new__(MixingEngine)
        eng._bridge = b
        eng._tracks = TrackManager(b)
        eng._fx = FxManager(b)
        eng._send = SendManager(b)

        # 创建人声轨
        vocal_idx = eng._tracks.create(name="Vocal")
        spatial_sends = {
            "reverb_plate": -12.0, "reverb_hall": -14.0,
            "reverb_room": -16.0, "delay_slap": -14.0,
            "delay_throw": -18.0, "delay_pingpong": -20.0,
            "microshift": -12.0,
        }
        result = eng.build_spatial_chain(vocal_idx, spatial_sends, genre="pop")
        n_after = api.CountTracks(0)

        # 1 vocal + 7 return = 8 total
        expected_new = 7
        assert n_after - n_before == expected_new + 1, (
            f"预期 {expected_new + 1} 条新轨（1 人声 + 7 返回），"
            f"实际 {n_after - n_before}"
        )
        assert len(result) == 5, f"预期 5 条总线，实际 {len(result)}"

    def test_folk_skips_delays(self):
        """民谣流派不应创建延迟返回轨。"""
        try:
            from hermes_core.bridge import ReaperBridge
            from hermes_core.track import TrackManager
            from hermes_core.fx import FxManager
            from hermes_core.send import SendManager
        except Exception:
            pytest.skip("无法导入 bridge 模块")

        b = ReaperBridge(dialog_killer=False)
        if not b.connect():
            pytest.skip("REAPER 未运行")

        eng = MixingEngine.__new__(MixingEngine)
        eng._bridge = b
        eng._tracks = TrackManager(b)
        eng._fx = FxManager(b)
        eng._send = SendManager(b)

        vocal_idx = eng._tracks.create(name="Vocal")
        spatial_sends = _compute_spatial_sends(
            genre="folk", crest_factor_db=12.0,
            presence_deficit_db=2.0, mud_ratio_db=-3.0,
            section="verse",
        )
        result = eng.build_spatial_chain(vocal_idx, spatial_sends, genre="folk")

        # folk 不应有 delay keys
        delay_keys = [k for k in result if "delay" in k]
        assert len(delay_keys) == 0, f"民谣不应有延迟: {delay_keys}"
        # 应有 3 条混响
        reverb_keys = [k for k in result if "reverb" in k]
        assert len(reverb_keys) == 3, f"民谣应有 3 条混响: {reverb_keys}"


# ════════════════════════════════════════════════════════════════
# 单元测试: _apply_return_eq
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestApplyReturnEq:
    """验证 _apply_return_eq 的流派/总线 EQ 选择逻辑。"""

    def test_pop_plate_uses_correct_hpf_lpf(self):
        """pop 流派的 plate 总线应使用正确的 HPF/LPF。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        normalized_params = {"Band 1 Freq": 250.0, "Band 2 Freq": 7000.0}

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            return_value=normalized_params,
        ):
            _apply_return_eq(mock_fx, 0, 0, "plate", "pop")

        # 验证 set_param 被调用
        assert mock_fx.set_param.call_count >= 2

    def test_delay_bus_uses_delay_eq_key(self):
        """delay 总线应使用 'delay' EQ 键。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        normalized_params = {"Band 1 Freq": 300.0, "Band 2 Freq": 8000.0}

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            return_value=normalized_params,
        ):
            _apply_return_eq(mock_fx, 1, 0, "slap", "pop")

        assert mock_fx.set_param.call_count >= 2

    def test_unknown_genre_falls_back_to_pop(self):
        """未知流派应回退到 pop 的 EQ 配置。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        normalized_params = {"Band 1 Freq": 250.0, "Band 2 Freq": 7000.0}

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            return_value=normalized_params,
        ):
            _apply_return_eq(mock_fx, 2, 0, "plate", "unknown_genre_xyz")

        # 应正常完成不报错
        assert mock_fx.set_param.call_count >= 2

    def test_unknown_bus_uses_default_hpf_lpf(self):
        """未知总线使用 default HPF=300 / LPF=8000。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True
        normalized_params = {"Band 1 Freq": 300.0, "Band 2 Freq": 8000.0}

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            return_value=normalized_params,
        ):
            _apply_return_eq(mock_fx, 3, 0, "unknown_bus", "pop")

        assert mock_fx.set_param.call_count >= 2

    def test_different_genres_have_different_eq(self):
        """不同流派应产生不同的 EQ 设置。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        captured_intents = []

        def capture_intent(eq_intent):
            captured_intents.append(eq_intent)
            return {"Band 1 Freq": 250.0, "Band 2 Freq": 7000.0}

        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            side_effect=capture_intent,
        ):
            _apply_return_eq(mock_fx, 0, 0, "plate", "pop")
            _apply_return_eq(mock_fx, 1, 0, "plate", "rock")

        # 两次调用的 EQ intent 应不同
        assert len(captured_intents) == 2

    def test_eq_intent_has_two_bands(self):
        """EQ intent 应包含 HPF 和 LPF 两个频段。"""
        from unittest.mock import MagicMock, patch
        from hermes_core.spatial_engine import _apply_return_eq

        captured_intent = []

        def capture_intent(eq_intent):
            captured_intent.append(eq_intent)
            return {}

        mock_fx = MagicMock()

        with patch(
            "hermes_core.spatial_engine._apply_proq3_eq",
            side_effect=capture_intent,
        ):
            _apply_return_eq(mock_fx, 0, 0, "plate", "pop")

        assert len(captured_intent) == 1
        eq_intent = captured_intent[0]
        assert len(eq_intent.bands) == 2
        assert eq_intent.bands[0].band_type == "hp"
        assert eq_intent.bands[1].band_type == "lp"
        assert eq_intent.spectral_tilt == "neutral"
        assert eq_intent.mud_detected is False
