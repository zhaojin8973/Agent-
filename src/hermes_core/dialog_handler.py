"""
跨平台弹窗处理接口。

当前仅实现 macOS（AppleScript），Windows/Linux 为预留接口。
macOS 实现委托给 bridge.py 的 AppleScript 逻辑，
同时也可独立使用（模块自包含 AppleScript 片段）。
"""

from __future__ import annotations

import subprocess
import logging
from abc import ABC, abstractmethod

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# AppleScript 片段（与 bridge.py 共用）
# ════════════════════════════════════════════════════════════════

_AS_INSPECT = """
tell application "System Events"
    tell process "REAPER"
        set output to ""
        repeat with w in windows
            set winName to name of w
            if (winName does not contain "REAPER v") and (winName is not "") then
                if (winName does not start with "FX:") and ¬
                   (winName does not start with "Routing") and ¬
                   (winName does not contain "Track Manager") and ¬
                   (winName does not contain "Media Explorer") and ¬
                   (winName does not contain "Performance Meter") and ¬
                   (winName does not contain "Virtual MIDI") and ¬
                   (winName does not contain "Region/Marker") and ¬
                   (winName does not contain "Undo History") and ¬
                   (winName does not contain "Screenset") and ¬
                   (winName does not contain "Action List") and ¬
                   (winName does not contain "Preferences") and ¬
                   (winName does not contain "Project Settings") then
                    set buttonList to ""
                    try
                        repeat with b in buttons of w
                            set btnName to name of b
                            if btnName is not "" then
                                set buttonList to buttonList & btnName & "|"
                            end if
                        end repeat
                    end try
                    set output to output & winName & ":::" & buttonList & ";;;"
                end if
            end if
            try
                repeat with s in sheets of w
                    set sheetName to name of s
                    if sheetName is not "" then
                        set buttonList to ""
                        try
                            repeat with b in buttons of s
                                set btnName to name of b
                                if btnName is not "" then
                                    set buttonList to buttonList & btnName & "|"
                                end if
                            end repeat
                        end try
                        set output to output & sheetName & ":::" & buttonList & ";;;"
                    end if
                end repeat
            end try
        end repeat
        return output
    end tell
end tell
"""

_AS_CLICK_BUTTON = """
tell application "System Events"
    tell process "REAPER"
        repeat with w in windows
            set winTitle to name of w
            if (winTitle contains "{title_fragment}") then
                repeat with b in buttons of w
                    if (name of b contains "{button_match}") then
                        click b
                        return "clicked:" & name of b
                    end if
                end repeat
            end if
            try
                repeat with s in sheets of w
                    set sheetTitle to name of s
                    if (sheetTitle contains "{title_fragment}") then
                        repeat with b in buttons of s
                            if (name of b contains "{button_match}") then
                                click b
                                return "clicked:" & name of b
                            end if
                        end repeat
                    end if
                end repeat
            end try
        end repeat
    end tell
end tell
"""

_AS_DISMISS = """
tell application "System Events"
    tell process "REAPER"
        repeat with w in windows
            set winName to name of w
            if (winName does not contain "REAPER v") and (winName is not "") then
                keystroke (ASCII character 27)
            end if
        end repeat
    end tell
end tell
"""


# ════════════════════════════════════════════════════════════════
# 抽象接口
# ════════════════════════════════════════════════════════════════


class DialogHandler(ABC):
    """跨平台弹窗处理抽象接口。

    子类需实现三个方法：窗口扫描、按钮点击、Escape 发送。
    """

    @abstractmethod
    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """扫描当前应用的弹窗窗口。

        Returns
        -------
        list[tuple[str, list[str]]]
            每个元素为 ``(窗口标题, [按钮名称, ...])`` 的元组列表。
        """
        ...

    @abstractmethod
    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """点击匹配标题和按钮名称的窗口按钮。

        Parameters
        ----------
        title_fragment : str
            窗口标题的部分文本（子串匹配）。
        button_match : str
            按钮名称的部分文本（子串匹配）。

        Returns
        -------
        bool
            True 表示点击成功，False 表示未找到匹配窗口或按钮。
        """
        ...

    @abstractmethod
    def send_escape(self) -> bool:
        """向活动弹窗发送 Escape 键。

        Returns
        -------
        bool
            True 表示操作成功，False 表示失败。
        """
        ...


