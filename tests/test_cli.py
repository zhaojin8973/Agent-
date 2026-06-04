"""CLI 单元测试 — 参数解析、BPM提取、命令路由。"""
import argparse, json, sys
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open
import pytest
from hermes_core.cli import (
    _resolve_bpm, _build_parser,
    _build_vocal_mix_parser, _build_check_parser, _build_batch_parser,
    _build_calibrate_parser, _build_adjust_parser,
    cmd_vocal_mix, cmd_check, cmd_calibrate, cmd_adjust, cmd_batch,
    cmd_config, cmd_preflight, cmd_project, main,
)

# ═══════════════════════════ BPM ═══════════════════════════

class TestResolveBpm:
    def test_explicit(self):
        assert _resolve_bpm(argparse.Namespace(bpm=120.5, midi=None)) == 120.5
    def test_midi(self):
        a = argparse.Namespace(bpm=None, midi="/t.mid")
        with patch("hermes_core.midi_tempo.read_midi_tempo", return_value=100.0):
            assert _resolve_bpm(a) == 100.0
    def test_midi_error(self):
        a = argparse.Namespace(bpm=None, midi="/b.mid")
        from hermes_core.midi_tempo import MidiTempoError
        with patch("hermes_core.midi_tempo.read_midi_tempo",
                   side_effect=MidiTempoError("x")):
            assert _resolve_bpm(a) is None
    def test_neither(self):
        assert _resolve_bpm(argparse.Namespace(bpm=None, midi=None)) is None

# ═══════════════════════════ 解析器 ═══════════════════════════

class TestParserBuilding:
    def test_all_subcommands(self):
        p = _build_parser()
        c = getattr(next((a for a in p._actions if a.dest=="command"),None),"choices",{})
        for cmd in ["vocal-mix","check","batch","calibrate","adjust","project","config","preflight"]:
            assert cmd in c

    def test_vocal_mix_minimal(self):
        p=argparse.ArgumentParser();_build_vocal_mix_parser(p.add_subparsers(dest="c"))
        a=p.parse_args(["vocal-mix","--vocal","v.wav","--backing","b.wav"])
        assert a.genre=="pop"

    def test_vocal_mix_full(self):
        p=argparse.ArgumentParser();_build_vocal_mix_parser(p.add_subparsers(dest="c"))
        a=p.parse_args(["vocal-mix","--vocal","v.wav","--backing","b1.wav","b2.wav",
            "--genre","rock","--target-lufs","-12","--output","./o",
            "--profile","p.yaml","--tolerance","0.5","--watchdog","--bpm","140",
            "--midi","t.mid"])
        assert a.backing==["b1.wav","b2.wav"] and a.watchdog and a.midi is not None

    def test_check_requires_profile(self):
        p=argparse.ArgumentParser();_build_check_parser(p.add_subparsers(dest="c"))
        with pytest.raises(SystemExit):p.parse_args(["check"])

    def test_batch_requires_input(self):
        p=argparse.ArgumentParser();_build_batch_parser(p.add_subparsers(dest="c"))
        with pytest.raises(SystemExit):p.parse_args(["batch"])

    def test_calibrate_requires_args(self):
        p=argparse.ArgumentParser();_build_calibrate_parser(p.add_subparsers(dest="c"))
        with pytest.raises(SystemExit):p.parse_args(["calibrate"])

    def test_adjust_requires_project(self):
        p=argparse.ArgumentParser();_build_adjust_parser(p.add_subparsers(dest="c"))
        with pytest.raises(SystemExit):p.parse_args(["adjust"])

# ═══════════════════════════ 辅助函数 ═══════════════════════════

def _mock_engine_ctx():
    """创建成功路径的 mock MixingEngine 上下文管理器。"""
    me = MagicMock()
    me.preflight_plugins.return_value = []
    me.create_project.return_value = {"path": "/tmp/p.rpp"}
    me.prepare_stems.return_value = {"stems": []}
    me.post_fx_balance.return_value = {}
    me.apply_bus_compressor.return_value = {"peak_db":-3,"thresh_db":-20,
        "attack_ms":10,"makeup_db":3,"gr_target":4,"error":None}
    me.finalize_master.return_value = {"passed":True,
        "output_path":"/tmp/final.wav","achieved_lufs":-12.0}
    me.render_mix.return_value = {"output_path":"/tmp/r.wav","signal_check":{}}
    me.render_preview.return_value = {"output_path":"/tmp/pv.wav",
        "estimated_lufs":-13.0}
    me.close_project.return_value = {"saved":True}
    me.calibrate_compressor.return_value = [
        (0.0, -24.0), (0.5, -18.0), (1.0, -12.0),
    ]
    me._vocal_chain_nodes = []
    me._backing_chain_nodes = []
    me._reverb_send_node = None
    me.get_project_info.return_value = {"name":"test","path":"/tmp",
        "track_count":3}
    me._bridge = MagicMock()
    me._bridge.connect.return_value = True
    return me

