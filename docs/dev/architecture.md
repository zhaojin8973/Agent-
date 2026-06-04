# 架构

## 三层设计

hermes-core 采用精益三层架构，层间单向依赖：

```
┌──────────────────────────────────────────────────────┐
│  L3: MixingEngine (engine.py)                        │
│  组合所有 L2 模块，提供统一 API                        │
├──────────────────────────────────────────────────────┤
│  L2: 领域管理器                                       │
│  track.py  bus.py  fx.py  send.py                    │
│  render.py  signal.py  loudness_optimizer.py          │
├──────────────────────────────────────────────────────┤
│  L1: REAPER 桥接 (bridge.py)                         │
│  reapy 连接 + UI 抑制 + 弹窗守护                      │
└──────────────────────────────────────────────────────┘
```

### 依赖方向

```
L3 → L2 → L1
```

高层依赖低层，低层不感知高层。这保证：

- L1 可独立测试（模拟 REAPER 连接）
- L2 可独立测试（模拟 L1 实现）
- L3 通过组合 L2 实现功能，自身不直接调用 reapy

---

## L1: REAPER 桥接层

**模块**: `bridge.py`

负责所有与 REAPER 的直接交互，是系统中唯一的 reapy 调用入口。

### 核心组件

| 组件 | 职责 |
|------|------|
| `ReaperBridge` | reapy 连接管理、重连、健康检查、UI 锁定/解锁 |
| `DialogKiller` | macOS AppleScript 弹窗自动关闭守护线程 |
| `DialogEvent` | 弹窗事件数据结构 |

### ReaperBridge API

```python
from hermes_core import ReaperBridge

bridge = ReaperBridge()

# 上下文管理器
with bridge:
    bridge.lock_ui()
    track_count = bridge.get_track_count()
    # ...
    bridge.unlock_ui()  # __exit__ 自动调用
```

关键能力：

- **reapy 连接**：自动检测 REAPER 是否运行，支持重连
- **UI 抑制**：`PreventUIRefresh` 防止渲染时闪烁
- **健康检查**：定期检测连接状态，自动重连
- **状态查询**：提供 REAPER 全局状态的只读查询接口

### DialogKiller

独立的守护线程，通过 AppleScript 扫描 REAPER 弹窗并自动关闭：

```python
from hermes_core import DialogKiller

# 通过 MixingEngine 启用
eng = MixingEngine(watchdog=True)

# 或直接使用
killer = DialogKiller()
killer.start()
# ... 混音操作 ...
killer.stop()
```

弹窗分为三级：

| 级别 | 说明 | 处理 |
|------|------|------|
| 安全 | 已知无害弹窗（保存提示等） | 自动关闭 |
| 诊断 | 需要记录但不阻断 | 记录日志后关闭 |
| 未知 | 无法分类的弹窗 | 记录并尝试关闭 |

---

## L2: 领域管理层

每个模块封装一类混音领域概念，通过 `ReaperBridge` 与 REAPER 交互。

### track.py — 音轨管理

```python
from hermes_core import TrackManager, TrackInfo

manager = TrackManager(bridge)

# CRUD
track = manager.create_track("Vocal")
manager.set_volume(track, -3.0)
manager.set_pan(track, 0.0)

# 媒体导入
manager.import_media(track, "/path/to/audio.wav")

# 查询
tracks = manager.list_tracks()
info = manager.get_track_info(track)  # TrackInfo 数据类
```

关键特性：

- **Clip Gain**：通过 `SetMediaItemInfo_Value` 设置 item 电平
- **绝对路径导入**：防止 REAPER 离线
- **Float WAV 转 PCM**：兼容所有 WAV 格式
- **文件夹深度管理**：支持子文件夹编组

### bus.py — 总线管理

```python
from hermes_core import BusManager, FolderInfo

manager = BusManager(bridge)

# 创建文件夹总线
folder = manager.create_folder("Vocal Bus")

# 将轨道移入文件夹
manager.add_to_folder(track, folder)

# 总线结构验证
tree = manager.get_folder_tree()
```

### fx.py — 效果器管理

