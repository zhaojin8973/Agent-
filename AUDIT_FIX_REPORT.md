# RE_AUDIT_REPORT.md 整改完成报告

> **整改日期**: 2026-06-04 | **基准报告**: `RE_AUDIT_REPORT.md`
> **整改范围**: commit `4669392` (全面重构) 的 18 项问题逐项审核与修复

---

## 一、执行摘要

对 `RE_AUDIT_REPORT.md` 中标记的所有 P0/P1/P2 问题进行了逐项审查和修复。

- **确认真实 Bug**: 6 项 → **全部修复**
- **审计误报**: 3 项 → 经代码验证不存在
- **Phase 1 已解决**: 2 项 → 无需额外改动
- **留给后续**: 7 项 → 需 REAPER 环境或更多设计工作

**最终测试: 1270 passed, 53 skipped, 0 failed, 72% 覆盖率**

---

## 二、P0 修复详情（必须立即修复的崩溃级 Bug）

### P0-1: agent_protocol.py 调用 engine 不存在的方法

**审计判断**: ❌ 误报

**验证过程**:
审计报告称 `agent_protocol.py` 调用了 `adjust_eq_brightness()`, `adjust_compression()` 等不存在的方法。经代码审查确认，`HermesAgentAPI._apply_adjustment()` 使用的是内部路由机制，将 11 种 `AdjustmentType` 映射到 engine 上实际存在的方法：

```python
# 实际代码路径 (agent_protocol.py:736-771)
eng.update_node_param(node, "presence_boost", ...)   # ✅ 存在
eng.apply_gain(0, intensity * 1.5)                    # ✅ 存在
eng._reverb_send_node                                 # ✅ 存在
eng.render_preview(...)                               # ✅ 存在
```

审计报告描述的方法名（如 `self._engine.adjust_eq_brightness(intensity)`）在源代码中不存在。

**结论**: 无需修改，测试通过（mock 未掩盖真实调用路径）。

---

### P0-2: dialog_handler.py 模块级 import 跨平台崩溃

**审计判断**: ❌ 误报

**验证过程**:
审计报告称 `dialog_handler.py` 顶部有 `from pywinauto import Application`，在 macOS 上会导致 `ImportError`。经代码审查：

- 文件中唯一提及 `pywinauto` 的是第 269 行 docstring 注释：`"""Windows 实现（预留 — pywinauto / win32gui）。"""`
- `WindowsDialogHandler` 所有方法均使用 `raise NotImplementedError(...)`，无实际导入
- 模块顶部导入仅有 `subprocess`, `logging`, `abc`

```python
# dialog_handler.py 实际导入 (line 8-13)
from __future__ import annotations
import subprocess
import logging
from abc import ABC, abstractmethod
```

**结论**: 代码已使用正确的惰性初始化模式，无需修改。

---

### P0-3: mastering.py `_friendly_hint` 返回值截断 ✅

**严重级别**: CRITICAL — 生产环境消息丢失

**问题**: `mastering.py` 第 85-87 行跨行字符串未加括号，Python 隐式字符串连接仅在括号内生效，第 86-87 行成为不可达表达式。

```python
# 修复前 — 只返回第一行
return "Check the log for details. Common issues: missing plugins, "
"unwritable output directory, insufficient disk space, or REAPER "
"modal dialogs blocking automation."
```

**修复**: 添加括号包裹多行字符串

```python
# 修复后 — 正确拼接三行
return (
    "Check the log for details. Common issues: missing plugins, "
    "unwritable output directory, insufficient disk space, or REAPER "
    "modal dialogs blocking automation."
)
```

**文件**: `src/hermes_core/mastering.py` — 第 85-88 行

---

### P0-4: engine.py 缺少 `from pathlib import Path` ✅

**严重级别**: CRITICAL — 运行 `create_project()` 时 `NameError`

**问题**: `engine.py` 第 1116 行 `Path(output_dir)` 使用了 `Path` 类，但文件顶部未导入。

**修复**: 在模块顶部添加导入

```python
# 修复后 (engine.py:14-15)
from pathlib import Path
from typing import Callable
```

**文件**: `src/hermes_core/engine.py` — 第 14 行

---

## 三、P1 修复详情（代码质量问题）

### P1-5: engine.py 删除已提取到新模块的重复代码 ✅

**问题**: 审计报告指出新模块中的代码是从 engine.py **复制**而非**移动**，导致同一逻辑存在两份副本。

**验证与修复**:

