# Plan: Hermes 混音引擎重启

**来源**: CTO 指令 — Hermes 验收测试暴露大量落地问题后决定精简重做
**复杂度**: 大型
**日期**: 2026-05-24

## 摘要

旧 Hermes 项目 51 个源文件、1253 个测试看似完整，但 Hermes 验收测试（10 场景）暴露了**架构设计与实际落地之间的巨大鸿沟**。核心问题不是代码 bug，而是大量 Phase 7 高级子系统（MemoryDaemon、SkillHarvester、PostAuditor、IntentTranslator）在真实 REAPER 环境中从未被真正串联验证过。

本次重启策略：**从 REAPER 数据出发，只保留经过真实环境验证的底层模块，在新文件夹中逐步构建一个真正能跑通的精简版 Hermes。**

## 旧项目资产盘点

| 类别 | 模块 | 行数 | 独立评分 | 处置 |
|------|------|:----:|:--------:|------|
| **保留提取** | `bridge.py` | 834 | 5/5 | 零内部依赖，直接迁移 |
| **保留提取** | `bus.py` | 322 | 5/5 | 纯文件夹深度操作，自包含 |
| **保留提取** | `fx.py` | 369 | 4/5 | 仅依赖 bridge，核心 20% 约 120 行 |
| **参考提取** | `track.py` | 440 | 3/5 | 仅依赖 bridge，精简后迁移 |
| **参考提取** | `send.py` | 403 | 3/5 | 依赖 bridge+fx，核心 20% 约 100 行 |
| **重写参考** | `render.py` | 648 | 1/5 | 深度耦合 6 模块，架构参考 |
| **重写参考** | `signal.py` | 314 | — | 精简版信号分析 |
| **全部废弃** | 其余 44 个源文件 | ~18,000 | — | Phase 7 子系统、意图翻译、语义层、推理网关、插件协议等 |
| **全部废弃** | 1253 个测试 | — | — | 与新架构不兼容 |
| **保留参考** | `Hermes 测试/` 音频素材 | — | — | 真实多轨素材 |
| **保留参考** | HERMES_ACCEPTANCE_PLAN.md | — | — | 10 个场景作为功能目标 |

## 已学习的模式

| 类别 | 来源 | 模式 |
|------|------|------|
| 依赖注入 | `fx.py, bus.py, send.py` | 构造函数接收 `bridge: ReaperBridge`，全部 REAPER 操作通过 `self._bridge.api` |
| 命名 | `bridge.py:1-834` | `ReaperBridge` 类，方法 `snake_case`，常量 `UPPER_SNAKE` |
| 错误处理 | `bridge.py:_get_reapy()` | 延迟导入 + RuntimeError + 明确错误消息 |
| 日志 | 全部模块 | stdlib logging，模块级 logger |
| 测试 | `tests/test_phase3.py` | pytest + AAA 模式 + E2E REAPER 集成 |
| REAPER API | `bridge.py:connect()` | reapy 必须在 REAPER 启动后才能导入 |
| 模态死锁 | `bridge.py:DialogKiller` | 后台线程轮询杀对话框，OS SIGTERM 退出 |

## 新项目模块规划

```
hermes-core/
├── pyproject.toml
└── src/hermes_core/
    ├── __init__.py
    ├── bridge.py       # Phase 2 — REAPER 进程桥接（从旧项目提取）
    ├── track.py        # Phase 3 — 轨道 CRUD + 媒体导入
    ├── bus.py          # Phase 3 — 文件夹总线管理
    ├── fx.py           # Phase 4 — 效果器链（EQ/压缩/混响插件操作）
    ├── send.py         # Phase 4 — 发送/返送/并行压缩
    ├── render.py       # Phase 5 — 混音渲染
    ├── signal.py       # Phase 5 — 信号分析（RMS/Peak/LUFS）
    └── engine.py       # Phase 6 — MixingEngine 顶层入口
```

