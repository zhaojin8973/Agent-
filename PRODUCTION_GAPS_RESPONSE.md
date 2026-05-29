# PRODUCTION_GAPS 逐条核实报告

> **来源**: PRODUCTION_GAPS.md（外部落地差距分析）
> **核实日期**: 2026-05-29
> **核实方法**: 逐条对照当前仓库实际代码确认

---

## 总体评价

这份分析**准确率极高**。与 CODE_REVIEW 不同，它关注的是"缺失的能力"而非代码质量问题，因此几乎不存在基于错误理解的情况。28 条评估中，**除 2 条已被本次 CODE_REVIEW 修复部分解决外，其余全部确认属实**。

核心结论完全同意：**代码骨架成型，但本质上是作者本人单机原型**。

---

## 🔴 一、操作安全性 — 全部确认

### 1. 不支持幂等 — ✅ 确认

**核实**：

- `prepare_stems()`（engine.py:285-427）：每次调用都会基于当前 RMS 计算 clip gain 并施加。第二次调用时音频已被第一次修改 → gain 叠加。无任何守卫。
- `finalize_master()`（engine.py:590-697）：直接 `add_master(limiter_fx)`（第 616 行），不检查 master 上是否已有 limiter → 重复执行会挂第二个 Pro-L 2。
- `import_stems()`（engine.py:230-247）：每次调用 `self._tracks.create()` → 产生重复音轨。

**评价**：这是落地头号障碍。建议的两种方案（`_stems_prepared` 标记 或 跳过已非 0 dB 的 clip gain）都合理。推荐前者——更明确、不会产生意外行为。

### 2. 完全没有使用 REAPER Undo 系统 — ✅ 确认

**核实**：全文搜索 `Undo_BeginBlock` / `Undo_EndBlock` / `Undo_` → 零调用。唯一的回退手段是 `save_checkpoint()`（保存整个 .rpp 快照），需要关闭工程重新加载。

**补充**：当前 `save_checkpoint()` 是**手动快照**，不是自动触发。用户必须记得在执行每个操作前调用它——这在实践中几乎不会发生。

### 3. 操作中断无恢复 — ✅ 确认

**核实**：

| 场景 | 当前代码 | 确认 |
|------|---------|------|
| REAPER 渲染崩溃 | render.py 轮询超时返回 error dict（第 155-157 行），无重试 | ✅ |
| FX 操作挂起 | 无超时机制（只有 render 有 timeout 参数） | ✅ |
| Python 进程 crash | bridge.py 的 `_ui_locked` 是简单 bool（第 393 行），`unlock_ui()` 的 `finally` 不可靠（进程 crash 时 finally 不执行） | ✅ |
| 网络断开 | `ensure_connected()` 无重试机制（第 419-427 行，简单重连一次） | ✅ |

**补充**：建议的 `atexit` 钩子是**最低成本**的改进。`_ui_locked` 不仅应改为计数（支持嵌套 lock），还应在 crash 时通过 `atexit` 或信号处理器恢复。

---

## 🔴 二、插件依赖 — 全部确认

### 硬编码插件链 — ✅ 确认

**核实**：

```python
# engine.py:594
limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)"

# engine.py:468
reverb_fx: str = "ReaVerbate"
```

Pro-L 2 参数归一化公式硬编码（engine.py:47, 624, 665）：
```python
_PRO_L2_RANGE_DB: float = 30.0
ceiling_norm = (ceiling_db + _PRO_L2_RANGE_DB) / _PRO_L2_RANGE_DB
gain_norm = gain_db / _PRO_L2_RANGE_DB
```

**补充风险**：不仅换插件要改源码，**同一插件的不同版本也可能崩溃**。Pro-L 2 v1.x 和 v2.x 的参数映射可能不同。

### 无安装检测 — ✅ 确认

