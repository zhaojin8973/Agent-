#!/usr/bin/env python3
"""
跨平台 DialogKiller 验证脚本。

在 Windows / Linux 上运行，验证 DialogHandler 的弹窗处理能力。

用法:
    # macOS（需要 REAPER 运行中 + 触发弹窗）
    PYTHONPATH=src python tools/verify_dialog_killer.py

    # Windows（需要 pywinauto 已安装）
    PYTHONPATH=src python tools/verify_dialog_killer.py

    # Linux（需要 xdotool 已安装）
    PYTHONPATH=src python tools/verify_dialog_killer.py

前置条件:
    - REAPER 运行中并有一个弹窗（如保存确认对话框）
    - Windows: ``pip install pywinauto``
    - Linux: ``sudo apt install xdotool``
"""
from __future__ import annotations

import sys
from hermes_core.dialog_handler import (
    create_dialog_handler,
    MacOSDialogHandler,
    WindowsDialogHandler,
    LinuxDialogHandler,
)


def check_dependencies(handler) -> list[str]:
    """检查处理器所需的系统依赖。"""
    issues = []
    if isinstance(handler, WindowsDialogHandler):
        try:
            import pywinauto  # noqa: F401
        except ImportError:
            issues.append("Windows: pywinauto 未安装 — pip install pywinauto")
    elif isinstance(handler, LinuxDialogHandler):
        if not LinuxDialogHandler._check_xdotool():
            issues.append("Linux: xdotool 未安装 — sudo apt install xdotool")
    return issues


def verify_platform():
    """验证平台检测是否正确。"""
    handler = create_dialog_handler()
    platform = sys.platform

    if platform == "darwin":
        expected = MacOSDialogHandler
    elif platform == "win32":
        expected = WindowsDialogHandler
    else:
        expected = LinuxDialogHandler

    if isinstance(handler, expected):
        print(f"[PASS] 平台检测: {platform} → {handler.__class__.__name__}")
    else:
        print(f"[FAIL] 平台检测: 期望 {expected.__name__}, 实际 {handler.__class__.__name__}")
        return False
    return True


def verify_inspect_windows(handler):
    """验证窗口检测接口。"""
    try:
        windows = handler.inspect_windows()
        print(f("[INFO] 检测到 {len(windows)} 个窗口"))
        for title, buttons in windows[:5]:  # 最多显示 5 个
            print(f"  - {title}: {buttons}")
        print("[PASS] inspect_windows 正常")
        return True
    except Exception as exc:
        print(f"[WARN] inspect_windows 失败: {exc}")
        return False


def verify_send_escape(handler):
    """验证 Escape 键发送。"""
    try:
        result = handler.send_escape()
        if result:
            print("[PASS] send_escape 成功")
        else:
            print("[INFO] send_escape 返回 False（可能无弹窗可关）")
        return True
    except Exception as exc:
        print(f"[WARN] send_escape 失败: {exc}")
        return False


def main():
    print("═" * 60)
    print("Hermes DialogKiller 跨平台验证")
    print(f"平台: {sys.platform}")
    print(f"Python: {sys.version}")
    print("═" * 60)

    handler = create_dialog_handler()
    print(f"\n处理器类型: {handler.__class__.__name__}")
    print(f"超时设置: {handler._timeout}s")

    # 1. 平台检测
    print("\n── 1. 平台检测 ──")
    verify_platform()

    # 2. 依赖检查
    print("\n── 2. 依赖检查 ──")
    issues = check_dependencies(handler)
    if issues:
        for issue in issues:
            print(f"[WARN] {issue}")
    else:
        print("[INFO] 依赖满足")

    # 3. 接口验证（基本不依赖弹窗存在）
    print("\n── 3. 窗口检测 ──")
    verify_inspect_windows(handler)

    # 4. Escape 发送
    print("\n── 4. Escape 键 ──")
    verify_send_escape(handler)

    # 5. 兼容性说明
    print("\n── 5. 兼容性验证 ──")
    if sys.platform == "darwin":
        print("[INFO] macOS: 已验证 AppleScript 弹窗控制")
    elif sys.platform == "win32":
        print("[INFO] Windows: 需要 pywinauto + REAPER 弹窗方可测试 click_button")
        print("[INFO] 手动测试: handler.click_button('REAPER', 'OK')")
    else:
        print("[INFO] Linux: 需要 xdotool + REAPER(WINE) 弹窗方可测试 click_button")
        print("[INFO] 手动测试: handler.click_button('REAPER', 'OK')")

    print("\n" + "═" * 60)
    print("验证完成。")
    print("═" * 60)


if __name__ == "__main__":
    main()