**总计 8 个源文件**，覆盖 Hermes 验收 10 场景的全部核心能力。

## 模块对应验收场景

| 场景 | 需要的模块 |
|------|-----------|
| 1. 健康检查 | bridge |
| 2. 工程与轨道 | bridge, track |
| 3. 增益分级 | bridge, track, engine |
| 4. 效果器 (EQ) | bridge, fx |
| 5. 总线与混响发送 | bridge, bus, send, fx |
| 6. 渲染与信号分析 | bridge, render, signal |
| 7. 意图翻译 | 本次不做，Phase 2 架构另案 |
| 8. 端到端混音 | bridge, track, bus, fx, send, render, signal, engine |
| 9. 安全审计 | signal |
| 10. 记忆存储 | 本次不做，Phase 2 架构另案 |

## Agent 编排方案

本次开发采用 ECC 多智能体协作模式：

```
Phase 0: REAPER 数据采集
  └── Claude Code (主控) 直接在 REAPER 中运行诊断脚本

Phase 1: 架构设计
  ├── architect agent      → 系统架构设计
  └── Claude Code (主控)   → 审核架构，签核

Phase 2-5: 逐模块实现 (每个模块独立循环)
  ├── tdd-guide agent      → 先写测试
  ├── Claude Code (主控)   → 实现代码
  ├── python-reviewer      → 代码审查
  └── Claude Code (主控)   → 在 REAPER 中验证

Phase 6: 端到端集成
  ├── e2e-runner agent     → 自动化验收测试
  ├── security-reviewer    → 安全检查
  └── Claude Code (主控)   → 最终签核
```

**编排原则：**
- Claude Code 始终是主控（orchestrator），负责决策和签核
- 专业 agent 负责特定领域的分析和审查
- 每个 agent 输出结果后，Claude Code 审核再进入下一步
- 绝不自动执行 agent 建议 — 始终需要 CTO 确认

## 任务

### Phase 0: REAPER 数据采集与诊断

**目标**: 在动手写代码之前，充分了解 REAPER 7.73 ARM64 的实际 API 行为。**重点验证第三方插件（FabFilter/Waves/Valhalla 等）的参数操作可靠性**——旧项目 ReaEQ 能挂上但参数写不进去，这个问题在第三方插件上必须彻底搞清楚。

- **任务 0.1**: REAPER 基础 API 诊断：
  - `TrackFX_AddByName` — 是否可靠返回正确的 FX index
  - `TrackFX_GetCount` / `TrackFX_GetFXName` — FX 插入后能否立即读到
  - `CalculateNormalization` — 返回值类型（float vs tuple）
  - `InsertMedia` — item 插入位置行为
  - `GetSetProjectInfo_String("RENDER_STATS")` — 确认会触发模态对话框
  - `Main_SaveProject` — 未命名项目行为
  - `GetTrackEnvelopeByName` — NULL 返回格式
  - `CSurf_OnVolumeChange` — 包络创建可靠性
  - `DeleteEnvelopePointRange` — ARM64 残留 point 问题
  - `I_FOLDERDEPTH` — 文件夹深度行为
  - `CreateTrackSend` / `SetTrackSendInfo_Value` — 发送创建和参数设置
- **任务 0.2**: **第三方插件参数读写诊断（首批 6 款核心插件）**：

  优先验证以下插件，覆盖 EQ / 压缩 / 限制 / 混响四类：

  | 插件 | 类型 | 厂商 | 格式 |
  |------|------|------|------|
  | Pro-Q 3 | EQ | FabFilter | VST3 |
  | Pro-L 2 | Limiter | FabFilter | VST3 |
  | RVox | Compressor | Waves | VST3 |
  | CLA-76 (1176) | Compressor | Waves | VST3 |
  | L4 (MaxxVolume) | Limiter | Waves | VST3 |
  | VintageVerb | Reverb | Valhalla | VST3 |

  对每款插件执行：
  - 用 `TrackFX_AddByName` 添加 → 确认返回正确 index
  - `TrackFX_GetNumParams` → 获取参数数量
  - 遍历 `TrackFX_GetParamName` → 确认所有参数名可读
  - **`SetParamNormalized → GetParamNormalized` 回读** → 选 3-5 个关键参数（如 Pro-Q 3 的 freq/gain/Q，RVox 的 threshold/ratio），设置后立即回读，验证一致
  - 工程关闭→重开后参数索引稳定性