**核实**：`add_fx` 返回 -1 时（fx.py:169），`finalize_master` 调用 `_master_error()` 返回错误 dict（engine.py:618-621）。这个处理是**存在的**，但问题是：
- 检测发生在**运行时**而非**预检阶段**
- `set_param` 失败只返回 `False`（engine.py:627-631），不会阻塞后续操作

**评价**：错误处理比文档描述的好（有 `_master_error` 错误返回），但缺少预检。建议的 `preflight_plugins()` 确实必要——在开始操作前一次性验证所有依赖。

---

## 🟠 三、用户体验 — 全部确认

### CLI / 配置 / README — ✅ 确认

**核实**：
- 无 CLI 入口（无 `__main__.py`，无 `[project.scripts]`）
- 无配置文件支持（所有参数通过 Python 函数参数传递）
- 无 README.md（项目根目录确认不存在）
- 无批处理能力（一次只能处理一个工程）

**评价**：最低可用 CLI（`hermes vocal-mix --vocal ... --backing ...`）是当前最该优先实现的功能入口。`PROJECT_STATUS.md` 里列的所有场景（Scene 1-9）都可以映射为 CLI 子命令。

---

## 🟠 四、文档 — 全部确认

**核实**：

| 文档 | 状态 | 补充 |
|------|------|------|
| README.md | ❌ | - |
| 安装指南 | ❌ | Python 3.14 不兼容、REAPER 7.73、reapy connect 流程都需要文档化 |
| 快速开始 | ❌ | - |
| API 文档 | ❌ | docstring 存在但零散，无自动生成 |
| 架构文档 | ✅ | PROJECT_STATUS.md 足够详细 |
| 故障排除 | ❌ | ARM64 元组 bug、对话框弹窗、float WAV 兼容都是真实踩坑点 |
| 示例脚本 | ❌ | - |

**评价**：README.md 是**真正的 P0**。没有它，其他所有改动对潜在用户都没有意义。

---

## 🟡 五、环境鲁棒性 — 全部确认

### 1. 项目状态感知 — ✅ 确认

**核实**：`self._project_path`（engine.py:83）存储了当前项目路径，但**从未在后续操作中验证**。`get_project_info()` 能读取当前 REAPER 项目名（第 212-228 行），但没有任何方法调用它做校验。

**补充**：除了项目切换，还有一个更隐蔽的问题——REAPER 可能在操作中途被用户意外修改（误触快捷键、手动拖拽轨道等）。Undo block（#2）可以部分缓解这个问题。

### 2. REAPER 版本兼容性 — ✅ 确认

**核实**：`health_check()` 记录版本号但不警告（bridge.py:441）。PROJECT_STATUS.md 已知 Python 3.14 不兼容、REAPER 7.73 是测试版本。

### 3. 磁盘空间 — ✅ 确认

**核实**：无检查。render.py 的 `_check_output_writable()` 只检查目录可写性（第 48-58 行），不检查空间。

---

## 🟡 六、音频处理边界 — 大部分确认，2 项已解决

| 场景 | 原状态 | 核实 | 当前状态 |
|------|--------|------|---------|
| 非 WAV 格式（MP3/FLAC） | signal.py 崩溃 | **⚠️ 已解决** | `_read_pcm` 已改用 `soundfile`，支持 MP3/FLAC/AIFF 等所有 libsndfile 格式 |
| Mono + Stereo | LUFS 异常 | **⚠️ 已解决** | `_read_pcm` 将 mono reshape 为 2D（`data.reshape(-1, 1)`），下游处理一致 |
| 44.1kHz 采样率 | LUFS 不准确 | ✅ 确认 | 但 CODE_REVIEW #1 的修复（双线性变换）已解决。文档中可移除此项 |
| 混合采样率 | 静默混合 | ✅ 确认 | REAPER 项目级采样率统一，但导入的媒体文件可能有不同采样率 |
| 超长音频 >30min | 内存溢出 | ✅ 确认 | `_compute_true_peak`（np.convolve）和 `_compute_lufs`（全量加载）均为全内存操作 |
| 空白/无声音频 | LUFS = -inf | ✅ 确认 | 已有处理（pcm.size==0 返回 -200），但二分搜索的 `probe_lufs < -70` 检查不够显式 |
| DC offset | 影响准确性 | ✅ 确认 | 无 DC 移除 |

