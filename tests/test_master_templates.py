"""master_templates 单元测试 — 大师模板调度器和 Mock 模板验证。

所有测试通过 Mock 完成，无需 REAPER。
"""
from unittest.mock import MagicMock, patch, call
import pytest

from hermes_core.master_templates import (
    apply_master_template,
    _townsend_hp_lp_eq,
    _TEMPLATE_DISPATCH,
    AVAILABLE_TEMPLATES,
)


# ════════════════════════════════════════════════════════════════
# apply_master_template 调度器
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterTemplateDispatch:
    """验证 apply_master_template 的模板名调度逻辑。"""

    def _mock_args(self):
        return (
            MagicMock(),  # bridge
            MagicMock(),  # tracks
            MagicMock(),  # fx
            MagicMock(),  # send
        )

    def test_cla_dispatched(self):
        """'cla' 调度到 _master_cla。"""
        from hermes_core.master_templates import _master_cla
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_cla",
                   return_value={"delays": {}}) as mock_fn:
            result = apply_master_template(
                bridge, tracks, fx, send, "cla", 0,
            )
            mock_fn.assert_called_once()
            assert result == {"delays": {}}

    def test_chris_lord_alge_alias(self):
        """'chris lord-alge' 别名调度到 _master_cla。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_cla",
                   return_value={"delays": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "chris lord-alge", 0,
            )
            assert result == {"delays": {}}

    def test_hewitt_dispatched(self):
        """'hewitt' 调度到 _master_hewitt。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_hewitt",
                   return_value={"plates": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "hewitt", 0,
            )
            assert result == {"plates": {}}

    def test_ryan_hewitt_alias(self):
        """'ryan hewitt' 别名调度到 _master_hewitt。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_hewitt",
                   return_value={"plates": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "Ryan Hewitt", 0,
            )
            assert result == {"plates": {}}

    def test_serban_dispatched(self):
        """'serban' 调度到 _master_serban。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_serban",
                   return_value={"buses": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "serban", 0,
            )
            assert result == {"buses": {}}

    def test_serban_ghenea_alias(self):
        """'serban ghenea' 别名调度到 _master_serban。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_serban",
                   return_value={"buses": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "Serban Ghenea", 0,
            )
            assert result == {"buses": {}}

    def test_townsend_dispatched(self):
        """'townsend' 调度到 _master_townsend。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_townsend",
                   return_value={}):
            result = apply_master_template(
                bridge, tracks, fx, send, "townsend", 0,
            )
            assert result == {}

    def test_devin_townsend_alias(self):
        """'devin townsend' 别名调度到 _master_townsend。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_townsend",
                   return_value={}):
            result = apply_master_template(
                bridge, tracks, fx, send, "Devin Townsend", 0,
            )
            assert result == {}

    def test_unknown_template_raises_valueerror(self):
        """未知模板名 → ValueError。"""
        bridge, tracks, fx, send = self._mock_args()
        with pytest.raises(ValueError, match="未知大师模板"):
            apply_master_template(
                bridge, tracks, fx, send, "unknown_master", 0,
            )

    def test_case_insensitive(self):
        """模板名大小写不敏感。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_cla",
                   return_value={"delays": {}}):
            result = apply_master_template(
                bridge, tracks, fx, send, "CLA", 0,
            )
            assert result == {"delays": {}}

    def test_passes_genre_and_bpm(self):
        """genre 和 bpm 传递给模板函数。"""
        bridge, tracks, fx, send = self._mock_args()
        with patch("hermes_core.master_templates._master_cla",
                   return_value={}) as mock_fn:
            apply_master_template(
                bridge, tracks, fx, send, "cla", 3,
                genre="rock", bpm=120.0,
            )
            call_args = mock_fn.call_args[0]
            # 签名: (bridge, tracks, fx, send, vocal_track, genre, bpm)
            assert call_args[4] == 3       # vocal_track
            assert call_args[5] == "rock"  # genre
            assert call_args[6] == 120.0   # bpm

    def test_available_templates_list(self):
        """AVAILABLE_TEMPLATES 包含所有 4 个模板。"""
        assert set(AVAILABLE_TEMPLATES) == {"cla", "hewitt", "serban", "townsend"}

    def test_dispatch_keys_cover_all_aliases(self):
        """_TEMPLATE_DISPATCH 的值覆盖所有 4 个函数名。"""
        func_names = set(_TEMPLATE_DISPATCH.values())
        assert "_master_cla" in func_names
        assert "_master_hewitt" in func_names
        assert "_master_serban" in func_names
        assert "_master_townsend" in func_names