def _mock_engine_patch():
    """返回 (patch, me) 用于 MixingEngine 上下文管理器模拟。"""
    mec = patch("hermes_core.MixingEngine")
    me = _mock_engine_ctx()
    return mec, me

# ═══════════════════════════ cmd_vocal_mix ═══════════════════════════

class TestCmdVocalMix:
    def test_success(self):
        with patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.profiles.get_default_vocal_chain", return_value=[]):
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(vocal="v.wav",backing=["b.wav"],genre="pop",
                target_lufs=None,output="/o",profile=None,tolerance=0.3,
                watchdog=False,bpm=None,midi=None)
            assert cmd_vocal_mix(a) == 0

    def test_missing_plugins(self):
        with patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.profiles.get_default_vocal_chain", return_value=[]):
            me = _mock_engine_ctx(); me.preflight_plugins.return_value = ["X"]
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(vocal="v.wav",backing=["b.wav"],genre="pop",
                target_lufs=None,output="/o",profile=None,tolerance=0.3,
                watchdog=False,bpm=None,midi=None)
            assert cmd_vocal_mix(a) == 1

    def test_with_profile(self):
        """使用 --profile 参数时应从 YAML 加载 MixingProfile。"""
        with patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.profiles.MixingProfile") as mp:
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            mp.from_yaml.return_value = MagicMock(all_fx_names=lambda: ["EQ","Comp"])
            a = argparse.Namespace(vocal="v.wav",backing=["b.wav"],genre="pop",
                target_lufs=None,output="/o",profile="p.yaml",tolerance=0.3,
                watchdog=False,bpm=None,midi=None)
            assert cmd_vocal_mix(a) == 0

    def test_bus_compressor_error(self):
        """总线压缩器错误时显示警告但仍返回 0。"""
        with patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.profiles.get_default_vocal_chain", return_value=[]):
            me = _mock_engine_ctx()
            me.apply_bus_compressor.return_value = {"peak_db":-3,
                "thresh_db":-20,"attack_ms":10,"makeup_db":3,
                "gr_target":4,"error":"threshold too low"}
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(vocal="v.wav",backing=["b.wav"],genre="pop",
                target_lufs=None,output="/o",profile=None,tolerance=0.3,
                watchdog=False,bpm=None,midi=None)
            assert cmd_vocal_mix(a) == 0

# ═══════════════════════════ cmd_check ═══════════════════════════

class TestCmdCheck:
    def test_all_found(self):
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec:
            mp.from_yaml.return_value = MagicMock(all_fx_names=lambda: ["R"])
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            assert cmd_check(argparse.Namespace(profile="/t.yaml")) == 0

    def test_missing_plugins(self):
        """有缺失插件时应返回 1。"""
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec:
            mp.from_yaml.return_value = MagicMock(all_fx_names=lambda: ["A","B"])
            me = _mock_engine_ctx()
            me.preflight_plugins.return_value = ["B"]
            mec.return_value.__enter__.return_value = me
            assert cmd_check(argparse.Namespace(profile="/t.yaml")) == 1

# ═══════════════════════════ cmd_calibrate ═══════════════════════════

class TestCmdCalibrate:
    def test_success_with_output(self):
        """校准成功，写入 JSON 输出文件。"""
        with patch("hermes_core.MixingEngine") as mec, \
             patch("builtins.open", mock_open()) as mopen:
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(plugin="FabFilter Pro-C 2",
                param="Threshold", lo=-36.0, hi=0.0, steps=10,
                signal=None, output="/tmp/cal.json", watchdog=False)
            assert cmd_calibrate(a) == 0
            mopen.assert_called_once_with("/tmp/cal.json", "w")

    def test_success_print_only(self):
        """无 output 时只打印表格，不写文件。"""
        with patch("hermes_core.MixingEngine") as mec:
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(plugin="FabFilter Pro-C 2",
                param="Threshold", lo=-36.0, hi=0.0, steps=10,
                signal=None, output=None, watchdog=False)
            assert cmd_calibrate(a) == 0

