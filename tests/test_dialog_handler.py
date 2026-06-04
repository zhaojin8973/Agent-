"""Tests for hermes_core.dialog_handler — 跨平台弹窗处理接口。

不依赖运行中的 REAPER，所有测试使用 mock。

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_dialog_handler.py -v
    PYTHONPATH=src python3 -m pytest tests/test_dialog_handler.py -v -m unit
"""

from unittest.mock import MagicMock, patch

import pytest

from hermes_core.dialog_handler import (
    DialogHandler,
    MacOSDialogHandler,
    WindowsDialogHandler,
    LinuxDialogHandler,
)


# ════════════════════════════════════════════════════════════════
# Unit: 抽象接口
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDialogHandlerABC:
    """验证抽象基类的约束。"""

    def test_cannot_instantiate_abc(self):
        """不能直接实例化抽象类。"""
        with pytest.raises(TypeError):
            DialogHandler()  # type: ignore[abstract]

    def test_subclass_must_implement_all_methods(self):
        """未实现全部方法的子类也不可实例化。"""

        class Incomplete(DialogHandler):
            def inspect_windows(self):
                return []

        with pytest.raises(TypeError):
            Incomplete()  # type: ignore[abstract]


# ════════════════════════════════════════════════════════════════
# Unit: MacOSDialogHandler
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMacOSDialogHandler:
    """测试 macOS 实现的接口和行为。"""

    def test_instantiate(self):
        """可以正常实例化。"""
        handler = MacOSDialogHandler()
        assert isinstance(handler, DialogHandler)

    def test_inspect_windows_returns_list(self):
        """inspect_windows 返回列表类型。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"", stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.inspect_windows()
            assert isinstance(result, list)

    def test_inspect_windows_parses_output(self):
        """正确解析 AppleScript 输出格式。"""
        fake_output = (
            b"Save project:::"        # 一个无按钮的窗口
            b"No|Don't Save|Cancel;;;"
        )
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=fake_output, stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.inspect_windows()

            assert len(result) == 1
            title, buttons = result[0]
            assert title == "Save project"
            assert buttons == ["No", "Don't Save", "Cancel"]

    def test_inspect_windows_handles_empty_output(self):
        """空输出返回空列表。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"", stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.inspect_windows()
            assert result == []

    def test_inspect_windows_handles_subprocess_error(self):
        """osascript 失败时返回空列表。"""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("osascript not found")
            handler = MacOSDialogHandler()
            result = handler.inspect_windows()
            assert result == []

    def test_click_button_success(self):
        """点击成功时返回 True。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"clicked:OK", stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.click_button("Save", "No")
            assert result is True

    def test_click_button_failure(self):
        """未找到匹配按钮时返回 False。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"", stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.click_button("Nonexistent", "OK")
            assert result is False

    def test_click_button_handles_subprocess_error(self):
        """osascript 异常时返回 False。"""
        with patch("subprocess.run") as mock_run:
            mock_run.side_effect = OSError("osascript not found")
            handler = MacOSDialogHandler()
            result = handler.click_button("Save", "No")
            assert result is False

    def test_send_escape_returns_true(self):
        """send_escape 返回 True（脚本成功执行）。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"", stderr=b"", returncode=0
            )
            handler = MacOSDialogHandler()
            result = handler.send_escape()
            assert result is True

    def test_escape_applescript_string_safe(self):
        """特殊字符被正确转义。"""
        safe = MacOSDialogHandler._escape_applescript_string(
            'He said "hello" then\\n left'
        )
        assert '"' not in safe.replace('\\"', "")
        assert "\n" not in safe
        # 双引号被转义为 \"
        assert '\\"' in safe

    def test_escape_applescript_string_empty(self):
        """空字符串安全。"""
        safe = MacOSDialogHandler._escape_applescript_string("")
        assert safe == ""


# ════════════════════════════════════════════════════════════════
# Unit: WindowsDialogHandler（预留）
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestWindowsDialogHandler:
    """验证 Windows 预留实现正确抛出 NotImplementedError。"""

    def test_inspect_windows_raises(self):
        handler = WindowsDialogHandler()
        with pytest.raises(NotImplementedError, match="Windows"):
            handler.inspect_windows()

    def test_click_button_raises(self):
        handler = WindowsDialogHandler()
        with pytest.raises(NotImplementedError, match="Windows"):
            handler.click_button("title", "button")

    def test_send_escape_raises(self):
        handler = WindowsDialogHandler()
        with pytest.raises(NotImplementedError, match="Windows"):
            handler.send_escape()


# ════════════════════════════════════════════════════════════════
# Unit: LinuxDialogHandler（预留）
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLinuxDialogHandler:
    """验证 Linux 预留实现正确抛出 NotImplementedError。"""

    def test_inspect_windows_raises(self):
        handler = LinuxDialogHandler()
        with pytest.raises(NotImplementedError, match="Linux"):
            handler.inspect_windows()

    def test_click_button_raises(self):
        handler = LinuxDialogHandler()
        with pytest.raises(NotImplementedError, match="Linux"):
            handler.click_button("title", "button")

    def test_send_escape_raises(self):
        handler = LinuxDialogHandler()
        with pytest.raises(NotImplementedError, match="Linux"):
            handler.send_escape()
