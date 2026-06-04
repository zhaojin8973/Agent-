# Hermes-Core 二次审核报告

> **审核日期**: 2026-06-04 | **审核范围**: commit `4669392` (全面重构)
> **变更量**: +15 源文件, +11 测试文件, +8 Profile YAML, +CI/CD

---

## 一、总体评估

### 测试结果

```
✅ 1270 passed, 53 deselected, 1 warning
⏱  19.58s
📊 Coverage: 70% (6177 statements, 1837 missed)
```

**所有 1270 个单元测试全部通过**，无失败、无跳过。

### 变更规模统计

| 类别 | 修改前 | 修改后 | 变化 |
|------|-------|-------|------|
| 源文件数 | 21 | 36 | **+15** |
| 测试文件数 | 24 | 35 | **+11** |
| Profile 数 | 1 | 9 | **+8** |
| 源代码总量 | ~450KB | ~640KB | **+42%** |
| 测试代码总量 | ~400KB | ~630KB | **+58%** |
| engine.py 大小 | 180KB / 4441行 | 151KB / 3743行 | **-16%** |

### 打分卡

| 维度 | 评分 | 说明 |
|------|------|------|
| **功能完整度** | ⭐⭐⭐⭐ A- | 新增 Agent Protocol、安全层、审计、进度、预览、参考曲、伴奏处理 |
| **代码质量** | ⭐⭐⭐ B | 新模块质量好，但 engine.py 拆分不彻底，存在大量重复代码 |
| **测试覆盖** | ⭐⭐⭐⭐ A- | 1270个测试，70% 覆盖率，所有新模块有配套测试 |
| **集成完整性** | ⭐⭐ C | 多个新模块未接入 engine.py，agent_protocol 调用不存在的方法 |
| **安全性** | ⭐⭐⭐⭐ A- | 路径沙箱、限流、文件保护均已实现 |
| **Profile 丰富度** | ⭐⭐⭐⭐ A | 9 个流派全覆盖 |

---

## 二、🔴 必须修复的 Bug（P0）

### Bug 1：agent_protocol.py 调用 engine 上不存在的方法

`HermesAgentAPI.adjust()` 方法中调用了 `MixingEngine` 上不存在的方法，**生产环境运行会直接 `AttributeError` 崩溃**：

```python
# agent_protocol.py 中调用的方法 → engine.py 中不存在：
self._engine.adjust_eq_brightness(intensity)    # ❌ AttributeError
self._engine.adjust_compression(intensity)      # ❌ AttributeError
self._engine.adjust_reverb_level(intensity)      # ❌ AttributeError
self._engine.adjust_stereo_width(intensity)      # ❌ AttributeError
self._engine.adjust_delay_level(intensity)       # ❌ AttributeError
self._engine.render_preview(...)                 # ❌ AttributeError
```

> [!CAUTION]
> 测试通过是因为测试用 mock 替换了这些方法调用。但实际运行 Agent 时这些调用会全部崩溃。

**修复方案**: 在 `MixingEngine` 中实现这些方法，或修改 `agent_protocol.py` 使用已有的引擎方法。

---

### Bug 2：dialog_handler.py 模块级 import 会在非目标平台崩溃

```python
# dialog_handler.py 文件顶部：
from pywinauto import Application  # ← macOS/Linux 上直接 ImportError
```

`WindowsDialogHandler` 类在模块顶部 import `pywinauto`，导致 **在 macOS 上仅仅 import 这个文件就会崩溃**。

**修复方案**: 将平台特定的 import 改为延迟导入（在类方法内部 import）。

---

### Bug 3：mastering.py `_friendly_hint` 默认返回值被截断

```python
# mastering.py 第85-87行 — 跨行字符串未加括号：
return "Check the log for details. Common issues: missing plugins, "
"unwritable output directory, insufficient disk space, or REAPER "
"modal dialogs blocking automation."
```

Python 会只返回第一行字符串，后两行是**不可达的表达式语句（dead code）**。

**修复方案**: 加括号包裹多行字符串。

---

### Bug 4：engine.py 缺少 `from pathlib import Path`

`create_project()` 中使用了 `Path(output_dir)` 但文件顶部未 import `pathlib.Path`。运行时会 `NameError`。

---

## 三、🟠 engine.py 拆分评估（核心问题）

### 拆分结果：不合格——复制而非移动

> [!WARNING]
> 新模块中的代码是从 engine.py **复制** 出来的，但 engine.py 中的**原始代码并未删除**。这意味着同一逻辑存在两份副本，维护时需要同时修改两处。