| 新模块 | 审计报告状态 | 实际验证 | 处理 |
|-------|------------|---------|------|
| genre_tables.py | 引擎有副本 (L54-560) | 引擎仅导入使用，无副本 | ✅ 无误报 |
| comp_engine.py | 引擎有副本 (L565-800) | 引擎仅导入使用，无副本 | ✅ 无误报 |
| spatial_engine.py | 引擎有副本 (L263-326) | 引擎仅导入使用，无副本 | ✅ 无误报 |
| gain_staging.py | 引擎有副本 (L1641-1795) | **确认重复** | 🔧 已修复 |

**实际重复**: `MixingEngine._prepare_stems_impl()` 和 `MixingEngine._balance_faders()` 在 engine.py 和 gain_staging.py 中有两份几乎完全相同的实现。

**修复**: 将 engine.py 的调用委托给 `GainStagingEngine`，删除 engine.py 中的重复方法：

```python
# 修复后 — prepare_stems 委托给 GainStagingEngine
def _do_prepare():
    return self._gain_staging.prepare(
        stem_paths, genre=genre, vocal_indices=vocal_indices,
        backing_indices=backing_indices,
    )

# 修复后 — balance 委托给 GainStagingEngine
balance_info = self._gain_staging._balance_faders(
    stems,
    vocal_indices=vocal_indices,
    backing_indices=backing_indices,
    genre=genre,
)
```

**删除代码量**:
- `_prepare_stems_impl`: 94 行
- `_balance_faders`: 60 行
- 相关测试更新为直接调用 `eng._gain_staging._balance_faders()`

**文件**: `src/hermes_core/engine.py`, `tests/test_engine.py`

---

### P1-8: 删除 signal.py 死代码 ✅

**问题**: `_biquad_hp()` 已被 scipy 替代，无任何调用者。

```bash
$ grep -rn "_biquad_hp" src/ tests/
# 仅在定义处出现，零引用
```

**修复**: 删除 `_biquad_hp()` 静态方法（含 12 行实现 + 注释）。

`_to_mono()` 保留 — 虽无生产代码调用，但测试有引用，且属于合理的工具函数。

**文件**: `src/hermes_core/signal.py` — 删除第 224-236 行

---

### P1-9: `_read_pcm()` 迁移至 audio_utils ✅

**问题**: 5 处调用点仍使用 `SignalAnalyzer._read_pcm()` 而非 `audio_utils.read_pcm()`，两函数逻辑完全相同。

**迁移清单**:

| 文件 | 行号 | 修改 |
|------|------|------|
| `loudness_optimizer.py` | 139, 203, 234, 239 | `SignalAnalyzer._read_pcm(x)` → `read_pcm(x)` |
| `engine.py` | 3219 | `SignalAnalyzer._read_pcm(dry_path)` → `read_pcm(dry_path)` |

**导入更新**:
- `loudness_optimizer.py`: 新增 `from hermes_core.audio_utils import read_pcm`
- `engine.py`: `from hermes_core.audio_utils import note_to_ms` → `...import note_to_ms, read_pcm`

**文件**: `src/hermes_core/loudness_optimizer.py`, `src/hermes_core/engine.py`

---

### P1-10: engine.py 冗余内联 import ✅

**问题**: 12 处内联 import 重复模块级已导入的符号。

**删除清单**:

| 内联 import | 数量 | 说明 |
|------------|------|------|
| `from hermes_core.eq_engine import _apply_proq3_eq` | 6 | 模块级已导入 (line 111) |
| `from hermes_core.loudness_optimizer import EqIntent, EqBandIntent` | 2 | 模块级已导入 (line 34-35) |
| `from hermes_core.bridge import _extract_reaper_string` | 2 | **升级为模块级** |

**模块级导入升级**:

```python
# 修复前
from hermes_core.bridge import ReaperBridge

# 修复后
from hermes_core.bridge import ReaperBridge, _extract_reaper_string
```

**文件**: `src/hermes_core/engine.py`

---

### P1-11: engine.py 删除死代码 `_finalize_master_impl` ✅

**问题**: `MixingEngine._finalize_master_impl()` (107 行) 未被任何代码调用，`finalize_master()` 已委托给 `MasteringEngine.finalize()`。

```python
# 实际使用的新代码 (engine.py:3220-3230)
# 委托给 MasteringEngine
self._mastering._on_progress = on_progress
result = self._undo_block(
    "Finalize Master",
    lambda: self._mastering.finalize(
        target_lufs, limiter_fx=limiter_fx, ...),
)
```

**修复**: 删除整个 `_finalize_master_impl` 方法（107 行）。

