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
# Windows 实现（预留）
# ════════════════════════════════════════════════════════════════


class WindowsDialogHandler(DialogHandler):
    """Windows 实现（预留 — pywinauto / win32gui）。

    未来通过 win32gui 枚举窗口和发送消息实现。
    """

    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """未实现。"""
        raise NotImplementedError("Windows 弹窗处理暂不支持")

    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """未实现。"""
        raise NotImplementedError("Windows 弹窗处理暂不支持")

    def send_escape(self) -> bool:
        """未实现。"""
        raise NotImplementedError("Windows 弹窗处理暂不支持")


# ════════════════════════════════════════════════════════════════
# Linux 实现（预留）
# ════════════════════════════════════════════════════════════════


class LinuxDialogHandler(DialogHandler):
    """Linux 实现（预留 — xdotool / wmctrl）。

    未来通过 xdotool 搜索窗口和发送按键实现。
    """

    def inspect_windows(self) -> list[tuple[str, list[str]]]:
        """未实现。"""
        raise NotImplementedError("Linux 弹窗处理暂不支持")

    def click_button(self, title_fragment: str, button_match: str) -> bool:
        """未实现。"""
        raise NotImplementedError("Linux 弹窗处理暂不支持")

    def send_escape(self) -> bool:
        """未实现。"""
        raise NotImplementedError("Linux 弹窗处理暂不支持")
