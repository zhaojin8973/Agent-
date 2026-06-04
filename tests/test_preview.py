"""Tests for hermes_core.preview — PreviewRenderer unit tests with mocked bridge."""

import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.preview import PreviewRenderer, PreviewResult


@pytest.mark.unit
class TestPreviewResult:
    """PreviewResult dataclass 基本行为。"""

    def test_default_values(self):
        """默认值正确。"""
        r = PreviewResult(success=False)
        assert r.success is False
        assert r.preview_path is None
        assert r.before_path is None
        assert r.after_path is None
        assert r.format == "mp3"
        assert r.bitrate_kbps == 128
        assert r.duration_sec == 0.0
        assert r.error is None

    def test_success_result(self):
        """成功结果包含正确路径。"""
        r = PreviewResult(
            success=True,
            preview_path="/tmp/test.mp3",
            format="mp3",
            bitrate_kbps=128,
            duration_sec=15.0,
        )
        assert r.preview_path == "/tmp/test.mp3"
        assert r.duration_sec == 15.0

    def test_error_result(self):
        """失败结果包含错误信息。"""
        r = PreviewResult(
            success=False,
            error="渲染超时",
        )
        assert r.error == "渲染超时"


@pytest.mark.unit
class TestPreviewRendererInit:
    """PreviewRenderer 初始化。"""

    def test_stores_bridge(self):
        """bridge 被正确存储。"""
        bridge = MagicMock()
        renderer = PreviewRenderer(bridge)
        assert renderer._bridge is bridge

    def test_api_property(self):
        """api 属性委托给 bridge.api。"""
        bridge = MagicMock()
        bridge.api = MagicMock()
        renderer = PreviewRenderer(bridge)
        assert renderer.api is bridge.api


@pytest.mark.unit
class TestPreviewRendererHelpers:
    """内部辅助方法测试。"""

    def test_configure_mp3_render(self):
        """MP3 渲染配置调用正确的 API。"""
        bridge = MagicMock()
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        renderer = PreviewRenderer(bridge)

        renderer._configure_mp3_render("/tmp/out")

        # 验证 RENDER_FORMAT、RENDER_FILE、RENDER_PATTERN 被调用
        call_args_list = [
            call[0] for call in bridge.api.GetSetProjectInfo_String.call_args_list
        ]
        call_names = set()
        for args in call_args_list:
            if len(args) >= 2:
                call_names.add(args[1])
        assert "RENDER_FORMAT" in call_names
        assert "RENDER_FILE" in call_names
        assert "RENDER_PATTERN" in call_names

    def test_configure_wav_render(self):
        """WAV 渲染配置调用正确的 API。"""
        bridge = MagicMock()
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        renderer = PreviewRenderer(bridge)

        renderer._configure_wav_render("/tmp/out")

        bridge.api.GetSetProjectInfo_String.assert_any_call(
            0, "RENDER_FILE", "/tmp/out", True,
        )

    def test_set_time_selection(self):
        """时间选区设置调用正确的 API。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock()
        renderer = PreviewRenderer(bridge)

        renderer._set_time_selection(0.0, 15.0)

        bridge.api.GetSet_LoopTimeRange.assert_called_once_with(
            True, False, 0.0, 15.0, False,
        )

    def test_get_time_selection(self):
        """获取时间选区调用正确的 API。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 15.0, False),
        )
        renderer = PreviewRenderer(bridge)

        start, end = renderer._get_time_selection()

        assert start == 0.0
        assert end == 15.0

    def test_set_render_bounds_entire_project(self):
        """设置渲染范围为整个工程。"""
        bridge = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        renderer = PreviewRenderer(bridge)

        renderer._set_render_bounds("entire_project")

        bridge.api.GetSetProjectInfo.assert_called_once_with(
            0, "RENDER_BOUNDSFLAG", 1, True,
        )

    def test_set_render_bounds_time_selection(self):
        """设置渲染范围为时间选区。"""
        bridge = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        renderer = PreviewRenderer(bridge)

        renderer._set_render_bounds("time_selection")

        bridge.api.GetSetProjectInfo.assert_called_once_with(
            0, "RENDER_BOUNDSFLAG", 2, True,
        )