# ════════════════════════════════════════════════════════════════
# macOS 实现
# ════════════════════════════════════════════════════════════════


class MacOSDialogHandler(DialogHandler):
    """macOS AppleScript 实现。

    通过 osascript 执行 AppleScript 来操作 REAPER 的弹窗。
    逻辑与 bridge.py 的 DialogKiller 共用 AppleScript 片段，
    但 MacOSDialogHandler 是独立、同步、按需调用的接口。
    """

    def __init__(self, timeout: float = 3.0) -> None:
        """初始化 macOS 弹窗处理器。

        Parameters
        ----------
        timeout : float
            osascript 子进程超时时间（秒）。
        """
        self._timeout = timeout

    # ── DialogHandler 接口实现 ─────────────────────────────

    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """扫描 REAPER 进程的所有非主窗口弹窗。"""
        raw = self._run_osascript(_AS_INSPECT)
        if not raw:
            return []
        windows: list[tuple[str, list[str]]] = []
        for segment in raw.split(";;;"):
            segment = segment.strip()
            if not segment:
                continue
            parts = segment.split(":::", 1)
            title = parts[0].strip()
            buttons = (
                [b.strip() for b in parts[1].split("|") if b.strip()]
                if len(parts) > 1
                else []
            )
            windows.append((title, buttons))
        return windows

    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """点击匹配标题和按钮名称的窗口按钮。"""
        safe_title = self._escape_applescript_string(title_fragment)
        safe_button = self._escape_applescript_string(button_match)
        script = _AS_CLICK_BUTTON.replace(
            "{title_fragment}", safe_title
        ).replace(
            "{button_match}", safe_button
        )
        result = self._run_osascript(script)
        return result.startswith("clicked:")

    def send_escape(self) -> bool:
        """向 REAPER 的非主窗口弹窗发送 Escape 键。"""
        raw = self._run_osascript(_AS_DISMISS)
        # Escape 操作无返回值，脚本执行成功即视为操作完成
        return True

    # ── 内部辅助 ───────────────────────────────────────────

    def _run_osascript(self, source: str) -> str:
        """运行 AppleScript 并返回 stdout，失败返回空字符串。"""
        try:
            proc = subprocess.run(
                ["osascript", "-e", source],
                capture_output=True,
                timeout=self._timeout,
            )
            return proc.stdout.decode("utf-8", errors="replace").strip()
        except Exception as e:
            log.debug("osascript failed: %s", e)
            return ""

    @staticmethod
    def _escape_applescript_string(s: str) -> str:
        """转义字符串以安全嵌入 AppleScript 字符串字面量。

        转义反斜杠、双引号，移除控制字符。
        """
        s = s.replace("\\", "\\\\")
        s = s.replace('"', '\\"')
        s = s.replace("\n", "").replace("\r", "").replace("\t", " ")
        return s


# ════════════════════════════════════════════════════════════════
# Windows 实现
# ════════════════════════════════════════════════════════════════


