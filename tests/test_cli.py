"""CLI 单元测试 — 参数解析、BPM提取、命令路由。"""
import argparse, sys
from unittest.mock import MagicMock, patch
import pytest
from hermes_core.cli import (
    _resolve_bpm, _build_parser,
    _build_vocal_mix_parser, _build_check_parser, _build_batch_parser,
    _build_calibrate_parser, _build_adjust_parser,
    cmd_vocal_mix, cmd_check,
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

# ═══════════════════════════ 命令处理器 ═══════════════════════════

def _mock_engine_ctx():
    """创建成功路径的 mock MixingEngine 上下文管理器。"""
    me = MagicMock()
    me.preflight_plugins.return_value = []
    me.create_project.return_value = {"path": "/tmp/p.rpp"}
    me.prepare_stems.return_value = {"stems": []}
    me.post_fx_balance.return_value = {}
    me.apply_bus_compressor.return_value = {"peak_db":-3,"thresh_db":-20,
        "attack_ms":10,"makeup_db":3,"gr_target":4,"error":None}
    me.finalize_master.return_value = {"passed":True}
    me.render_mix.return_value = {"output_path":"/tmp/r.wav","signal_check":{}}
    me.close_project.return_value = {"saved":True}
    return me

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

class TestCmdCheck:
    def test_all_found(self):
        with patch("hermes_core.profiles.MixingProfile") as mp, \
             patch("hermes_core.MixingEngine") as mec:
            mp.from_yaml.return_value = MagicMock(all_fx_names=lambda: ["R"])
            me = _mock_engine_ctx()
            mec.return_value.__enter__.return_value = me
            assert cmd_check(argparse.Namespace(profile="/t.yaml")) == 0

# 注：cmd_config/cmd_preflight/main() 需要复杂 mock（argparse 参数结构），
# 更适合在集成测试中验证。