| 新模块 | 模块中有 | engine.py 中也有 | 状态 |
|-------|---------|----------------|------|
| genre_tables.py | 所有流派参数字典 | ✅ 仍有完整副本 (L54-560) | 🔴 重复 |
| comp_engine.py | 压缩器转换函数 | ✅ 仍有 `_apply_vca_params` 等 (L565-800) | 🔴 重复 |
| spatial_engine.py | `_compute_spatial_sends` | ✅ 仍有完整副本 (L263-326) | 🔴 重复 |
| gain_staging.py | `_prepare_stems_impl`, `_balance_faders` | ✅ engine.py 有自己的副本 (L1641-1795) | 🔴 重复 |
| mastering.py | `_finalize_master_impl` | ✅ engine.py 保留死代码 (L3237-3343) | 🟡 死代码 |
| eq_engine.py | EQ 意图和转换 | 已正确委托 | ✅ 正常 |
| plugin_registry.py | 插件注册表 | engine.py 不使用此模块 | 🟡 未接入 |
| backing.py | 伴奏处理 | engine.py 不使用此模块 | 🟡 未接入 |

### 拆分合格标准 vs 实际

| 指标 | 目标 | 实际 | 差距 |
|------|------|------|------|
| engine.py 行数 | ~500行 (薄 Facade) | ~3743行 | 差 7.5 倍 |
| engine.py 大小 | ~20KB | 151KB | 差 7.5 倍 |
| 代码重复率 | 0% | ~25% 内容重复 | 需二次清理 |

---

## 四、🟠 新模块集成状态

### 模块是否真正接入系统？

| 新模块 | engine.py 导入? | engine.py 使用? | __init__.py 导出? | 评价 |
|-------|:-:|:-:|:-:|------|
| genre_tables.py | ✅ | ⚠️ 导入了但保留自己的副本 | — | 重复 |
| comp_engine.py | ✅ | ⚠️ 导入了但保留自己的副本 | — | 重复 |
| eq_engine.py | ✅ | ✅ 正确委托 | — | ✅ 正常 |
| spatial_engine.py | ✅ | ⚠️ 导入了但保留副本 | — | 重复 |
| mastering.py | ✅ | ⚠️ 有死代码副本 | — | 部分 |
| gain_staging.py | ✅ | ⚠️ 仅部分方法委托 | — | 部分 |
| plugin_registry.py | ❌ | ❌ | ✅ | 🔴 闲置 |
| backing.py | ❌ | ❌ | — | 🔴 闲置 |
| agent_protocol.py | ❌ (它导入engine) | — | ✅ | ⚠️ 独立但有Bug |
| security.py | ✅ | ✅ | ✅ | ✅ 正常 |
| audit.py | ✅ | ✅ | ✅ | ✅ 正常 |
| progress.py | ✅ | ✅ | ✅ | ✅ 正常 |
| preview.py | ❌ | ❌ | ✅ | 🔴 闲置 |
| dialog_handler.py | ❌ | ❌ (bridge.py仍用内联) | ❌ | 🔴 闲置 |
| reference.py | ❌ | ❌ | ❌ | 🔴 闲置 |

**结论**: 15 个新模块中，只有 **6 个真正集成运行**。其余 **9 个处于"写了但没接上"的状态**。

---

## 五、✅ 做得好的部分

### 5.1 新模块内部代码质量高

| 模块 | 亮点 | 覆盖率 |
|------|------|--------|
| security.py | 路径沙箱+符号链接解析+限流+磁盘空间 | 92% |
| audit.py | 线程安全，JSON 持久化，操作查询 | 97% |
| progress.py | 推/拉双模式 | 99% |
| reference.py | 频谱匹配+响度对齐+动态范围匹配 | 98% |
| plugin_registry.py | 统一插件注册+回退链+预检 | 98% |
| backing.py | 伴奏总线压缩+频率互让 | 99% |
| genre_tables.py | 纯数据，无依赖，干净 | 100% |
| comp_engine.py | 压缩器参数推导完整 | 98% |
| spatial_engine.py | 信号自适应空间发送 | 100% |

### 5.2 已有模块的改进

| 改进项 | 状态 |
|--------|------|
| render.py → MP3/FLAC 渲染 | ✅ |
| render.py → 静音检测+自动重试 | ✅ |
| render.py → 渲染前预检 | ✅ |
| exceptions.py → 新增 7 个异常类型 | ✅ |
| bus.py → VCA 实际实现 | ✅ |
| bus.py → mute/solo/color | ✅ |
| track.py → 相位反转 | ✅ |
| audio_utils.py → DC 偏移检测/消除 | ✅ |
| signal.py → K-weighting 升级到 BS.1770-4 二阶 | ✅ |
| signal.py → True Peak 用 scipy 优化 | ✅ |
| bridge.py → undo_block 上下文管理器 | ✅ |
| bridge.py → 弹窗模式补全 | ✅ |
| 9 个流派 Profile | ✅ |
| CI/CD GitHub Actions | ✅ |

### 5.3 高覆盖率模块（≥95%）