class WindowsDialogHandler(DialogHandler):
    """Windows pywinauto 实现。

    通过 pywinauto 的 UIA / Win32 后端操作 REAPER 弹窗。
    pywinauto 是可选依赖，仅在使用时导入。
    """

    def __init__(self, timeout: float = 5.0) -> None:
        self._timeout = timeout

    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """扫描 REAPER 进程的所有非主窗口弹窗。"""
        try:
            import pywinauto.findwindows
            import pywinauto.controls.hwndwrapper
        except ImportError:
            log.debug("pywinauto 未安装，无法扫描 Windows 弹窗")
            return []

        try:
            hwnds = pywinauto.findwindows.find_windows(
                title_re=".*", class_name="#32770",
            )
        except Exception as exc:
            log.debug("pywinauto 窗口扫描失败: %s", exc)
            return []

        windows: list[tuple[str, list[str]]] = []
        for hwnd in hwnds:
            try:
                wrapper = pywinauto.controls.hwndwrapper.HwndWrapper(hwnd)
                title = wrapper.window_text()
                if not title or "REAPER" not in title:
                    continue
                # 跳过主窗口
                if "REAPER v" in title:
                    continue
                buttons = []
                for child in wrapper.children():
                    if child.window_text():
                        buttons.append(child.window_text())
                windows.append((title, buttons))
            except Exception as exc:
                log.debug("pywinauto 窗口 %d 解析失败: %s", hwnd, exc)
                continue

        return windows

    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """点击匹配标题和按钮名称的窗口按钮。"""
        try:
            import pywinauto.findwindows
            import pywinauto.controls.hwndwrapper
        except ImportError:
            log.debug("pywinauto 未安装，无法点击 Windows 按钮")
            return False

        try:
            hwnds = pywinauto.findwindows.find_windows(
                title_re=f".*{title_fragment}.*", class_name="#32770",
            )
            for hwnd in hwnds:
                wrapper = pywinauto.controls.hwndwrapper.HwndWrapper(hwnd)
                for child in wrapper.children():
                    if button_match.lower() in child.window_text().lower():
                        child.click()
                        log.info("Windows 弹窗点击: %s → %s",
                                 title_fragment, child.window_text())
                        return True
        except Exception as exc:
            log.debug("pywinauto 按钮点击失败: %s", exc)

        return False

    def send_escape(self) -> bool:
        """向 REAPER 弹窗发送 Escape 键。"""
        try:
            import pywinauto.keyboard
            pywinauto.keyboard.send_keys("{ESC}")
            return True
        except ImportError:
            # 回退：使用 ctypes 调用 keybd_event
            try:
                import ctypes
                VK_ESCAPE = 0x1B
                KEYEVENTF_KEYUP = 0x0002
                ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, 0, 0)
                ctypes.windll.user32.keybd_event(VK_ESCAPE, 0, KEYEVENTF_KEYUP, 0)
                return True
            except Exception as exc:
                log.debug("Windows Escape 发送失败: %s", exc)
                return False


# ════════════════════════════════════════════════════════════════
# Linux 实现
# ════════════════════════════════════════════════════════════════


