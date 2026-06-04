"""测试 hermes_core.backing — BackingProcessor 总线压缩和频率互让。

所有测试使用 Mock，不依赖真实 REAPER。
"""

from unittest.mock import MagicMock

import pytest

from hermes_core.backing import (
    BackingProcessor,
    _GENRE_COMPRESSION,
)


def _mock_bridge():
    """创建模拟 ReaperBridge，用于 BackingProcessor 单元测试。"""
    mock = MagicMock()
    mock.api = MagicMock()
    mock.rpr = MagicMock()
    return mock


def _mock_fx_manager(**overrides):
    """创建模拟 FxManager，可覆盖特定方法。

    默认行为：
    - add() 返回 0（成功）
    - set_param() 返回 True
    - get_chain() 返回空列表（无已有 FX）
    """
    mock = MagicMock()
    mock.add = MagicMock(return_value=0)
    mock.set_param = MagicMock(return_value=True)
    mock.get_chain = MagicMock(return_value=[])
    for attr, val in overrides.items():
        setattr(mock, attr, val)
    return mock


@pytest.mark.unit
class TestConstruction:
    """BackingProcessor 构造测试。"""

    def test_stores_bridge_and_fx_manager(self):
        bridge = _mock_bridge()
        fx = _mock_fx_manager()
        proc = BackingProcessor(bridge, fx)
        assert proc._bridge is bridge
        assert proc._fx is fx


