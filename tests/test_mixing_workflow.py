"""混音管线端到端集成测试。

仅保留验证实际混音逻辑的测试：
- prepare_stems / post_fx_balance（clip gain 计算 + 流派推子比例）
- finalize_master（响度优化收敛）
- 完整贴唱管线（端到端无 crash）
- 安全与运维功能（插件检测、进度回调、reset）

已删除的低价值测试：手动 add_fx / create_reverb_send 等 REAPER API
wrapper 测试。这些逻辑已被 test_engine.py 的单元测试覆盖，不需要在
REAPER 里重跑。

需要运行中的 REAPER 实例。
"""

import pytest

from hermes_core.engine import MixingEngine
from tests.conftest import require_reaper


# 贴唱混音测试音频文件
_VOCAL_FILE = "Hermes 测试/望归 贴唱/望归 Vocal（测试）.wav"
_BACKING_FILE = "Hermes 测试/望归 贴唱/望归 伴奏（测试）.wav"

# 第三方插件名（TrackFX_AddByName 子串匹配）
_EQ_PLUGIN = "FabFilter Pro-Q 3"
_COMP_PLUGIN = "RVox"
_REVERB_PLUGIN = "ValhallaVintageVerb"
_MASTER_LIMITER = "FabFilter Pro-L 2 (FabFilter)"


# ════════════════════════════════════════════════════════════════
# 贴唱混音管线测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestVocalMixing:
    """贴唱混音端到端测试 — 验证实际混音逻辑。"""

    def test_prepare_stems_clip_gain_and_fader_deferral(self):
        """prepare_stems 计算 clip gain 对齐 -18 dBFS，fader 延迟到 post_fx_balance。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )

        result = eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        stems = result["stems"]
        vocal = stems[0]
        backing = stems[1]

        assert vocal["role"] == "vocal"
        assert backing["role"] == "backing"
        assert vocal["raw_lufs"] is not None, "未测量人声 LUFS"
        assert backing["raw_lufs"] is not None, "未测量伴奏 LUFS"

        # Clip gain 将每轨对齐到 -18 dBFS RMS 参考
        for s in stems:
            assert s["clip_gain_db"] != 0.0, (
                f"{s['role']} 应有非零 clip gain（当前 {s['clip_gain_db']}）"
            )

        # Fader 延迟到 post_fx_balance — prepare_stems 阶段应为 0
        for s in stems:
            assert s["fader_gain_db"] == 0.0, (
                f"{s['role']} 的 fader 在 post_fx_balance 前应为 0.0"
            )

    def test_post_fx_balance_genre_based_faders(self):
        """post_fx_balance 按流派比例降低伴奏推子，突出人声。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestPostFxBalance", output_dir="/tmp/hermes_balance_test",
            sample_rate=48000,
        )

        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )

        balance = eng.post_fx_balance(
            vocal_indices=[0], backing_indices=[1],
            genre="chinese_folk_bel_canto",
        )
        stems = balance["stems"]
        vocal = stems[0]
        backing = stems[1]

        # 民美流派：伴奏降 9-12 LU，伴奏推子应远低于人声推子
        assert abs(backing["fader_gain_db"]) > abs(vocal["fader_gain_db"]), (
            f"伴奏推子 ({backing['fader_gain_db']}) 应低于 "
            f"人声推子 ({vocal['fader_gain_db']})，民美流派突出人声"
        )

    def test_finalize_master_to_target_lufs(self, tmp_path):
        """finalize_master 响度优化：probe → search → final render 收敛。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        eng.add_fx(0, _EQ_PLUGIN)
        eng.add_fx(0, _COMP_PLUGIN)
        eng.create_reverb_send(src_track=0, reverb_fx=_REVERB_PLUGIN)

        result = eng.finalize_master(
            target_lufs=-12.0, tmp_dir=str(tmp_path),
        )
        assert result["pre_limiter_peak_db"] <= 0, (
            f"进限制器前峰值 {result['pre_limiter_peak_db']} — 混音已削波"
        )
        assert result["passed"] is True, f"finalize_master 未通过: {result}"
        assert result["converged"] is True
        assert result["probe_lufs"] is not None
        assert result["gain_db"] >= 0
        assert result["output_path"] is not None

        audit = eng.audit_mix(result["output_path"])
        assert audit["passed"] is True, f"母带输出审计未通过: {audit}"

    def test_full_vocal_mixing_session(self, tmp_path):
        """完整贴唱混音管线端到端 — 无 crash，输出通过审计。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        # 1. 创建工程 + 导入分轨
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        prep = eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        assert all(s["success"] for s in prep["stems"])

        # 2. 人声处理链
        assert eng.add_fx(0, _EQ_PLUGIN) >= 0
        assert eng.add_fx(0, _COMP_PLUGIN) >= 0

        # 3. 混响发送
        reverb = eng.create_reverb_send(
            src_track=0, reverb_fx=_REVERB_PLUGIN,
        )
        assert reverb["aux_index"] >= 0

        # 4. 母带前快照
        cp = eng.save_checkpoint(label="before_master")
        assert cp["checkpoint_path"] is not None

        # 5. 母带响度优化
        master = eng.finalize_master(
            target_lufs=-12.0, tmp_dir=str(tmp_path),
        )
        assert master["passed"] is True, f"finalize_master 失败: {master}"
        assert master["pre_limiter_peak_db"] <= 0
        assert master["converged"] is True
        assert master["probe_lufs"] is not None
        assert master["gain_db"] >= 0

        # 6. 审计
        audit = eng.audit_mix(master["output_path"])
        assert audit["passed"] is True, f"最终混音审计失败: {audit}"

        # 7. 健康检查
        health = eng.health_check()
        assert health["reapy_connected"] is True