# ════════════════════════════════════════════════════════════════
# _townsend_hp_lp_eq
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTownsendHpLpEq:
    """验证 Townsend HPF/LPF EQ 块。"""

    def test_eq_idx_negative_returns_early(self):
        """eq_idx < 0 → 提前返回不报错。"""
        mock_fx = MagicMock()
        _townsend_hp_lp_eq(mock_fx, 0, -1)
        mock_fx.set_param.assert_not_called()

    def test_normal_case_applies_hpf_and_lpf(self):
        """正常情况 → 应用 HPF@400Hz + LPF@3kHz。"""
        mock_fx = MagicMock()
        mock_fx.set_param.return_value = True

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={"Band 1 Freq": 400.0, "Band 2 Freq": 3000.0}):
            _townsend_hp_lp_eq(mock_fx, 0, 0)

        # 应调用 set_param 至少 2 次（HPF + LPF）
        assert mock_fx.set_param.call_count >= 2

    def test_set_param_exception_swallowed(self):
        """set_param 异常应被吞没不传播。"""
        mock_fx = MagicMock()
        mock_fx.set_param.side_effect = RuntimeError("param not found")

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={"Band 1 Freq": 400.0}):
            _townsend_hp_lp_eq(mock_fx, 0, 0)
        # 不应抛出异常


# ════════════════════════════════════════════════════════════════
# _master_cla — Chris Lord-Alge
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterCLA:
    """验证 CLA 模板：3 delays + 3 reverbs + 9 cross-sends。"""

    def _mock_deps(self):
        """返回标准 mock 对象。"""
        bridge = MagicMock()
        tracks = MagicMock()
        # 每次 create 返回递增的索引
        tracks.create.side_effect = lambda name: len(tracks.create.call_args_list)
        fx = MagicMock()
        fx.add.return_value = 0  # 假设 FX 添加成功
        fx.set_param.return_value = True
        send = MagicMock()
        send.create.return_value = {"index": 0}
        return bridge, tracks, fx, send

    def test_creates_3_delay_tracks(self):
        """应创建 3 条延迟轨道。"""
        from hermes_core.master_templates import _master_cla
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            result = _master_cla(bridge, tracks, fx, send, 0, "pop", None)

        # 3 delays + 3 reverbs = 6 条轨道
        assert tracks.create.call_count == 6
        assert "slap" in result["delays"]
        assert "throw" in result["delays"]
        assert "tape" in result["delays"]

    def test_creates_3_reverb_tracks(self):
        """应创建 3 条混响轨道。"""
        from hermes_core.master_templates import _master_cla
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            result = _master_cla(bridge, tracks, fx, send, 0, "pop", None)

        assert "plate" in result["reverbs"]
        assert "room" in result["reverbs"]
        assert "hall" in result["reverbs"]

    def test_cross_sends_from_delays_to_reverbs(self):
        """延迟 → 混响 跨发送（CLA 秘方）。"""
        from hermes_core.master_templates import _master_cla
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            result = _master_cla(bridge, tracks, fx, send, 0, "pop", None)

        # 3 delays × 3 reverbs = 9 cross-sends
        assert len(result["cross_sends"]) == 9

    def test_sends_from_vocal_to_delays(self):
        """人声 → 延迟发送 level=-15dB。"""
        from hermes_core.master_templates import _master_cla
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            _master_cla(bridge, tracks, fx, send, 0, "pop", None)

        # send.create(src=0, dest=...) 被调用
        vocal_sends = [
            c for c in send.create.call_args_list
            if c[1].get("src") == 0
        ]
        assert len(vocal_sends) >= 6  # 3 delays + 3 reverbs


# ════════════════════════════════════════════════════════════════
# _master_hewitt — Ryan Hewitt
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterHewitt:
    """验证 Hewitt 模板：3 层 EMT 140 板混响。"""

    def _mock_deps(self):
        bridge = MagicMock()
        tracks = MagicMock()
        tracks.create.side_effect = lambda name: len(tracks.create.call_args_list)
        fx = MagicMock()
        fx.add.return_value = 0
        fx.set_param.return_value = True
        send = MagicMock()
        send.create.return_value = {"index": 0}
        return bridge, tracks, fx, send

    def test_creates_3_plate_tracks(self):
        """应创建 3 条板混响轨道（mono/stereo/wide）。"""
        from hermes_core.master_templates import _master_hewitt
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            result = _master_hewitt(bridge, tracks, fx, send, 0, "pop", None)

        assert tracks.create.call_count == 3
        assert "plate_1_mono" in result["plates"]
        assert "plate_2_stereo" in result["plates"]
        assert "plate_3_wide" in result["plates"]

    def test_uad_emt_140_preferred(self):
        """优先尝试 UAD EMT 140。"""
        from hermes_core.master_templates import _master_hewitt
        bridge, tracks, fx, send = self._mock_deps()
        # UAD 添加成功
        fx.add.return_value = 1

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            _master_hewitt(bridge, tracks, fx, send, 0, "pop", None)

        # 第一次 add 调用检查 UAD EMT 140
        uad_calls = [
            c for c in fx.add.call_args_list
            if "UAD EMT 140" in str(c)
        ]
        assert len(uad_calls) > 0

    def test_fallback_to_valhalla_plate(self):
        """UAD 不可用时回退到 ValhallaPlate。"""
        from hermes_core.master_templates import _master_hewitt
        bridge, tracks, fx, send = self._mock_deps()
        # UAD 添加失败（-1），ValhallaPlate 成功（0）
        call_count = [0]

        def fx_add_side_effect(track, name):
            call_count[0] += 1
            if "UAD" in name:
                return -1
            if "Valhalla" in name:
                return 1
            return 0

        fx.add.side_effect = fx_add_side_effect

        with patch("hermes_core.master_templates._apply_proq3_eq",
                   return_value={}):
            result = _master_hewitt(bridge, tracks, fx, send, 0, "pop", None)

        assert "plate_1_mono" in result["plates"]