class LinuxDialogHandler(DialogHandler):
    """Linux xdotool 实现。

    通过 xdotool 搜索和操作 REAPER 弹窗。
    xdotool 是可选系统依赖（``apt install xdotool``）。
    """

    def __init__(self, timeout: float = 3.0) -> None:
        self._timeout = timeout

    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """扫描 REAPER 进程的所有非主窗口弹窗。"""
        if not self._check_xdotool():
            return []

        try:
            # 搜索所有 REAPER 相关窗口
            result = subprocess.run(
                ["xdotool", "search", "--name", "REAPER"],
                capture_output=True, timeout=self._timeout,
            )
            window_ids = result.stdout.decode().strip().split()
        except Exception as exc:
            log.debug("xdotool 窗口搜索失败: %s", exc)
            return []

        windows: list[tuple[str, list[str]]] = []
        for wid_str in window_ids:
            try:
                wid = wid_str.strip()
                # 获取窗口标题
                name_result = subprocess.run(
                    ["xdotool", "getwindowname", wid],
                    capture_output=True, timeout=self._timeout,
                )
                title = name_result.stdout.decode(errors="replace").strip()
                if not title or "REAPER v" in title:
                    continue
                # xdotool 无法枚举子控件，按钮列表为空
                windows.append((title, []))
            except Exception as exc:
                log.debug("xdotool 窗口 %s 解析失败: %s", wid_str, exc)
                continue

        return windows

    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """尝试使用 xdotool 搜索并点击按钮。

        注意：xdotool 无法直接枚举窗口内的按钮控件，
        所以此方法通过窗口搜索 + 按键模拟实现。
        对于简单的 OK/Cancel 弹窗，send_escape 通常已足够。
        """
        if not self._check_xdotool():
            return False

        try:
            # 搜索匹配窗口
            result = subprocess.run(
                ["xdotool", "search", "--name", title_fragment],
                capture_output=True, timeout=self._timeout,
            )
            window_ids = result.stdout.decode().strip().split()
            if not window_ids:
                return False

            wid = window_ids[0].strip()
            # 激活窗口
            subprocess.run(
                ["xdotool", "windowactivate", "--sync", wid],
                capture_output=True, timeout=self._timeout,
            )
            # 如果按钮名匹配 "OK"/"Yes"/"确定" → 按 Enter
            # 如果按钮名匹配 "Cancel"/"No"/"取消" → 按 Escape
            btn_lower = button_match.lower()
            if any(kw in btn_lower for kw in ("ok", "yes", "确定", "是", "save", "保存")):
                subprocess.run(
                    ["xdotool", "key", "Return"],
                    capture_output=True, timeout=self._timeout,
                )
                log.info("Linux 弹窗确认: %s → Return", title_fragment)
                return True
            elif any(kw in btn_lower for kw in ("cancel", "no", "取消", "否", "close", "关闭")):
                subprocess.run(
                    ["xdotool", "key", "Escape"],
                    capture_output=True, timeout=self._timeout,
                )
                log.info("Linux 弹窗取消: %s → Escape", title_fragment)
                return True
            else:
                # 通用尝试：激活窗口并发送 Enter
                subprocess.run(
                    ["xdotool", "key", "Return"],
                    capture_output=True, timeout=self._timeout,
                )
                return True
        except Exception as exc:
            log.debug("xdotool 按钮点击失败: %s", exc)

        return False

    def send_escape(self) -> bool:
        """向活动 REAPER 弹窗发送 Escape 键。"""
        if not self._check_xdotool():
            return False

        try:
            # 先搜索 REAPER 窗口，聚焦后发送 Escape
            result = subprocess.run(
                ["xdotool", "search", "--name", "REAPER"],
                capture_output=True, timeout=self._timeout,
            )
            window_ids = result.stdout.decode().strip().split()
            if window_ids:
                wid = window_ids[0].strip()
                subprocess.run(
                    ["xdotool", "windowactivate", "--sync", wid],
                    capture_output=True, timeout=self._timeout,
                )
            subprocess.run(
                ["xdotool", "key", "Escape"],
                capture_output=True, timeout=self._timeout,
            )
            return True
        except Exception as exc:
            log.debug("xdotool Escape 发送失败: %s", exc)
            return False

    @staticmethod
    def _check_xdotool() -> bool:
        """检查 xdotool 是否已安装。"""
        try:
            subprocess.run(
                ["which", "xdotool"],
                capture_output=True, timeout=2.0,
            )
            return True
        except Exception:
            return False


# ════════════════════════════════════════════════════════════════
# 平台检测工厂
# ════════════════════════════════════════════════════════════════


def create_dialog_handler(timeout: float = 3.0) -> DialogHandler:
    """根据当前操作系统自动创建对应的弹窗处理器。

    - macOS → :class:`MacOSDialogHandler`
    - Windows → :class:`WindowsDialogHandler`
    - Linux → :class:`LinuxDialogHandler`

    Parameters
    ----------
    timeout : float
        子进程超时时间（秒）。

    Returns
    -------
    DialogHandler
        平台对应的弹窗处理器实例。
    """
    import sys

    if sys.platform == "darwin":
        return MacOSDialogHandler(timeout=timeout)
    elif sys.platform == "win32":
        return WindowsDialogHandler(timeout=timeout)
    else:
        return LinuxDialogHandler(timeout=timeout)