# ════════════════════════════════════════════════════════════════
# 安全与运维功能测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestProductionGapsFeatures:
    """安全与运维功能集成测试。"""

    def test_preflight_plugins_detects_installed(self):
        """preflight_plugins 对已安装的内置插件返回空列表。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("PreflightTest", "/tmp/hermes_test")
        missing = eng.preflight_plugins(["ReaEQ", "ReaComp"])
        assert missing == [], f"内置 FX 应全部找到，缺失: {missing}"

    def test_preflight_plugins_detects_missing(self):
        """preflight_plugins 返回不存在的插件。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("PreflightTest", "/tmp/hermes_test")
        missing = eng.preflight_plugins(["DefinitelyNotARealPlugin_XYZ_123"])
        assert len(missing) == 1
        assert "DefinitelyNotARealPlugin_XYZ_123" in missing

    def test_on_progress_callback_fires(self):
        """finalize_master 过程中进度回调正常触发。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("ProgressTest", "/tmp/hermes_test", sample_rate=48000)

        # 用合成音频代替真实文件（避免依赖外部文件）
        from tests.conftest import make_test_wav
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            wav = make_test_wav(f"{td}/test.wav", duration_sec=2.0)
            eng.import_stems([wav])

            stages = []
            def track(stage, pct):
                stages.append(stage)

            result = eng.finalize_master(target_lufs=-12.0, on_progress=track)
            assert "setup" in stages, f"缺少 'setup' 阶段，got: {stages}"
            assert "probe_render" in stages
            assert "search" in stages
            assert "final_render" in stages
            assert "verify" in stages

    def test_reset_allows_reuse(self):
        """reset() 清除防护标记，允许重复调用 prepare_stems。"""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("ResetTest", "/tmp/hermes_test", sample_rate=48000)
        eng.import_stems([_VOCAL_FILE])

        # 第一次调用
        result1 = eng.prepare_stems(
            [_VOCAL_FILE], genre="pop",
            vocal_indices=[0],
        )
        assert "stems" in result1

        # 第二次应失败（防护标记）...
        # reset 后可再次调用
        eng.reset()
        eng.import_stems([_VOCAL_FILE])
        result2 = eng.prepare_stems(
            [_VOCAL_FILE], genre="pop",
            vocal_indices=[0],
        )
        assert "stems" in result2