genre_tables (100%), dialog_handler (100%), spatial_engine (100%), spectrum (100%), profiles (100%), preview (99%), backing (99%), progress (99%), reference (98%), plugin_registry (98%), comp_engine (98%), normalize (98%), audit (97%), loudness_optimizer (97%)

---

## 六、🟡 其他问题清单

### 死代码

| 文件 | 死代码 |
|------|--------|
| engine.py L3237-3343 | `_finalize_master_impl` 不可达 |
| signal.py | `_to_mono()` 无外部调用 |
| signal.py | `_biquad_hp()` 已被 scipy 替代 |
| engine.py 6处 | `from hermes_core.eq_engine import _apply_proq3_eq` 冗余 |

### 命名冲突

- `ConnectionError` 遮盖 Python 内置 → 建议 `HermesConnectionError`
- `TimeoutError` 遮盖 Python 内置 → 建议 `HermesTimeoutError`

### 脆弱性

- MP3 渲染用硬编码 base64 blob → REAPER 版本更新可能失效
- security.py `sanitize_filename()` 剥离中文字符（`\w` 默认不含 CJK）
- 5 个调用点仍用 `SignalAnalyzer._read_pcm()` 而非 `audio_utils.read_pcm()`
- 审计日志仅内存存储 → 崩溃丢失

### 覆盖率低洼

| 模块 | 覆盖率 | 原因 |
|------|--------|------|
| engine.py | **35%** | 仍有 3743 行，核心方法未充分测试 |
| cli.py | **32%** | 子命令执行逻辑未测试 |
| gain_staging.py | **35%** | 核心方法未被调用（engine.py 用自己的副本） |
| mastering.py | **49%** | `finalize()` 主路径未测试 |

---

## 七、优先修复行动清单

### 🔴 P0 — 必须立即修复（运行崩溃）

| # | 问题 | 工作量 |
|---|------|--------|
| 1 | agent_protocol.py 调用不存在的 6 个引擎方法 | 半天 |
| 2 | dialog_handler.py 模块级 import 非目标平台崩溃 | 15分钟 |
| 3 | mastering.py `_friendly_hint` 返回值截断 | 5分钟 |
| 4 | engine.py 缺少 `from pathlib import Path` | 1分钟 |

### 🟠 P1 — 应尽快修复（代码质量）

| # | 问题 | 工作量 |
|---|------|--------|
| 5 | engine.py 二次清理 — 删除已提取到新模块的重复代码 | 1-2天 |
| 6 | 接入闲置模块(plugin_registry, backing, preview, reference) | 1天 |
| 7 | dialog_handler.py 接入 bridge.py 替换内联 AppleScript | 半天 |
| 8 | 删除 signal.py 死代码 | 15分钟 |
| 9 | `_read_pcm()` 迁移 — 5 个调用点改用 audio_utils | 30分钟 |
| 10 | engine.py 删除 6 处冗余内联 import | 10分钟 |
| 11 | engine.py 删除死代码 `_finalize_master_impl` | 5分钟 |

### 🟡 P2 — 建议修复

| # | 问题 | 工作量 |
|---|------|--------|
| 12 | ConnectionError/TimeoutError 重命名 | 30分钟 |
| 13 | security.py sanitize_filename 支持中文 | 15分钟 |
| 14 | audit.py 增加自动持久化 | 1小时 |
| 15 | CI/CD 补充 mypy | 10分钟 |
| 16 | mkdocs 补充缺失页面 | 1天 |
| 17 | Profile YAML 增加 De-Esser 和空间效果配置 | 半天 |
| 18 | gain_staging.py 委托剩余核心方法 | 1小时 |

---

## 八、总结

### ✅ 本次重构做到了

- 成功新增 15 个模块，覆盖分析报告中指出的功能缺失
- 测试增长 58%，1270 个测试全部通过
- Profile 从 1 个增长到 9 个
- MP3/FLAC 渲染、K-weighting 升级、VCA 实现等多项改进
- CI/CD 已建立

### ❌ 本次重构没做到

- **engine.py 仍是 God Object**（3743 行），仅缩减 16%，目标是 80%+
- **新代码是"复制"不是"移动"** — engine.py 保留大量重复代码
- **9 个新模块未接入系统** — 写了但没用上
- **agent_protocol 调用不存在的方法** — 会在生产环境崩溃
- **部分死代码未清理**

### 建议的下一步

1. **紧急修复 4 个 P0 Bug**（预计 1 天）
2. **engine.py 二次清理** — 删除重复代码，让 engine.py 真正瘦身（预计 1-2 天）
3. **接入闲置模块** — plugin_registry, backing, preview, reference, dialog_handler（预计 1 天）
4. **在 engine.py 中实现 adjust 系列方法** — 让 agent_protocol 能正常工作（预计半天）