```python
from hermes_core import FxManager

manager = FxManager(bridge)

# 添加效果器
fx_index = manager.add_fx(track, "FabFilter Pro-Q 3 (FabFilter)")

# 设置参数
manager.set_fx_param(track, fx_index, "Band 1 Gain", 3.0)

# 预设管理
manager.load_preset(track, fx_index, "Vocal Presence")
```

### send.py — 发送管理

```python
from hermes_core import SendManager, SendMode

manager = SendManager(bridge)

# 创建发送
manager.create_send(src_track, dest_track, level_db=-8.0, mode=SendMode.POST_FX)

# 混响辅助返回
reverb_track = manager.create_aux_return("Reverb", "ValhallaVintageVerb")
manager.create_send(vocal_track, reverb_track, level_db=-8.0)
```

### render.py — 渲染管理

```python
from hermes_core import RenderManager, RenderFormat

manager = RenderManager(bridge)

# 配置渲染
manager.set_format(RenderFormat.WAV_24BIT)
manager.set_sample_rate(48000)

# 执行渲染
output_path = manager.render("/output/dir", "song_mix.wav")
```

### signal.py — 信号分析

```python
from hermes_core import SignalAnalyzer, SignalReport

analyzer = SignalAnalyzer()

# 分析音频文件
report: SignalReport = analyzer.analyze("/path/to/audio.wav")
print(f"LUFS: {report.lufs}")
print(f"Peak: {report.peak_db}")
print(f"Dynamic Range: {report.dynamic_range_db}")
```

### loudness_optimizer.py — 响度优化

```python
from hermes_core.loudness_optimizer import (
    find_optimal_gain,
    verify_output,
    load_calibration,
    generate_report,
)

# 寻找最佳增益
gain_db = find_optimal_gain(
    input_path="/path/to/mix.wav",
    target_lufs=-12.0,
    ceiling_db=-0.5,
)

# 验证输出
ok = verify_output("/path/to/master.wav", target_lufs=-12.0)

# 生成报告
report = generate_report(input_path, output_path, target_lufs=-12.0)
```

---

## L3: MixingEngine

**模块**: `engine.py`

组合所有 L2 模块，提供统一的高层 API。详见 [MixingEngine API 文档](../api/engine.md)。

---

## 辅助模块

| 模块 | 职责 |
|------|------|
| `profiles.py` | MixingProfile 数据类 + YAML 序列化 |
| `normalize.py` | 插件参数归一化 + PLUGIN_REGISTRY |
| `plugin_registry.py` | 信号链类别 → 主选/回退插件映射 |
| `genre_tables.py` | 18 个流派的参数表（推子比、LUFS 目标等） |
| `config.py` | HermesConfig 全局配置 |
| `exceptions.py` | 异常层次结构 |
| `security.py` | 路径沙箱、限流、磁盘检查 |
| `agent_protocol.py` | AI Agent 声明式通信层 |
| `dag.py` | 音频节点与链执行器 |
| `audio_utils.py` | note_to_ms 等转换工具 |
| `spectrum.py` | 频谱分析 |
| `cli.py` | 命令行入口 |
| `__init__.py` | 公共 API 重导出 |

---

## 数据流

一次完整的混音流程数据流：

```
用户 / Agent
    │
    ▼
MixingEngine (L3)
    │ create_project → L1 bridge (REAPER RPR_* API)
    │ prepare_stems → L2 track.import_media + gain_staging
    │ add_fx        → L2 fx.add_fx + param normalization
    │ create_send   → L2 send.create_send + bus.create_aux
    │ finalize      → L2 loudness_optimizer + render
    │
    ▼
L1 bridge.reapy
    │
    ▼
REAPER (本地进程, 127.0.0.1)
```

---

## 设计原则

1. **单一职责**：每个 L2 模块只封装一个领域概念
2. **依赖倒置**：L2 依赖 L1 的接口，不依赖具体 reapy API
3. **配置驱动**：MixingProfile 是数据，不是代码
4. **幂等性**：关键操作有守卫，防止意外重复执行
5. **安全优先**：所有路径操作经过安全层校验