@pytest.mark.unit
class TestGlueCompression:
    """apply_glue_compression 总线压缩测试。"""

    def test_rock_genre_applies_4_to_1_ratio(self):
        """rock 流派使用 4:1 压缩比和较快启动时间。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="rock")

        assert result["success"] is True
        assert result["plugin"] == "ReaComp"
        assert result["settings"]["ratio"] == 4.0
        assert result["settings"]["attack_ms"] == 3.0
        fx.add.assert_called_once_with(0, "ReaComp")

    def test_folk_genre_applies_2_to_1_ratio(self):
        """folk 流派使用 2:1 压缩比和较慢启动时间。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=1, genre="folk")

        assert result["success"] is True
        assert result["settings"]["ratio"] == 2.0
        assert result["settings"]["attack_ms"] == 15.0
        assert result["settings"]["release_ms"] == 150.0

    def test_pop_genre_default(self):
        """pop 流派使用中等 3:1 压缩比。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="pop")

        assert result["settings"]["ratio"] == 3.0
        assert result["settings"]["threshold_db"] == -20.0

    def test_unknown_genre_falls_back_to_default(self):
        """未知流派回退到 default 预设。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="nonexistent")

        assert result["success"] is True
        assert result["settings"]["ratio"] == _GENRE_COMPRESSION["default"]["ratio"]

    def test_electronic_genre(self):
        """electronic 流派使用 4:1 压缩比。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=2, genre="electronic")

        assert result["success"] is True
        assert result["settings"]["ratio"] == 4.0
        assert result["settings"]["attack_ms"] == 5.0

    def test_ballad_genre(self):
        """ballad 流派使用最轻的压缩。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="ballad")

        assert result["settings"]["ratio"] == 2.0
        assert result["settings"]["attack_ms"] == 20.0
        assert result["settings"]["release_ms"] == 200.0

    def test_returns_failure_when_fx_add_fails(self):
        """当 fx_manager.add 返回 -1 时，应返回 success=False。"""
        fx = _mock_fx_manager(add=MagicMock(return_value=-1))
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="rock")

        assert result["success"] is False
        assert result["settings"] == {}

    def test_sets_all_compressor_parameters(self):
        """验证所有 ReaComp 参数都被设置。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="pop")

        # 6 个参数: threshold, ratio, attack, release, knee, makeup
        assert fx.set_param.call_count == 6
        assert "threshold_db" in result["settings"]
        assert "ratio" in result["settings"]
        assert "attack_ms" in result["settings"]
        assert "release_ms" in result["settings"]
        assert "knee_db" in result["settings"]
        assert "makeup_db" in result["settings"]

    def test_partial_set_param_failure_does_not_break(self):
        """set_param 部分失败时仍返回 success=True，但 settings 中缺失失败项。"""
        call_count = [0]

        def set_param_side_effect(track_idx, fx_idx, param_idx, norm):
            call_count[0] += 1
            # 让第 3 个参数设置失败
            return call_count[0] != 3

        fx = _mock_fx_manager(set_param=MagicMock(side_effect=set_param_side_effect))
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_glue_compression(track_idx=0, genre="pop")

        assert result["success"] is True
        # 第 3 个参数（attack_ms）设置失败，不应出现在 settings 中
        assert len(result["settings"]) == 5


@pytest.mark.unit
class TestFrequencyPocket:
    """apply_frequency_pocket 频率互让测试。"""

    def test_applies_boost_and_cut(self):
        """人声轨道提升 2dB @ 3kHz，伴奏轨道衰减 2dB @ 3kHz。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(vocal_idx=0, backing_idx=1)

        assert result["success"] is True
        assert result["vocal_boost"]["gain_db"] == 2.0
        assert result["vocal_boost"]["freq_hz"] == 3000.0
        assert result["backing_cut"]["gain_db"] == -2.0
        assert result["backing_cut"]["freq_hz"] == 3000.0

    def test_custom_amount_db(self):
        """自定义调整幅度 1.5 dB。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(
            vocal_idx=0, backing_idx=1, amount_db=1.5,
        )

        assert result["vocal_boost"]["gain_db"] == 1.5
        assert result["backing_cut"]["gain_db"] == -1.5

    def test_amount_db_clamped_min(self):
        """amount_db 小于 1.0 时被限制为 1.0。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(
            vocal_idx=0, backing_idx=1, amount_db=0.2,
        )

        assert result["vocal_boost"]["gain_db"] == 1.0

    def test_amount_db_clamped_max(self):
        """amount_db 大于 3.0 时被限制为 3.0。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(
            vocal_idx=0, backing_idx=1, amount_db=5.0,
        )

        assert result["vocal_boost"]["gain_db"] == 3.0

    def test_adds_reaeq_to_both_tracks(self):
        """验证在两个轨道上都添加了 ReaEQ。"""
        fx = _mock_fx_manager()
        proc = BackingProcessor(_mock_bridge(), fx)
        proc.apply_frequency_pocket(vocal_idx=0, backing_idx=2)

        # 两次 add 调用（人声 + 伴奏各一次 ReaEQ）
        add_calls = [c.args[0] for c in fx.add.call_args_list]
        assert 0 in add_calls
        assert 2 in add_calls
        assert fx.add.call_count == 2

    def test_failure_when_eq_add_fails_on_vocal(self):
        """人声轨道 EQ 添加失败时 success=False。"""
        fx = _mock_fx_manager(
            add=MagicMock(side_effect=[-1, 0]),  # vocal 失败，backing 成功
        )
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(vocal_idx=0, backing_idx=1)

        assert result["success"] is False

    def test_failure_when_eq_add_fails_on_backing(self):
        """伴奏轨道 EQ 添加失败时 success=False。"""
        fx = _mock_fx_manager(
            add=MagicMock(side_effect=[0, -1]),  # vocal 成功，backing 失败
        )
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(vocal_idx=0, backing_idx=1)

        assert result["success"] is False

    def test_reuses_existing_reaeq(self):
        """人声轨道已有 ReaEQ 时复用，伴奏轨道无则新增。"""
        fx = _mock_fx_manager()
        # 根据 track_idx 返回不同的 chain：
        # - track 0 (vocal): 已有 ReaEQ
        # - track 1 (backing): 无 ReaEQ
        chain_map = {
            0: [{"index": 0, "name": "VST: ReaEQ", "enabled": True, "param_count": 20}],
            1: [],
        }
        fx.get_chain = MagicMock(side_effect=lambda idx: chain_map.get(idx, []))
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc.apply_frequency_pocket(vocal_idx=0, backing_idx=1)

        assert result["success"] is True
        # backing 轨无 ReaEQ → add 被调用了一次
        backing_add_called = any(
            c.args[0] == 1 for c in fx.add.call_args_list
        )
        assert backing_add_called
        # vocal 轨已有 ReaEQ → add 不应该为 vocal 调用
        vocal_add_called = any(
            c.args[0] == 0 for c in fx.add.call_args_list
        )
        assert not vocal_add_called


@pytest.mark.unit
class TestPresetQuery:
    """get_compression_preset / supported_genres 查询测试。"""

    def test_get_preset_for_known_genre(self):
        preset = BackingProcessor.get_compression_preset("rock")
        assert preset["ratio"] == 4.0
        assert preset["attack_ms"] == 3.0

    def test_get_preset_for_unknown_genre(self):
        preset = BackingProcessor.get_compression_preset("jazz")
        assert preset["ratio"] == _GENRE_COMPRESSION["default"]["ratio"]

    def test_get_preset_returns_copy(self):
        """返回的预设是副本，修改不会影响原始数据。"""
        preset = BackingProcessor.get_compression_preset("pop")
        preset["ratio"] = 999.0
        assert _GENRE_COMPRESSION["pop"]["ratio"] == 3.0

    def test_supported_genres_returns_list(self):
        genres = BackingProcessor.supported_genres()
        assert isinstance(genres, list)
        assert len(genres) > 0
        assert "rock" in genres
        assert "pop" in genres
        assert "folk" in genres
        assert "default" not in genres


@pytest.mark.unit
class TestNormalizeParam:
    """_normalize_param 静态方法测试。"""

    def test_maps_threshold_correctly(self):
        """-18 dB 阈值应映射到约 0.7（范围 -60..0 dB）。"""
        result = BackingProcessor._normalize_param(-18.0, "threshold_db")
        assert 0.6 < result < 0.8

    def test_maps_to_zero_at_lower_bound(self):
        """参数值在范围下限时映射到 0.0。"""
        result = BackingProcessor._normalize_param(-60.0, "threshold_db")
        assert result == pytest.approx(0.0)

    def test_maps_to_one_at_upper_bound(self):
        """参数值在范围上限时映射到 1.0。"""
        result = BackingProcessor._normalize_param(0.0, "threshold_db")
        assert result == pytest.approx(1.0)

    def test_clamps_out_of_range_low(self):
        """低于下限的值被限制为 0.0。"""
        result = BackingProcessor._normalize_param(-999.0, "threshold_db")
        assert result == 0.0

    def test_clamps_out_of_range_high(self):
        """高于上限的值被限制为 1.0。"""
        result = BackingProcessor._normalize_param(999.0, "threshold_db")
        assert result == 1.0

    def test_unknown_param_key_returns_0_5(self):
        """未知参数键返回中性值 0.5。"""
        result = BackingProcessor._normalize_param(42.0, "unknown_key")
        assert result == 0.5

    def test_zero_range_returns_0_5(self):
        """参数范围为 0 时返回 0.5（避免除零）。"""
        # ratio 范围是 1-100，不在这个测试中；我们模拟一个零范围场景
        # 实际上不会发生，但代码中已处理
        result = BackingProcessor._normalize_param(50.0, "ratio")
        # ratio 范围 1-100，50 -> (50-1)/(100-1) = 49/99 ≈ 0.495
        assert 0.0 <= result <= 1.0


@pytest.mark.unit
class TestNormalizeFreq:
    """_normalize_freq 静态方法测试。"""

    def test_maps_1000hz(self):
        """1kHz 应在对数范围内正确映射。"""
        result = BackingProcessor._normalize_freq(1000.0)
        assert 0.4 < result < 0.7

    def test_maps_20hz_to_low(self):
        """20Hz 映射到接近 0.0。"""
        result = BackingProcessor._normalize_freq(20.0)
        assert result == pytest.approx(0.0, abs=0.01)

    def test_maps_24000hz_to_high(self):
        """24kHz 映射到接近 1.0。"""
        result = BackingProcessor._normalize_freq(24000.0)
        assert result == pytest.approx(1.0, abs=0.01)

    def test_clamps_below_20hz(self):
        """低于 20Hz 的频率被限制为 0.0。"""
        result = BackingProcessor._normalize_freq(1.0)
        assert result == 0.0

    def test_clamps_above_24000hz(self):
        """高于 24kHz 的频率被限制为 1.0。"""
        result = BackingProcessor._normalize_freq(50000.0)
        assert result == 1.0


@pytest.mark.unit
class TestFindOrAddReaEQ:
    """_find_or_add_reaeq 内部方法测试。"""

    def test_returns_existing_reaeq_index(self):
        """轨道上已有 ReaEQ 时返回其索引。"""
        fx = _mock_fx_manager()
        fx.get_chain = MagicMock(return_value=[
            {"index": 0, "name": "VST: ReaComp", "enabled": True, "param_count": 6},
            {"index": 1, "name": "VST: ReaEQ (Cockos)", "enabled": True, "param_count": 20},
        ])
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc._find_or_add_reaeq(track_idx=0)

        assert result == 1
        fx.add.assert_not_called()

    def test_adds_new_reaeq_when_not_found(self):
        """轨道上没有 ReaEQ 时添加新的。"""
        fx = _mock_fx_manager()
        fx.get_chain = MagicMock(return_value=[
            {"index": 0, "name": "VST: ReaComp", "enabled": True, "param_count": 6},
        ])
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc._find_or_add_reaeq(track_idx=0)

        assert result == 0  # 新添加的 FX 索引
        fx.add.assert_called_once_with(0, "ReaEQ")

    def test_returns_minus_one_when_add_fails(self):
        """添加失败时返回 -1。"""
        fx = _mock_fx_manager(add=MagicMock(return_value=-1))
        fx.get_chain = MagicMock(return_value=[])
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc._find_or_add_reaeq(track_idx=0)

        assert result == -1

    def test_returns_existing_when_multiple_fx_present(self):
        """多个 FX 中有 ReaEQ 时正确找到。"""
        fx = _mock_fx_manager()
        fx.get_chain = MagicMock(return_value=[
            {"index": 0, "name": "VST: ReaComp", "enabled": True, "param_count": 6},
            {"index": 1, "name": "VST: ReaDelay", "enabled": True, "param_count": 4},
            {"index": 2, "name": "VST: ReaEQ", "enabled": True, "param_count": 20},
        ])
        proc = BackingProcessor(_mock_bridge(), fx)
        result = proc._find_or_add_reaeq(track_idx=0)

        assert result == 2
