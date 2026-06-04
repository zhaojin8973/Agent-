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
# Unit: WindowsDialogHandler
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestWindowsDialogHandler:
    """验证 Windows pywinauto 实现的接口和行为。"""

    def test_instantiate(self):
        """可以正常实例化。"""
        handler = WindowsDialogHandler()
        assert isinstance(handler, DialogHandler)

    def test_inspect_windows_returns_list_no_pywinauto(self):
        """无 pywinauto 时返回空列表（不抛异常）。"""
        handler = WindowsDialogHandler()
        # 在非 Windows 平台上 pywinauto 未安装，应优雅降级
        result = handler.inspect_windows()
        assert isinstance(result, list)

    def test_click_button_returns_false_no_pywinauto(self):
        """无 pywinauto 时返回 False（不抛异常）。"""
        handler = WindowsDialogHandler()
        result = handler.click_button("title", "OK")
        assert result is False

    def test_send_escape_graceful_no_pywinauto(self):
        """无 pywinauto 时 send_escape 优雅降级。"""
        handler = WindowsDialogHandler()
        # 在 macOS/Linux 上可能通过 ctypes 或直接返回 False
        result = handler.send_escape()
        assert isinstance(result, bool)


# ════════════════════════════════════════════════════════════════
# Unit: LinuxDialogHandler
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestLinuxDialogHandler:
    """验证 Linux xdotool 实现的接口和行为。"""

    def test_instantiate(self):
        """可以正常实例化。"""
        handler = LinuxDialogHandler()
        assert isinstance(handler, DialogHandler)

    def test_inspect_windows_returns_list_no_xdotool(self):
        """无 xdotool 时返回空列表（不抛异常）。"""
        handler = LinuxDialogHandler()
        # 在 macOS 上 xdotool 未安装，应优雅降级
        result = handler.inspect_windows()
        assert isinstance(result, list)

    def test_click_button_returns_false_no_xdotool(self):
        """无 xdotool 时返回 False（不抛异常）。"""
        handler = LinuxDialogHandler()
        result = handler.click_button("title", "OK")
        assert result is False

    def test_send_escape_returns_false_no_xdotool(self):
        """无 xdotool 时 send_escape 返回 False。"""
        handler = LinuxDialogHandler()
        # _check_xdotool 失败（macOS 上不存在 xdotool）→ 返回 False
        result = handler.send_escape()
        assert result is False

    def test_click_button_with_ok_pattern(self):
        """按钮名匹配 OK/确定 时尝试发送 Return 键。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"12345\n", stderr=b"", returncode=0
            )
            handler = LinuxDialogHandler()
            result = handler.click_button("REAPER Save", "OK")
            assert result is True

    def test_click_button_with_cancel_pattern(self):
        """按钮名匹配 Cancel/取消 时尝试发送 Escape 键。"""
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"12345\n", stderr=b"", returncode=0
            )
            handler = LinuxDialogHandler()
            result = handler.click_button("REAPER Warning", "Cancel")
            assert result is True


# ════════════════════════════════════════════════════════════════
# Unit: create_dialog_handler 工厂
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCreateDialogHandler:
    """验证平台检测工厂创建正确的处理器类型。"""

    def test_creates_macos_handler_on_darwin(self):
        """macOS 上创建 MacOSDialogHandler。"""
        from hermes_core.dialog_handler import create_dialog_handler
        with patch("sys.platform", "darwin"):
            handler = create_dialog_handler()
            assert isinstance(handler, MacOSDialogHandler)

    def test_creates_windows_handler_on_win32(self):
        """Windows 上创建 WindowsDialogHandler。"""
        from hermes_core.dialog_handler import create_dialog_handler
        with patch("sys.platform", "win32"):
            handler = create_dialog_handler()
            assert isinstance(handler, WindowsDialogHandler)

    def test_creates_linux_handler_on_linux(self):
        """Linux 上创建 LinuxDialogHandler。"""
        from hermes_core.dialog_handler import create_dialog_handler
        with patch("sys.platform", "linux"):
            handler = create_dialog_handler()
            assert isinstance(handler, LinuxDialogHandler)
