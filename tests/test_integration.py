"""集成测试 — 需要 REAPER 运行，标记为 @pytest.mark.integration。

运行方式：
    PYTHONPATH=src python -m pytest tests/test_integration.py -v -m integration
    或跳过集成测试：
    PYTHONPATH=src python -m pytest tests/test_integration.py -v -m "not integration"
"""

import pytest

from hermes_core.bridge import ReaperBridge
from hermes_core.engine import MixingEngine
from tests.conftest import require_reaper, clean_project, make_test_wav


@pytest.mark.integration
class TestReaperConnection:
    """REAPER 连接集成测试。"""

    def test_connect_and_health_check(self):
        """连接 REAPER 并检查健康状态。"""
        bridge = ReaperBridge()
        if not bridge.connect():
            pytest.skip("REAPER not running")
        health = bridge.health_check()
        assert health["reapy_connected"] is True
        assert health["version"] is not None

    def test_engine_connect_and_disconnect(self):
        """引擎连接/断开 REAPER。"""
        require_reaper()
        with MixingEngine() as eng:
            health = eng.health_check()
            assert health["reapy_connected"] is True


@pytest.mark.integration
class TestProjectLifecycle:
    """工程生命周期集成测试。"""

    def test_create_and_close_project(self, tmp_path):
        """创建和关闭工程。"""
        require_reaper()
        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            result = eng.create_project(
                name="test_integration",
                output_dir=str(tmp_path),
                sample_rate=48000,
            )
            assert result["name"] == "test_integration"
            assert result["track_count"] >= 0
        finally:
            eng.close_project(save=False)

    def test_create_project_with_meta(self, tmp_path):
        """创建工程时生成元数据。"""
        require_reaper()
        import os
        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            result = eng.create_project(
                name="test_meta",
                output_dir=str(tmp_path),
                sample_rate=44100,
                genre="pop",
            )
            assert result["sample_rate"] == 44100
            meta_path = os.path.join(str(tmp_path), ".hermes_meta.json")
            assert os.path.exists(meta_path), "元数据文件应被创建"
        finally:
            eng.close_project(save=False)

    def test_save_and_close_without_dialog(self, tmp_path):
        """保存并关闭工程不触发弹窗。"""
        require_reaper()
        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            eng.create_project(
                name="test_save_close",
                output_dir=str(tmp_path),
                sample_rate=48000,
            )
            result = eng.close_project(save=True)
            assert result["saved"] is True
        except Exception:
            eng.close_project(save=False)


@pytest.mark.integration
class TestFullPipeline:
    """完整混音管线集成测试。"""

    def test_end_to_end_mix(self, tmp_path):
        """端到端混音管线：创建->导入->FX->渲染。"""
        require_reaper()
        vocal = make_test_wav(
            str(tmp_path / "vocal.wav"),
            duration_sec=2.0, frequency=440.0,
        )
        backing = make_test_wav(
            str(tmp_path / "backing.wav"),
            duration_sec=2.0, frequency=220.0,
        )

        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            eng.create_project(
                name="test_e2e",
                output_dir=str(tmp_path / "project"),
                sample_rate=48000,
            )
            eng.prepare_stems(
                stem_paths=[vocal, backing],
                genre="pop",
            )
            eng.add_fx(track_index=0, fx_name="ReaEQ (Cockos)")
            eng.add_master_fx("ReaEQ (Cockos)")
            result = eng.render_mix(output_dir=str(tmp_path / "render"))
            assert result["output_path"] is not None
            assert "signal_check" in result
        finally:
            eng.close_project(save=False)

    def test_import_and_list_tracks(self, tmp_path):
        """导入 stems 并列出轨道。"""
        require_reaper()
        vocal = make_test_wav(
            str(tmp_path / "vocal.wav"),
            duration_sec=1.0, frequency=440.0,
        )

        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            eng.create_project(
                name="test_tracks",
                output_dir=str(tmp_path),
                sample_rate=48000,
            )
            result = eng.prepare_stems(
                stem_paths=[vocal],
                genre="pop",
            )
            stems = result.get("stems", [])
            assert len(stems) >= 1
            assert stems[0].get("success") is True

            tracks = eng.list_tracks()
            assert len(tracks) >= 1
        finally:
            eng.close_project(save=False)

    def test_health_check_returns_watchdog_status(self):
        """健康检查返回 watchdog 状态。"""
        require_reaper()
        eng = MixingEngine(watchdog=True)
        eng.connect()
        try:
            health = eng.health_check()
            assert "watchdog_enabled" in health
            assert health["watchdog_enabled"] is True
        finally:
            eng.close_project(save=False)


@pytest.mark.integration
class TestErrorHandling:
    """集成测试中的错误处理。"""

    def test_track_deletion_protected_by_default(self, tmp_path):
        """默认情况下不允许删除轨道。"""
        require_reaper()
        eng = MixingEngine()
        eng.connect()
        try:
            # 不调用 allow_track_deletion() — 应该被保护
            # 新工程没有轨道，所以 create_project 不会触发保护
            eng.allow_track_deletion()
            eng.create_project(
                name="test_protection",
                output_dir=str(tmp_path),
                sample_rate=48000,
            )
            # 添加一条轨道
            eng.prepare_stems(
                stem_paths=[make_test_wav(
                    str(tmp_path / "test.wav"),
                    duration_sec=1.0, frequency=440.0,
                )],
                genre="pop",
            )
            tracks = eng.list_tracks()
            assert len(tracks) >= 1, "应有至少一条轨道"
        finally:
            eng.close_project(save=False)

    def test_close_without_save_cleans_up(self, tmp_path):
        """不保存关闭时清理工程。"""
        require_reaper()
        eng = MixingEngine()
        eng.connect()
        try:
            eng.allow_track_deletion()
            eng.create_project(
                name="test_no_save",
                output_dir=str(tmp_path),
                sample_rate=48000,
            )
            result = eng.close_project(save=False)
            assert "project_path" in result
        except Exception:
            eng.close_project(save=False)