# ═══════════════════════════ cmd_adjust ═══════════════════════════

class TestCmdAdjust:
    def test_project_not_found(self):
        """项目目录不存在时返回 1。"""
        with patch("os.path.isdir", return_value=False):
            a = argparse.Namespace(project="/no/such/dir", genre="pop",
                comp_ratio=4.0, eq_presence=None, threshold=None,
                reverb_level=None, target_lufs=None, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 1

    def test_no_changes(self):
        """未指定任何参数更改时返回 1。"""
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec:
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=None, eq_presence=None, threshold=None,
                reverb_level=None, target_lufs=None, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 1

    def test_success_finalize(self):
        """成功调整 + 最终母带处理。"""
        node = MagicMock(fx_type="comp", params={})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me._vocal_chain_nodes = [node]
            me._backing_chain_nodes = []
            me._reverb_send_node = None
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=4.0, eq_presence=None, threshold=None,
                reverb_level=None, target_lufs=None, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 0
            me.finalize_master.assert_called_once()

    def test_success_preview(self):
        """成功调整 + 预览渲染。"""
        node = MagicMock(fx_type="eq", params={})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me._vocal_chain_nodes = [node]
            me._backing_chain_nodes = []
            me._reverb_send_node = None
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=None, eq_presence=2.0, threshold=None,
                reverb_level=None, target_lufs=None, preview=True,
                watchdog=False)
            assert cmd_adjust(a) == 0
            me.render_preview.assert_called_once()

    def test_preview_fails(self):
        """预览渲染失败时返回 1。"""
        node = MagicMock(fx_type="comp", params={})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me.render_preview.return_value = {"error": "no output"}
            me._vocal_chain_nodes = [node]
            me._backing_chain_nodes = []
            me._reverb_send_node = None
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=4.0, eq_presence=None, threshold=None,
                reverb_level=None, target_lufs=None, preview=True,
                watchdog=False)
            assert cmd_adjust(a) == 1

    def test_finalize_fails(self):
        """母带失败时返回 1。"""
        node = MagicMock(fx_type="comp", params={})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me.finalize_master.return_value = {"passed": False,
                "error": "LUFS too low"}
            me._vocal_chain_nodes = [node]
            me._backing_chain_nodes = []
            me._reverb_send_node = None
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=4.0, eq_presence=None, threshold=None,
                reverb_level=None, target_lufs=None, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 1

    def test_reverb_level_change(self):
        """reverb_level 参数通过 _reverb_send_node 应用。"""
        rv_node = MagicMock(fx_type="send", params={"level_db": -8})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me._vocal_chain_nodes = []
            me._backing_chain_nodes = []
            me._reverb_send_node = rv_node
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="pop",
                comp_ratio=None, eq_presence=None, threshold=None,
                reverb_level=-6.0, target_lufs=None, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 0

    def test_multi_param_change(self):
        """同时修改多个参数时的脏级联。"""
        cnode = MagicMock(fx_type="comp", params={})
        enode = MagicMock(fx_type="eq", params={})
        with patch("os.path.isdir", return_value=True), \
             patch("hermes_core.MixingEngine") as mec, \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            me = _mock_engine_ctx()
            me._vocal_chain_nodes = [cnode, enode]
            me._backing_chain_nodes = []
            me._reverb_send_node = None
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(project="/tmp/proj", genre="rock",
                comp_ratio=6.0, eq_presence=3.0, threshold=-24.0,
                reverb_level=None, target_lufs=-11.0, preview=False,
                watchdog=False)
            assert cmd_adjust(a) == 0
            assert me.update_node_param.call_count >= 3

# ═══════════════════════════ cmd_batch ═══════════════════════════