**文件**: `src/hermes_core/engine.py` — 删除原第 3238-3344 行

---

## 四、P2 处理详情（建议修复）

| # | 问题 | 状态 | 说明 |
|---|------|------|------|
| 12 | ConnectionError/TimeoutError 重命名 | ✅ Phase 1 已完成 | 已在 `exceptions.py` 中改为 `BridgeConnectionError` |
| 13 | sanitize_filename 支持中文 | ❌ 误报 | `sanitize_filename()` 函数不存在于代码库中 |
| 14 | audit.py 自动持久化 | ⏸️ 后续 | 需设计持久化策略（崩溃恢复方案） |
| 15 | CI/CD 补充 mypy | ✅ 已存在 | `.github/workflows/ci.yml` 中 `type-check` job 已配置 |
| 16 | mkdocs 补充页面 | ⏸️ 后续 | 文档完善任务 |
| 17 | Profile YAML De-Esser | ⏸️ 后续 | 功能增强，非 bug 修复 |
| 18 | gain_staging 委托剩余方法 | ✅ 已修复 | `_balance_faders` 已委托至 `GainStagingEngine` |

---

## 五、量化改善

### engine.py 瘦身

| 指标 | 修改前 | 修改后 | 变化 |
|------|-------|-------|------|
| 总行数 | 3,743 | 3,469 | **-274 (-7.3%)** |
| 文件大小 | 151KB | 141KB | **-10KB (-6.6%)** |
| 重复方法 | 2 个 (`_prepare_stems_impl`, `_balance_faders`) | 0 个 | **全消除** |
| 死代码方法 | 1 个 (`_finalize_master_impl`) | 0 个 | **全消除** |
| 冗余内联 import | 12 处 | 0 处 | **全消除** |

### 其他文件

| 文件 | 变化 |
|------|------|
| `signal.py` | -12 行（删除 `_biquad_hp`） |
| `mastering.py` | 修复字符串截断 Bug |
| `loudness_optimizer.py` | 迁移 4 处 `_read_pcm` 调用 |
| `test_engine.py` | 更新 2 处 balance 测试 |

### 测试结果

```
修改前: 1270 passed, 53 skipped, 0 failed, 70% 覆盖率
修改后: 1270 passed, 53 skipped, 0 failed, 72% 覆盖率
变化:   +0 passed, +2pp 覆盖率, 零回归
```

---

## 六、代码改动文件清单

| 文件 | 操作 | 说明 |
|------|------|------|
| `src/hermes_core/engine.py` | 修改 | P0-4, P1-5, P1-9, P1-10, P1-11 |
| `src/hermes_core/mastering.py` | 修改 | P0-3 |
| `src/hermes_core/signal.py` | 修改 | P1-8 |
| `src/hermes_core/loudness_optimizer.py` | 修改 | P1-9 |
| `tests/test_engine.py` | 修改 | P1-5（测试更新） |
| `RE_AUDIT_REPORT.md` | 基准 | 审计报告原文 |
| `AUDIT_FIX_REPORT.md` | 新增 | 本报告 |

---

## 七、留给后续的工作

以下项目确认为有效问题，但超出本次整改范围（需要 REAPER 环境、更多设计工作，或属于功能增强）：

1. **接入闲置模块** (P1-6): `plugin_registry`, `backing`, `preview`, `reference`, `dialog_handler` 目前写了但未接入 engine.py 主流程
2. **审计持久化** (P2-14): `audit.py` 仅内存存储，崩溃会丢失审计记录
3. **文档完善** (P2-16): mkdocs 补充缺失页面
4. **Profile 增强** (P2-17): 9 个流派 Profile YAML 增加 De-Esser 和空间效果配置
5. **集成测试** (P1-7): 需在真实 REAPER 环境中验证闲置模块的接入

---

## 八、结论

审计报告 `RE_AUDIT_REPORT.md` 对代码库的诊断基本准确，但有 3 项问题经代码验证确认为误报。本次整改修复了所有真实的 P0 崩溃级 Bug 和大部分 P1 代码质量问题，engine.py 瘦身 274 行，消除了 12 处冗余内联 import 和 ~230 行死代码。

**1270 测试全部通过，零回归。**

核心遗留问题是 engine.py 拆分不彻底（审计中标记为"核心问题"）。虽然本次删除了 `_prepare_stems_impl` 和 `_balance_faders` 的重复实现，但 engine.py 仍承担过多职责（3,469 行，74 个方法）。彻底拆分为薄 Facade（目标 ~500 行）需要更大规模的架构调整，属于后续迭代的工作。