- **任务 0.3**: **reapy 路径的第三方插件操作**：
  - 通过 `reapy.Project.tracks[].add_fx()` 添加第三方插件
  - 通过 reapy 的 `fx.params[]` 设置参数并回读验证
  - 对比 RPR API 路径 vs reapy 路径的可靠性差异
- **任务 0.4**: 验证 `reapy` 导入时序问题的复现条件
- **任务 0.5**: 编写 `_extract_reaper_string()` 对各种 REAPER 返回字符串的解析测试
- **任务 0.6**: 输出诊断报告，明确：
  - 哪些 API 路径对第三方插件可靠
  - 哪些插件格式（VST3/AU）参数可读写
  - 推荐的新架构 FX 操作策略
- **验证**: 至少 3 款第三方插件的参数在 set→get 回读中一致

### Phase 1: 新项目架构设计

- **任务 1.1**: 基于 Phase 0 诊断报告，设计精简版 Hermes 架构（最多 3 层）
- **任务 1.2**: 定义 8 个模块的公开 API 接口（不写实现）
- **任务 1.3**: 确定模块依赖图（零循环依赖；send.py 不再懒加载 fx.py）
- **任务 1.4**: CTO 签核架构
- **验证**: 架构图清晰，所有模块职责单一，依赖无环

### Phase 2: 底层桥接层

**模块**: `bridge.py`

- **任务 2.1**: 从旧项目提取 bridge.py，精简冗余代码
- **任务 2.2**: 修复已知 bug（`_extract_reaper_string` 去重、`reapy` 导入安全）
- **任务 2.3**: TDD 编写 bridge 测试
- **验证**: 健康检查通过，零模态对话框，进程启动/退出可靠

### Phase 3: 轨道与总线层

**模块**: `track.py`, `bus.py`

- **任务 3.1**: 从旧项目提取 bus.py，精简到核心 20%（约 90 行）
- **任务 3.2**: 重写 track.py，精简到核心（约 130 行）
- **任务 3.3**: 实现 import_media 并按旧验收标准的 5 项检查逐项验证：
  - 轨道名来自源文件 basename
  - 每条轨 item_count == 1
  - D_POSITION == 0.0
  - item 媒体源文件名与轨道源文件一致
  - 同一源文件不得残留在其他轨道
- **任务 3.4**: TDD 编写 track + bus 测试
- **验证**: 场景 2 全部通过标准满足

### Phase 4: 效果器与发送层

**模块**: `fx.py`, `send.py`

**设计原则**: FX 层必须插件无关——不硬编码任何厂商的参数索引或曲线。所有插件操作走统一的 `add/set_param/get_param` 接口，参数语义由上层调用者定义。

**首批支持**: Pro-Q 3 / Pro-L 2 (FabFilter) + RVox / CLA-76 / L4 (Waves) + VintageVerb (Valhalla)。后续按需扩展。

- **任务 4.1**: 从旧项目提取 fx.py 核心，去除 ReaEQ 特化方法（`add_eq_band`/`set_eq_band` 等硬编码 band 索引的），只保留通用的：
  - `add(track_index, fx_name) -> int` — 添加插件（支持 VST3/AU/VST2）
  - `remove(track_index, fx_index)`
  - `set_param(track_idx, fx_idx, param_idx, normalized_value)` — **写后必须回读验证**
  - `get_param(track_idx, fx_idx, param_idx) -> float`
  - `set_enabled(track_idx, fx_idx, enabled)`
  - `get_chain(track_index) -> list[dict]`
  - `get_param_list(track_idx, fx_idx) -> list[dict]` — 枚举所有参数名和当前值
  - 内置 `_fx_exists_at_index()` 假阳性检测
  - reapy 回退路径（当 RPR API 不可靠时）
