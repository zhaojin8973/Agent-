---
name: project-status-report
description: "Comprehensive project status — implemented features, gaps, test coverage, and next steps for hermes-core"
metadata: 
  node_type: memory
  type: project
  originSessionId: c5448f8f-79ec-422c-ac06-b983660a36d1
---

# Hermes-Core 项目阶段性报告

**更新日期**: 2026-05-28
**版本**: 0.2.2
**最新提交**: (pending)

---

## 一、项目概述

hermes-core 是 REAPER DAW 的精益三层 Python 自动化引擎，目标是非交互式、无界面的混音工作流。通过 `python-reapy` 连接 REAPER ReaScript API。

**技术栈**: Python >=3.11, <3.14 | python-reapy >=0.10 | numpy >=1.24

---

## 二、架构：三层设计

| 层 | 模块 | 职责 |
|---|---|---|
| L1 桥接 | `bridge.py` | REAPER 连接、原始 API、UI 抑制、弹窗守护 |
| L2 领域 | `track.py` `bus.py` `fx.py` `send.py` `render.py` `signal.py` | 各领域管理器，仅依赖 L1 |
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
- [x] **Clip gain 支持** — `set_item_volume()` 通过 `SetMediaItemInfo_Value`
- [x] **绝对路径导入** — `os.path.abspath` 防止 REAPER 离线
- [x] **Float/24-bit WAV 转 PCM** — `_convert_to_pcm()` 兼容所有 WAV 格式

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
- [x] **Master track FX 支持** — `add_master()`, `get_param_list()` 适配

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
- [x] **RENDER_FORMAT base64 编码修复**

### L2: signal.py
- [x] WAV 读取（16/24-bit PCM + 32-bit float）
- [x] RMS / 峰值 dBFS
- [x] 集成 LUFS（ITU-R BS.1770-4，含 K-加权+双门限）
- [x] 真峰值 dBTP（4x 过采样 sinc 插值）
- [x] 削波检测 + 静音检测
- [x] **立体声功率保持下混** — RMS/LUFS 逐通道计算，消除 ~2dB 偏差
- [x] **`_to_mono()` 辅助函数** — 标准 (L+R)/2 下混

### ~~L2: normalize.py~~ — 已删除
- 被 `prepare_stems` + `finalize_master` 取代，功能更精确（clip gain + RMS 两趟法）

### L3: engine.py
- [x] 场景 1: 连接与健康检查
- [x] 场景 2: **工程管理** — create_project(name, output_dir) 自动保存 .rpp、时间戳冲突规避、save_project() 静默保存、save_checkpoint() 检查点快照、get_project_info()
- [x] 导入 stems
- [x] **场景 3: prepare_stems()** — 两段式增益分级：clip gain (-18 dBFS RMS 参考) + 推子曲风平衡
- [x] 场景 4: FX 添加与查询
- [x] 场景 5: 总线创建 + 混响发送
- [x] 场景 6: 渲染混音（含信号分析验证）
- [x] **场景 8: finalize_master()** — **P90 分段 RMS 两趟法**母带（探针渲染 → P90 分段 RMS → 算 Gain → 成品渲染），防止动态大时副歌被压爆
- [x] **场景 8: add_master_fx()** — Master 总线 FX 挂载
- [x] 场景 9: 混音安全审计
- [x] **apply_gain(target="clip_gain")** — clip gain 已实现
- [x] **Peak guard** — clip gain 不会推到 0 dBFS 以上
- [x] **曲风表** — folk/pop/chinese_folk_bel_canto 伴奏压低量

---

## 四、未实现/存根

| 功能 | 位置 | 状态 |
|---|---|---|
| `apply_gain(target="master_fader")` | engine.py | NotImplementedError |
| `check_headroom()` 有效实现 | engine.py | 返回 "unavailable_without_render" 存根 |
| DialogKiller 跨平台 | bridge.py | 仅 macOS AppleScript |

---

## 五、测试覆盖

### 单元测试

| 文件 | 测试数 |
|---|---|
| test_bridge.py | 33 |
| test_bus.py | 16 |
| test_engine.py | 41 |
| test_fx.py | 46 |
| test_render.py | 53 |
| test_send.py | 22 |
| test_signal.py | 21 |
| test_track.py | 34 |
| **小计** | **266** |

### 集成测试