@pytest.mark.unit
class TestPreviewRendererTriggerRender:
    """_trigger_render 测试。"""

    def test_triggers_render_and_waits(self):
        """触发渲染并等待文件出现。"""
        bridge = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000):
            ok = renderer._trigger_render("/tmp/out/preview.mp3")

        assert ok is True
        bridge.focus_reaper.assert_called_once()
        bridge.api.Main_OnCommand.assert_called_once_with(42230, 0)

    def test_timeout_returns_false(self):
        """渲染超时返回 False。"""
        bridge = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        with patch("os.path.exists", return_value=False), \
             patch("time.sleep", return_value=None):
            ok = renderer._trigger_render("/tmp/out/preview.mp3", timeout=0.05)

        assert ok is False

    def test_empty_file_returns_false(self):
        """空输出文件返回 False。"""
        bridge = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=0):
            ok = renderer._trigger_render("/tmp/out/preview.mp3")

        assert ok is False


@pytest.mark.unit
class TestRenderPreview:
    """render_preview 公开 API 测试。"""

    def test_successful_preview(self, tmp_path):
        """成功渲染返回 PreviewResult。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        output_dir = str(tmp_path / "previews")
        output_path = os.path.join(output_dir, "test_label.mp3")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=50000):
            result = renderer.render_preview(
                output_dir=output_dir,
                duration_sec=10.0,
                label="test_label",
            )

        assert result.success is True
        assert result.preview_path is not None
        assert result.format == "mp3"
        assert result.bitrate_kbps == 128
        assert result.duration_sec == 10.0

    def test_render_failure_returns_error(self, tmp_path):
        """渲染失败返回错误。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)
        # Mock _trigger_render 返回 False（模拟失败）
        renderer._trigger_render = MagicMock(return_value=False)

        result = renderer.render_preview(
            output_dir=str(tmp_path),
            duration_sec=10.0,
        )

        assert result.success is False
        assert result.error is not None

    def test_exception_returns_error(self, tmp_path):
        """异常不传播。"""
        bridge = MagicMock()
        bridge.api = None  # 会触发 AttributeError
        renderer = PreviewRenderer(bridge)

        result = renderer.render_preview(output_dir=str(tmp_path))

        assert result.success is False
        assert result.error is not None


@pytest.mark.unit
class TestRenderABComparison:
    """render_ab_comparison 公开 API 测试。"""

    def test_successful_ab(self, tmp_path):
        """成功 A/B 对比返回正确路径。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        output_dir = str(tmp_path / "ab_test")
        before_path = os.path.join(output_dir, "before.mp3")
        after_path = os.path.join(output_dir, "after.mp3")

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=50000):
            result = renderer.render_ab_comparison(
                output_dir=output_dir,
                before_label="before",
                after_label="after",
                duration_sec=10.0,
            )

        assert result.success is True
        assert result.before_path is not None
        assert result.after_path is not None

    def test_before_render_failure(self, tmp_path):
        """Before 渲染失败时返回错误。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)
        # Mock _trigger_render: before 失败
        renderer._trigger_render = MagicMock(return_value=False)

        result = renderer.render_ab_comparison(
            output_dir=str(tmp_path),
        )

        assert result.success is False

    def test_exception_returns_error(self, tmp_path):
        """异常不传播。"""
        bridge = MagicMock()
        bridge.api = None  # 触发 AttributeError
        renderer = PreviewRenderer(bridge)

        result = renderer.render_ab_comparison(output_dir=str(tmp_path))

        assert result.success is False
        assert result.error is not None


@pytest.mark.unit
class TestPreviewRendererEdgeCases:
    """边界情况测试。"""

    def test_zero_duration_handled(self, tmp_path):
        """0 秒预览时长被正确处理。"""
        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=100):
            result = renderer.render_preview(
                output_dir=str(tmp_path),
                duration_sec=0.0,
                label="zero",
            )

        # 即使 0 秒，也应该能渲染（没有报错即通过）
        # 实际渲染结果取决于 REAPER，mock 中通过
        assert result.duration_sec == 0.0

    def test_path_object_accepted(self, tmp_path):
        """Path 对象作为 output_dir。"""
        from pathlib import Path

        bridge = MagicMock()
        bridge.api.GetSet_LoopTimeRange = MagicMock(
            return_value=(True, False, 0.0, 30.0, False),
        )
        bridge.api.GetSetProjectInfo_String = MagicMock()
        bridge.api.GetSetProjectInfo = MagicMock()
        bridge.api.Main_OnCommand = MagicMock()
        bridge.focus_reaper = MagicMock()
        renderer = PreviewRenderer(bridge)

        with patch("os.path.exists", return_value=True), \
             patch("os.path.getsize", return_value=1000):
            result = renderer.render_preview(
                output_dir=Path(str(tmp_path)),
                duration_sec=5.0,
                label="path_test",
            )

        assert result.success is True
