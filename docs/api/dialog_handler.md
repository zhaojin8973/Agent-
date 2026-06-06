# 跨平台弹窗处理

`hermes_core.dialog_handler` — 跨平台 REAPER 弹窗自动处理。

## 概述

自动检测并关闭 REAPER 的模态弹窗（保存确认、插件缺失警告等），支持 macOS / Windows / Linux。

## 平台实现

| 平台 | 实现类 | 后端 |
|------|--------|------|
| macOS | `MacOSDialogHandler` | AppleScript (osascript) |
| Windows | `WindowsDialogHandler` | pywinauto (UIA/Win32) |
| Linux | `LinuxDialogHandler` | xdotool |

## 工厂函数

`create_dialog_handler()` 根据 `sys.platform` 自动选择对应平台的处理器。

## API

::: hermes_core.dialog_handler
    options:
      members:
        - DialogHandler
        - MacOSDialogHandler
        - WindowsDialogHandler
        - LinuxDialogHandler
        - create_dialog_handler