# ════════════════════════════════════════════════════════════════
# _master_serban — Serban Ghenea
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterSerban:
    """验证 Serban 模板：5 buses + sidechain compression。"""

    def _mock_deps(self):
        bridge = MagicMock()
        tracks = MagicMock()
        tracks.create.side_effect = lambda name: len(tracks.create.call_args_list)
        fx = MagicMock()
        fx.add.return_value = 0
        fx.set_param.return_value = True
        send = MagicMock()
        send.create.return_value = {"index": 0}
        return bridge, tracks, fx, send

    def test_creates_5_buses(self):
        """应创建 5 条总线（plate/hall/room/slap/rhythm）。"""
        from hermes_core.master_templates import _master_serban
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._apply_return_eq",
                   return_value=None):
            result = _master_serban(bridge, tracks, fx, send, 0, "pop", None)

        assert tracks.create.call_count == 5
        assert "plate" in result["buses"]
        assert "hall" in result["buses"]
        assert "room" in result["buses"]
        assert "slap" in result["buses"]
        assert "rhythm" in result["buses"]

    def test_each_bus_has_sidechain_fx(self):
        """每条总线应有 sidechain Pro-C 2。"""
        from hermes_core.master_templates import _master_serban
        bridge, tracks, fx, send = self._mock_deps()
        # FX 添加：EQ + 空间插件 + sidechain = 3 次 per bus
        fx.add.return_value = 0

        with patch("hermes_core.master_templates._apply_return_eq",
                   return_value=None):
            result = _master_serban(bridge, tracks, fx, send, 0, "pop", None)

        for bus_key, bus_info in result["buses"].items():
            assert "sidechain_fx" in bus_info


# ════════════════════════════════════════════════════════════════
# _master_townsend — Devin Townsend
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMasterTownsend:
    """验证 Townsend 模板：L/R 不对称延迟 + glue verb。"""

    def _mock_deps(self):
        bridge = MagicMock()
        tracks = MagicMock()
        tracks.create.side_effect = lambda name: len(tracks.create.call_args_list)
        fx = MagicMock()
        fx.add.return_value = 0
        fx.set_param.return_value = True
        send = MagicMock()
        send.create.return_value = {"index": 0}
        send.set_pan.return_value = True
        return bridge, tracks, fx, send

    def test_creates_3_tracks(self):
        """应创建 3 条轨道：L Delay + R Delay + Glue Verb。"""
        from hermes_core.master_templates import _master_townsend
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._townsend_hp_lp_eq",
                   return_value=None):
            result = _master_townsend(
                bridge, tracks, fx, send, 0, "pop", None,
            )

        assert tracks.create.call_count == 3
        assert "left_delay" in result
        assert "right_delay" in result
        assert "glue_reverb" in result

    def test_left_delay_pan_hard_left(self):
        """左延迟应硬声像到左（pan=-1.0）。"""
        from hermes_core.master_templates import _master_townsend
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._townsend_hp_lp_eq",
                   return_value=None):
            result = _master_townsend(
                bridge, tracks, fx, send, 0, "pop", None,
            )

        assert result["left_delay"]["pan"] == -1.0

    def test_right_delay_pan_hard_right(self):
        """右延迟应硬声像到右（pan=1.0）。"""
        from hermes_core.master_templates import _master_townsend
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._townsend_hp_lp_eq",
                   return_value=None):
            result = _master_townsend(
                bridge, tracks, fx, send, 0, "pop", None,
            )

        assert result["right_delay"]["pan"] == 1.0

    def test_glue_verb_has_post_eq(self):
        """粘合混响应有 post EQ 配置。"""
        from hermes_core.master_templates import _master_townsend
        bridge, tracks, fx, send = self._mock_deps()

        with patch("hermes_core.master_templates._townsend_hp_lp_eq",
                   return_value=None):
            result = _master_townsend(
                bridge, tracks, fx, send, 0, "pop", None,
            )

        assert result["glue_reverb"]["post_eq"] == {"hpf": 400, "lpf": 3000}