class TestCmdBatch:
    def test_input_dir_not_found(self):
        """输入目录不存在时返回 1。"""
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("pathlib.Path.is_dir", return_value=False):
            mp.from_yaml.return_value = MagicMock()
            a = argparse.Namespace(input_dir="/no/dir", profile="p.yaml",
                output_dir="./out", target_lufs=None, bpm=None, midi=None)
            assert cmd_batch(a) == 1

    def test_no_song_folders(self):
        """输入目录为空时返回 1。"""
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.iterdir", return_value=[]):
            mp.from_yaml.return_value = MagicMock()
            a = argparse.Namespace(input_dir="/empty", profile="p.yaml",
                output_dir="./out", target_lufs=None, bpm=None, midi=None)
            assert cmd_batch(a) == 1

    def test_success_all_passed(self):
        """全部歌曲处理成功时返回 0。"""
        mock_dir = MagicMock()
        mock_dir.name = "song1"
        mock_dir.is_dir.return_value = True
        mock_wav = MagicMock()
        mock_wav.__str__ = lambda self: "/tmp/song1/vocal.wav"
        mock_dir.glob.return_value = [mock_wav]
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec, \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.iterdir", return_value=[mock_dir]), \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            mp.from_yaml.return_value = MagicMock()
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            a = argparse.Namespace(input_dir="/songs", profile="p.yaml",
                output_dir="./out", target_lufs=None, bpm=None, midi=None)
            assert cmd_batch(a) == 0

    def test_partial_failure(self):
        """部分歌曲处理失败时返回 1。"""
        mock_dir1 = MagicMock()
        mock_dir1.name = "song1"
        mock_dir1.is_dir.return_value = True
        mock_wav1 = MagicMock()
        mock_wav1.__str__ = lambda self: "/tmp/song1/vocal.wav"
        mock_dir1.glob.return_value = [mock_wav1]
        mock_dir2 = MagicMock()
        mock_dir2.name = "song2"
        mock_dir2.is_dir.return_value = True
        mock_wav2 = MagicMock()
        mock_wav2.__str__ = lambda self: "/tmp/song2/vocal.wav"
        mock_dir2.glob.return_value = [mock_wav2]
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec, \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.iterdir", return_value=[mock_dir1, mock_dir2]), \
             patch("hermes_core.mastering._get_genre_target_lufs", return_value=-12.0):
            mp.from_yaml.return_value = MagicMock()
            me_ok = _mock_engine_ctx()
            me_fail = _mock_engine_ctx()
            me_fail.finalize_master.return_value = {"passed": False}
            mec.return_value.__enter__.side_effect = [me_ok, me_fail]
            a = argparse.Namespace(input_dir="/songs", profile="p.yaml",
                output_dir="./out", target_lufs=None, bpm=None, midi=None)
            assert cmd_batch(a) == 1

    def test_skip_no_wavs(self):
        """跳过无 WAV 文件的歌曲目录。"""
        mock_dir = MagicMock()
        mock_dir.name = "empty_song"
        mock_dir.is_dir.return_value = True
        mock_dir.glob.return_value = []
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec, \
             patch("pathlib.Path.is_dir", return_value=True), \
             patch("pathlib.Path.iterdir", return_value=[mock_dir]):
            mp.from_yaml.return_value = MagicMock()
            a = argparse.Namespace(input_dir="/songs", profile="p.yaml",
                output_dir="./out", target_lufs=None, bpm=None, midi=None)
            assert cmd_batch(a) == 1  # 无成功处理

# ═══════════════════════════ cmd_config ═══════════════════════════