- **任务 4.2**: 编写第三方插件参数映射层（独立文件或 fx.py 内的辅助类）：
  - `PluginParamMap` — 按插件名+格式查找参数索引映射
  - 支持从 REAPER preset/state chunk 中提取参数名→索引映射
  - 不硬编码厂商参数，而是运行时从插件读取
- **任务 4.3**: 从旧项目提取 send.py 核心，约 100 行
- **任务 4.4**: 解决 send.py → fx.py 循环依赖：send 通过构造函数接收 fx_manager
- **任务 4.5**: TDD 编写 fx + send 测试，**重点测试 set_param→get_param 回读一致性**
- **验证**: 
  - 至少 1 款 EQ（FabFilter Pro-Q 3 或等价）能挂上 + 参数写读一致 + A/B bypass 可用
  - 至少 1 款混响（Valhalla 或等价）能作为 aux return 挂上 + send level 正确
  - 场景 4 和场景 5 全部通过标准满足

### Phase 5: 渲染与信号分析层

**模块**: `render.py`, `signal.py`

- **任务 5.1**: 重写 render.py（解耦安全层，用注入替代硬导入）
- **任务 5.2**: 精简 signal.py（只保留 RMS/Peak/LUFS/Clip/Silence）
- **任务 5.3**: TDD 编写测试
- **验证**: 渲染输出有效 wav，integrated_lufs 有效，silence_passed 为 True

### Phase 6: 顶层入口 + 端到端验收

**模块**: `engine.py`, 全部 10 场景验收

- **任务 6.1**: 编写 MixingEngine 顶层入口，组装全部 7 个模块
- **任务 6.2**: 按旧 HERMES_ACCEPTANCE_PLAN.md 的 10 个场景逐项验收（场景 7/10 明确标记为"下阶段"）
- **任务 6.3**: 记录每个场景的 PASS/FAIL 和根因
- **验证**: 至少 8/10 场景 PASS

## 验证

```bash
# 新项目测试套件
cd /Users/zhaojin/hermes-core
python3 -m pytest -q

# REAPER 集成测试
python3 -m pytest -q -m reaper_integration

# 验收场景（在 REAPER 运行中）
python3 -m pytest -q -m acceptance
```

## 风险

| 风险 | 可能性 | 缓解措施 |
|------|--------|---------|
| REAPER ARM64 API 行为与文档不一致 | 高 | Phase 0 诊断脚本全覆盖 |
| **第三方插件参数 set→get 不一致**（旧项目核心 bug） | **高** | Phase 0.2 逐插件验证，fx.set_param() 内置回读断言 |
| TrackFX_AddByName 假阳性（返回 index 但未创建） | 高 | Phase 0 诊断 + fx.add() 内置 `_fx_exists_at_index()` |
| VST3 vs AU vs VST2 的 AddByName 名称格式不统一 | 高 | Phase 0.2 列出所有已安装插件的精确 `FXName` 字符串 |
| reapy 桥接在长时间运行中断连 | 中 | bridge 加重连心跳机制 |
| send.py ↔ fx.py 循环依赖 | 低 | 构造函数注入 fx_manager |
| 范围蔓延（又回到 51 个文件） | 高 | 硬上限 8 个源文件，CTO 每个 Phase 签核 |

## 验收标准

- [ ] 新项目源文件 = 8 个
- [ ] 所有测试在 REAPER 运行中通过
- [ ] 10 个 Hermes 验收场景至少 8 个 PASS（场景 7 意图翻译、场景 10 记忆存储标记为下阶段）
- [ ] 零模态对话框阻塞
- [ ] 零循环依赖
- [ ] CTO 签核每个 Phase