---

## 🟢 七、打包发布 — 全部确认（部分已修复）

**核实**：CODE_REVIEW 修复已补全 `dependencies`（soundfile, pyloudnorm）。剩余缺失：

- `license`
- `authors`
- `readme = "README.md"`（需先创建 README）
- `classifiers`
- `[project.scripts]`（需先实现 CLI）
- `[project.optional-dependencies]` 缺 `dev`（ruff, mypy）

---

## 📋 落地路线图评估

文档提出的四阶段路线图**整体合理**，但从本次两轮审查的上下文来看，建议调整优先级：

### 建议调整

| 原阶段 | 调整 | 理由 |
|--------|------|------|
| Phase 1 幂等守卫 | **维持 P0** | 没有这个，项目无法被任何人（包括作者自己）重复使用 |
| Phase 1 Undo Block | **升为 P0** | 与幂等性并列——一个防"重复执行"，一个防"执行错误后无法回退" |
| Phase 1 atexit 清理 | **维持 P0** | Python crash 导致 REAPER UI 冻结是灾难性的——作者自己也会踩 |
| Phase 1 插件预检 | **维持 P1** | 非破坏性改进，但大大减少"跑到一半失败"的概率 |
| Phase 1 README | **升为 P0** | 项目门面。CODE_REVIEW 修复已做了大量工程改进，没有 README 别人不知道 |
| Phase 2 MixingProfile | **维持 P1** | 从零到一的价值巨大，但依赖 Phase 1 的基础安全 |
| Phase 2 CLI | **升为 P1** | 与 MixingProfile 并列——一个是"配什么"，一个是"怎么跑"。CLI + 配置 = 项目从库变成工具 |
| Phase 3 RPR 超时/重连 | **维持 P2** | 对稳定性重要，但使用频率低于 Phase 1/2 |
| Phase 3 非 WAV 支持 | **降为 P3** | soundfile 已解决大部分问题 |
| Phase 3 多采样率 LUFS | **降为 P4** | 双线性变换已解决 |

### 修正路线图

**Phase 1: 基础安全（1-2 周）** — 让项目不会弄坏工程
- [ ] 幂等性守卫
- [ ] REAPER Undo Block
- [ ] `atexit` UI 清理钩子（chained unlock）
- [ ] README.md + 安装指南

**Phase 2: 从库到工具（1-2 周）** — 让别人能用
- [ ] CLI 入口（`hermes vocal-mix`）
- [ ] MixingProfile YAML 配置
- [ ] 插件预检
- [ ] 快速开始 + 示例脚本

**Phase 3: 生产健壮（2 周）**
- [ ] 项目状态校验
- [ ] RPR 调用超时 + 断线重连
- [ ] 渲染失败重试
- [ ] 磁盘空间预检
- [ ] 错误信息人性化

**Phase 4: 完善（后续）**
- [ ] 批处理
- [ ] API 文档
- [ ] DC offset 移除
- [ ] 分块处理大文件
- [ ] 进度回调

---

## 总结

PRODUCTION_GAPS 是一份**高质量的生产落地分析**，抓住了从"原型"到"工具"的关键差距。

**核心三件事**（最应该优先做的）：

1. **幂等性 + Undo**：没有这两项，任何操作错误都会导致工程报废。这是从"开发中"到"可以自己用"的门槛。
2. **README.md**：没有它，CODE_REVIEW 的所有代码改进对别人都没有意义。
3. **CLI + 配置**：当前"写 Python 来用"的模式限制了所有非开发者用户（包括作者自己一个月后忘记 API）。

三个 P0 项预计工作量约 2-3 天，但能从根本上改变项目的可使用性。