class TestCmdConfig:
    def test_show(self):
        """config show 显示当前配置。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            hc.load.return_value.show.return_value = "project_root: /tmp"
            a = argparse.Namespace(config_action="show")
            assert cmd_config(a) == 0

    def test_set_bool_true(self):
        """config set 布尔值 true 转换。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            a = argparse.Namespace(config_action="set",
                key="auto_save_prompt", value="true")
            assert cmd_config(a) == 0
            hc.load.return_value.set.assert_called_with("auto_save_prompt", True)

    def test_set_bool_false(self):
        """config set 布尔值 false 转换。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            a = argparse.Namespace(config_action="set",
                key="auto_save_prompt", value="false")
            assert cmd_config(a) == 0
            hc.load.return_value.set.assert_called_with("auto_save_prompt", False)

    def test_set_int(self):
        """config set 整数转换。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            a = argparse.Namespace(config_action="set",
                key="default_sample_rate", value="48000")
            assert cmd_config(a) == 0
            hc.load.return_value.set.assert_called_with("default_sample_rate", 48000)

    def test_set_string(self):
        """config set 字符串值。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            a = argparse.Namespace(config_action="set",
                key="project_root", value="/my/projects")
            assert cmd_config(a) == 0
            hc.load.return_value.set.assert_called_with("project_root", "/my/projects")

    def test_set_unknown_key(self):
        """config set 未知键返回 1。"""
        with patch("hermes_core.config.HermesConfig") as hc:
            hc.load.return_value.set.side_effect = KeyError("bad_key")
            a = argparse.Namespace(config_action="set",
                key="bad_key", value="x")
            assert cmd_config(a) == 1

    def test_unknown_action(self):
        """未知 config 操作返回 1。"""
        a = argparse.Namespace(config_action="delete")
        assert cmd_config(a) == 1

# ═══════════════════════════ cmd_preflight ═══════════════════════════

class TestCmdPreflight:
    def test_reaper_offline(self):
        """REAPER 未运行时返回 1。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = MagicMock()
            me._bridge.connect.return_value = False
            mec.return_value = me
            a = argparse.Namespace(plugin=None)
            assert cmd_preflight(a) == 1

    def test_all_available(self):
        """全部插件可用时返回 0。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = MagicMock()
            me._bridge.connect.return_value = True
            me.preflight_plugins.return_value = {
                "FabFilter Pro-Q 3": True, "FabFilter Pro-C 2": True,
            }
            mec.return_value = me
            a = argparse.Namespace(plugin=None)
            assert cmd_preflight(a) == 0

    def test_some_missing(self):
        """部分插件缺失时返回 1。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = MagicMock()
            me._bridge.connect.return_value = True
            me.preflight_plugins.return_value = {
                "FabFilter Pro-Q 3": True, "FabFilter Pro-C 2": False,
            }
            mec.return_value = me
            a = argparse.Namespace(plugin=None)
            assert cmd_preflight(a) == 1

    def test_specific_plugins(self):
        """检查指定的插件列表。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = MagicMock()
            me._bridge.connect.return_value = True
            me.preflight_plugins.return_value = {"EchoBoy": True}
            mec.return_value = me
            a = argparse.Namespace(plugin=["EchoBoy"])
            assert cmd_preflight(a) == 0
            me.preflight_plugins.assert_called_with(required=["EchoBoy"])

# ═══════════════════════════ cmd_project ═══════════════════════════

class TestCmdProject:
    def test_list_empty(self):
        """project list 无工程时正常返回 0。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.projects = False  # 空字典视为 false
            idx.filter_by.return_value = []
            idx.list_all.return_value = []
            pi.load.return_value = idx
            a = argparse.Namespace(project_action="list",
                genre=None, stage=None, category=None)
            assert cmd_project(a) == 0

    def test_list_with_filter(self):
        """project list 按流派筛选。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.projects = {"song1": {"name":"song1"}}
            idx.filter_by.return_value = [
                ("song1", {"name":"song1","genre":"pop","stage":"mastered",
                 "category":"demo","last_modified":"2024-01-01"}),
            ]
            idx.list_all.return_value = idx.filter_by.return_value
            pi.load.return_value = idx
            a = argparse.Namespace(project_action="list",
                genre="pop", stage=None, category=None)
            assert cmd_project(a) == 0

    def test_scan(self):
        """project scan 正常返回 0。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.scan.return_value = 5
            pi.return_value = idx
            a = argparse.Namespace(project_action="scan")
            assert cmd_project(a) == 0

    def test_info_not_found(self):
        """project info 找不到工程时返回 1。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.find.return_value = []
            pi.load.return_value = idx
            a = argparse.Namespace(project_action="info", name="unknown")
            assert cmd_project(a) == 1

    def test_info_found(self):
        """project info 找到工程时返回 0。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi, \
             patch("hermes_core.project_meta.ProjectMeta") as pm:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.find.return_value = [("song1", {"name":"song1"})]
            pi.load.return_value = idx
            meta = MagicMock()
            meta.summary.return_value = "Song1: pop, mastered"
            pm.load.return_value = meta
            a = argparse.Namespace(project_action="info", name="song1")
            assert cmd_project(a) == 0

    def test_status_by_name_not_found(self):
        """project status 按名称找不到工程时返回 1。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.project_meta.ProjectIndex") as pi:
            hc.load.return_value.project_root_expanded = "/tmp"
            idx = MagicMock()
            idx.find.return_value = []
            pi.load.return_value = idx
            a = argparse.Namespace(project_action="status", name="unknown")
            assert cmd_project(a) == 1

    def test_status_current_reaper_offline(self):
        """project status 无名称时 REAPER 离线返回 1。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.engine.MixingEngine") as mec:
            hc.load.return_value.project_root_expanded = "/tmp"
            me = MagicMock()
            me._bridge.connect.return_value = False
            mec.return_value = me
            a = argparse.Namespace(project_action="status", name="")
            assert cmd_project(a) == 1

    def test_status_current_reaper_online(self):
        """project status 无名称时 REAPER 在线返回 0。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.engine.MixingEngine") as mec:
            hc.load.return_value.project_root_expanded = "/tmp"
            me = _mock_engine_ctx()
            me._meta = None
            mec.return_value = me
            a = argparse.Namespace(project_action="status", name="")
            assert cmd_project(a) == 0

    def test_close_reaper_offline(self):
        """project close 时 REAPER 离线返回 1。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = MagicMock()
            me._bridge.connect.return_value = False
            mec.return_value = me
            a = argparse.Namespace(project_action="close")
            assert cmd_project(a) == 1

    def test_close_success(self):
        """project close 成功返回 0。"""
        with patch("hermes_core.engine.MixingEngine") as mec:
            me = _mock_engine_ctx()
            mec.return_value = me
            a = argparse.Namespace(project_action="close")
            assert cmd_project(a) == 0

    def test_create_reaper_offline(self):
        """project create 时 REAPER 离线返回 1。"""
        with patch("hermes_core.config.HermesConfig") as hc, \
             patch("hermes_core.engine.MixingEngine") as mec:
            hc.load.return_value.project_root_expanded = "/tmp"
            me = MagicMock()
            me._bridge.connect.return_value = False
            mec.return_value = me
            a = argparse.Namespace(project_action="create", name="new_song",
                category="", producer="", genre="pop")
            assert cmd_project(a) == 1

    def test_unknown_action(self):
        """未知 project 操作返回 1。"""
        a = argparse.Namespace(project_action="delete")
        assert cmd_project(a) == 1