| 文件 | 测试数 | 内容 |
|---|---|---|
| test_mixing_workflow.py::TestMixingWorkflow | 8 | 分轨混音全流程 |
| test_mixing_workflow.py::TestVocalMixing | 8 | 贴唱混音（望归）|
| test_render.py (集成) | 3 | 渲染集成 |
| **小计** | **19** |

### 总计

| 指标 | 值 |
|---|---|
| 单元测试 | 238 |
| 集成测试 | 19 |
| **总计** | **257** |
| Engine 覆盖率 | 82% |

---

## 六、贴唱混音管线

```
prepare_stems (clip gain -18dBFS RMS + 曲风推子)
  → Pro-Q 3 EQ
  → RVox 压缩
  → ValhallaVintageVerb 混响发送
  → save_checkpoint
  → finalize_master (Pro-L 2 RMS 两趟法)
  → render + audit
```

### 插件链

| 位置 | 插件 |
|---|---|
| 人声 | FabFilter Pro-Q 3 → Waves RVox |
| 混响发送 | ValhallaVintageVerb (post-fader) |
| Master | FabFilter Pro-L 2 (Gain=P90动态, Output Level=-0.5 dB) |

### 默认参考值

| 参数 | 默认值 |
|---|---|
| Clip gain 参考 | -18 dBFS RMS (0 VU) |
| 母带目标 RMS | -12 dBFS（响段 P90 参考） |
| Limiter Ceiling | -0.5 dBTP |
| 民美 backing 压低 | 9-12 LU |

---

## 七、已知问题

1. **REAPER Python 兼容性**：Python 3.14 不兼容 REAPER 7.73，必须使用 3.13
2. **DialogKiller 仅 macOS**：依赖 AppleScript，不支持 Windows/Linux
3. **target_rms_db 默认值**（-12 dBFS）和曲风表参数需要听感验证
4. **ARM64 REAPER API 元组问题**：`TrackFX_GetParamName`/`TrackFX_GetParam` 返回 6 元素元组，需安全解包。已在 fx.py 中统一处理。
5. **动态范围大的素材**：P90 分段 RMS 策略可防止响段被压爆，但整体 RMS 可能低于目标。符合预期——待听感验证。

---

## 八、下一步可做

### 优先级高
- [ ] 实现 `apply_gain` 的 master_fader 支持
- [ ] 实现 `check_headroom()` — 渲染后分析真峰值余量
- [ ] target_rms_db 默认值根据实际监听调整
- [ ] 曲风表监听验证

### 优先级中
- [ ] 各 L2 模块增加真实 REAPER 集成测试
- [ ] 曲风表验证与调整
- [ ] render.py 增加渲染进度反馈

### 优先级低
- [ ] DialogKiller 跨平台支持
- [ ] CI/CD pipeline 搭建
- [ ] 降噪/去嘶声等专用处理模块

---

## 九、环境依赖

- **REAPER**: 7.73 (arm64)
- **Python**: 3.13.12 (Homebrew) — 3.14 不兼容！
- **reapy**: 0.10.0
- **numpy**: 已安装
- **reaper.ini**: 已修复 `pythonlibpath64` 指向 Python 3.13

---

## 更新日志

| 日期 | 变更 |
|---|---|
| 2026-05-28 | **P90 分段 RMS 母带**：`segment_rms()` 窗口化百分位测量替代整体 RMS，防止动态大的素材副歌被压爆。`finalize_master` 新增 `percentile` 参数（默认 90）。pass 校验改为最终输出 P90 RMS vs 目标。结果新增 `measured_rms_db`。8/8 TestVocalMixing 通过。 |
| 2026-05-28 | Pro-L 2 参数校准：ARM64 元组安全解包 + REAPER GUI 实测归一化公式（Gain 0→+30, Output Level -30→0）。`set_param()` 返回 `-> bool`。`_master_error()` 消除重复代码。`backing_reduction_lu` 覆盖参数。全部 8 TestVocalMixing 通过。 |
| 2026-05-28 | 贴唱混音管线：prepare_stems (clip gain + 曲风推子) + finalize_master (RMS 两趟法) + peak guard。立体声 RMS/LUFS 修正。删除 normalize 模块。8 TestVocalMixing 集成测试。238 unit + 19 integration tests。 |
| 2026-05-27 | 工程管理模块：create_project 自动保存 + 时间戳冲突规避 + save_checkpoint 检查点 + Main_SaveProjectEx 非交互保存。305 tests, 90% cov |
