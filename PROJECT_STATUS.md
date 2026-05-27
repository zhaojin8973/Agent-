---
name: project-status-report
description: "Comprehensive project status — implemented features, gaps, test coverage, and next steps for hermes-core"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5448f8f-79ec-422c-ac06-b983660a36d1
---

# Hermes-Core 项目阶段性报告

**更新日期**: 2026-05-27
**版本**: 0.1.0
**最新提交**: 即将提交 (集成测试 31 个，总计 282 tests)

---

## 一、项目概述

hermes-core 是 REAPER DAW 的精益三层 Python 自动化引擎，目标是非交互式、无界面的混音工作流。通过 `python-reapy` 连接 REAPER ReaScript API。

**技术栈**: Python >=3.11, <3.14 | python-reapy >=0.10 | numpy >=1.24

---

## 二、架构：三层设计

| 层 | 模块 | 职责 |
|---|---|---|
| L1 桥接 | `bridge.py` | REAPER 连接、原始 API、UI 抑制、弹窗守护 |
| L2 领域 | `track.py` `bus.py` `fx.py` `send.py` `render.py` `signal.py` `normalize.py` | 各领域管理器，仅依赖 L1 |
| L3 入口 | `engine.py` | 组合所有 L2 模块，提供统一的 MixingEngine API |

---

## 三、已实现功能

### L1: bridge.py
- [x] ReaperBridge — reapy 连接/重连/健康检查
- [x] 上下文管理器（__enter__/__exit__）
- [x] UI 锁定/解锁（PreventUIRefresh）
- [x] DialogKiller — macOS AppleScript 弹窗自动关闭
- [x] 三级弹窗分类（安全/诊断/未知）
- [x] AppleScript 注入防御
- [x] 可自定义弹窗规则

### L2: track.py
- [x] 音轨 CRUD（创建/删除/列表）
- [x] 属性管理（名称/音量/声像/静音/独奏/文件夹深度）
- [x] 媒体导入（支持 ARM64 绕过 bug）
- [x] TrackInfo 数据类 + 序列化

### L2: bus.py
- [x] 文件夹总线创建/拆解
- [x] I_FOLDERDEPTH 语义管理
- [x] 文件夹树结构查询
- [x] 总线结构验证

### L2: fx.py
- [x] FX 链 CRUD（添加/删除/获取链）
- [x] 参数读/写（按索引或名称）
- [x] FX 启用/旁路
- [x] FX 复制/移动到其他音轨
- [x] ARM64 假阳性防御（RPR fallback → reapy）

### L2: send.py
- [x] 发送创建/删除
- [x] 电平/声像/静音控制
- [x] 三种发送模式（post-fader/pre-fx/pre-fader）
- [x] 发送查询与列表

### L2: render.py
- [x] 非模态渲染（Main_OnCommand 42230）
- [x] 统一飞行前检查（5 项：bounds/format/content/time-selection/writable）
- [x] 支持 WAV/FLAC/MP3
- [x] 支持 entire_project/time_selection
- [x] 渲染超时控制
- [x] 时间选区管理

### L2: signal.py
- [x] WAV 读取（16/24-bit PCM + 32-bit float）
- [x] RMS / 峰值 dBFS
- [x] 集成 LUFS（ITU-R BS.1770-4，含 K-加权+双门限）
- [x] 真峰值 dBTP（4x 过采样 sinc 插值）
- [x] 削波检测 + 静音检测

### L2: normalize.py
- [x] 单轨 LUFS 归一化（solo→渲染→分析→增益调整）
- [x] 批量全轨归一化
- [x] 项目状态保护（solo + time selection 备份/恢复）
- [x] 跳过无音频轨

### L3: engine.py
- [x] 场景 1: 连接与健康检查
- [x] 场景 2: 创建项目（命名/采样率/返回状态）+ 导入 stems + **保存工程 (Ctrl+S)** + **获取工程信息**
- [x] 场景 3: 增益分级（track_fader）
- [x] 场景 4: FX 添加与查询
- [x] 场景 5: 总线创建 + 混响发送
- [x] 场景 6: 渲染混音（含信号分析验证）
- [x] 场景 7: 响度归一化
- [x] 场景 9: 混音安全审计

---

## 四、未实现/存根

| 功能 | 位置 | 状态 |
|---|---|---|
| `apply_gain(target="clip_gain")` | engine.py | NotImplementedError |
| `apply_gain(target="master_fader")` | engine.py | NotImplementedError |
| `check_headroom()` 有效实现 | engine.py | 返回 "unavailable_without_render" 存根 |
| 32-bit float WAV 测试 | test_signal.py | 路径存在但无测试 |
| 集成测试（除 render 外） | tests/ | 仅 render.py 有 3 个集成测试 |

---

## 五、测试覆盖

| 文件 | 测试数 | 覆盖率 |
|---|---|---|
| test_bridge.py | 33 | 76% |
| test_bus.py | 16 | 87% |
| test_engine.py | 42 | 94% |
| test_fx.py | 46 | 81% |
| test_normalize.py | 20 | 98% |
| test_render.py | 53 | 100% |
| test_send.py | 22 | 88% |
| test_signal.py | 21 | 93% |
| test_track.py | 34 | 84% |
| **总计** | **292** | **89%** |

- 单元测试: 256 个（mock REAPER）
- 集成测试: 36 个（需要 REAPER 运行）
- 0 个测试 skip

---

## 六、已知问题

1. **REAPER Python 兼容性**：Python 3.14 不兼容 REAPER 7.73，必须使用 3.13
2. **DialogKiller 仅 macOS**：依赖 AppleScript，不支持 Windows/Linux
3. **reaper-kb.ini 残留**：有指向 Python 3.14 和旧项目的 reapy 脚本引用

## 八、上次发现的严重 Bug（已修复）

1. `render.py`: `GetSetLoopTimeRange` → `GetSet_LoopTimeRange`（API 名称缺下划线）
2. `render.py`: `get_time_selection_range` 返回值未解包（应解包 5 元组）
3. `render.py`: `get_time_selection_range` 第二次调用使用了错误的 `isLoop=True`

---

## 七、下一步可做

### 优先级高
- [ ] 实现 `apply_gain` 的 clip_gain 和 master_fader 支持
- [ ] 实现 `check_headroom()` — 渲染后分析真峰值余量
- [ ] 补全 32-bit float WAV 的单元测试
- [ ] 清理 reaper-kb.ini 中的 Python 3.14 路径

### 优先级中
- [ ] 各 L2 模块增加真实 REAPER 集成测试
- [ ] DialogKiller 跨平台支持（Windows 用 pywin32，Linux 用 xdotool）
- [ ] render.py 增加渲染进度反馈
- [ ] `check_headroom()` 在 engine.py 的实现

### 优先级低
- [ ] 修复 2 个 skip 的测试
- [ ] CI/CD pipeline 搭建
- [ ] 降噪/去嘶声等专用处理模块

---

## 八、环境依赖

- **REAPER**: 7.73 (arm64)
- **Python**: 3.13.12 (Homebrew) — 3.14 不兼容！
- **reapy**: 0.10.0
- **numpy**: 已安装
- **reaper.ini**: 已修复 `pythonlibpath64` 指向 Python 3.13

---

## 更新日志

| 日期 | 变更 |
|---|---|
| 2026-05-27 | 测试覆盖率提升 87%→89%，新增 18 个测试，2 个 skip 修复，32-bit float WAV 测试，_extract_string/_extract_reaper_string 测试 |