# ═══════════════════════════ main ═══════════════════════════

class TestMain:
    def test_routes_vocal_mix(self):
        """main 正确路由到 cmd_vocal_mix。"""
        with patch("hermes_core.cli.cmd_vocal_mix") as cvm, \
             patch("hermes_core.cli._build_parser") as bp:
            cvm.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="vocal-mix")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_check(self):
        """main 正确路由到 cmd_check。"""
        with patch("hermes_core.cli.cmd_check") as cc, \
             patch("hermes_core.cli._build_parser") as bp:
            cc.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="check")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_calibrate(self):
        """main 正确路由到 cmd_calibrate。"""
        with patch("hermes_core.cli.cmd_calibrate") as cc, \
             patch("hermes_core.cli._build_parser") as bp:
            cc.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="calibrate")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_adjust(self):
        """main 正确路由到 cmd_adjust。"""
        with patch("hermes_core.cli.cmd_adjust") as ca, \
             patch("hermes_core.cli._build_parser") as bp:
            ca.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="adjust")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_batch(self):
        """main 正确路由到 cmd_batch。"""
        with patch("hermes_core.cli.cmd_batch") as cb, \
             patch("hermes_core.cli._build_parser") as bp:
            cb.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="batch")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_project(self):
        """main 正确路由到 cmd_project。"""
        with patch("hermes_core.cli.cmd_project") as cp, \
             patch("hermes_core.cli._build_parser") as bp:
            cp.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="project")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_config(self):
        """main 正确路由到 cmd_config。"""
        with patch("hermes_core.cli.cmd_config") as cc, \
             patch("hermes_core.cli._build_parser") as bp:
            cc.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="config")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_routes_preflight(self):
        """main 正确路由到 cmd_preflight。"""
        with patch("hermes_core.cli.cmd_preflight") as cp, \
             patch("hermes_core.cli._build_parser") as bp:
            cp.return_value = 0
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="preflight")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 0

    def test_unknown_command(self):
        """未知命令时打印帮助并 exit(1)。"""
        with patch("hermes_core.cli._build_parser") as bp:
            parser = MagicMock()
            parser.parse_args.return_value = argparse.Namespace(command="unknown")
            bp.return_value = parser
            with pytest.raises(SystemExit) as excinfo:
                main([])
            assert excinfo.value.code == 1
