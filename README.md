# Hermes-Core

**REAPER DAW 精益三层 Python 自动化引擎** — 非交互式、无界面的混音工作流。

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![REAPER](https://img.shields.io/badge/REAPER-7.73%2B-orange)](https://www.reaper.fm/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

## 一句话

把混音流程写成 Python 脚本，让 REAPER 自动完成：**导入分轨 → 增益分级 → 添加效果 → 发送混响 → 母带响度优化 → 渲染导出**。

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| **REAPER** | 7.73+ | 必须运行中，reapy 通过网络连接 |
| **Python** | 3.11–3.13 | **⚠️ 3.14 不兼容 REAPER 7.73** |
| **reapy** | ≥0.10 | `pip install python-reapy` |
| **插件** | — | 见下方 [必需插件](#必需插件) |

### 必需插件

本项目的默认混音管线依赖以下第三方插件：

| 位置 | 插件 | 用途 |
|------|------|------|
| 人声 | FabFilter Pro-Q 3 | 均衡 |
| 人声 | Waves RVox | 压缩 |
| 混响发送 | ValhallaVintageVerb | 空间混响 |
| Master | FabFilter Pro-L 2 | 母带限幅 |

> **自定义插件链**：可通过 `MixingProfile` 配置替换以上任意插件（见 [配置体系](#配置体系)）。

## 安装

```bash
# 1. 克隆仓库
git clone <repo-url>
cd hermes-core

# 2. 创建虚拟环境（推荐）
python3.13 -m venv .venv
source .venv/bin/activate

# 3. 安装
pip install -e ".[dev,test]"
```

### REAPER 配置

在 REAPER 中设置 Python 路径（`reaper.ini`）：

```ini
[reaper]
pythonlibpath64=/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13
```

或通过 REAPER 菜单：`Options → Preferences → Plug-ins → ReaScript → Python`

## 快速开始

### 5 分钟跑通一次贴唱混音

```python
from hermes_core import MixingEngine

with MixingEngine(watchdog=True) as eng:
    # 1. 创建工程
    eng.create_project("望归", "./output", sample_rate=48000)

    # 2. 导入 + 增益分级
    eng.prepare_stems(
        ["./望归_Vocal.wav", "./望归_Backing.wav"],
        genre="chinese_folk_bel_canto",
        vocal_indices=[0],
    )

    # 3. 添加人声处理（EQ + 压缩）
    eng.add_fx(0, "FabFilter Pro-Q 3 (FabFilter)")
    eng.add_fx(0, "Waves RVox (Waves)")

    # 4. 创建混响发送
    eng.create_reverb_send(0, level_db=-8.0)

    # 5. 母带 + 渲染
    result = eng.finalize_master(target_lufs=-12.0)
    print(f"Output: {result['output_path']}")
    print(f"Achieved LUFS: {result['achieved_lufs']}")
```

## 架构

三层设计，层间单向依赖：

```
┌──────────────────────────────────────┐
│  L3: MixingEngine (engine.py)        │
│  组合所有 L2 模块，提供统一 API       │
├──────────────────────────────────────┤
│  L2: 领域管理器                       │
│  track.py  bus.py  fx.py  send.py    │
│  render.py  signal.py                │
│  loudness_optimizer.py               │
├──────────────────────────────────────┤
│  L1: REAPER 桥接 (bridge.py)         │
│  reapy 连接 + UI 抑制 + 弹窗守护      │
└──────────────────────────────────────┘
```

## 配置体系

### MixingProfile（YAML）

```yaml
# profiles/vocal_pop.yaml
name: "Vocal Pop"
clip_gain_ref_db: -18.0
target_lufs: -12.0

vocal_chain:
  - name: "FabFilter Pro-Q 3 (FabFilter)"
  - name: "Waves RVox (Waves)"

bus_reverb:
  name: "ValhallaVintageVerb (Valhalla DSP)"
  level_db: -8.0

master_limiter:
  name: "FabFilter Pro-L 2 (FabFilter)"
  ceiling_db: -0.5

genre_table:
  folk: [3, 6]
  pop: [6, 9]
  chinese_folk_bel_canto: [9, 12]
```

```python
from hermes_core import MixingEngine, MixingProfile

profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")
with MixingEngine() as eng:
    eng.create_project(...)
    eng.prepare_stems(..., genre="pop")
    eng.apply_profile(profile)  # 自动加载 FX 链 + 发送
    eng.finalize_master(target_lufs=profile.target_lufs)
```

## CLI 使用

```bash
# 一键贴唱混音
hermes vocal-mix \
  --vocal "望归 Vocal.wav" \
  --backing "望归 伴奏.wav" \
  --genre chinese_folk_bel_canto \
  --output ./output \
  --target-lufs -12

# 使用自定义配置
hermes vocal-mix \
  --vocal vocal.wav \
  --backing inst.wav \
  --profile profiles/rock.yaml

# 批量处理
hermes batch \
  --input-dir ./songs \
  --profile profiles/pop.yaml \
  --output-dir ./masters

# 插件预检
hermes check --profile profiles/rock.yaml
```

## 关键 API

| 方法 | 说明 | 幂等 |
|------|------|------|
| `create_project(name, dir)` | 创建工程 + 自动保存 | — |
| `prepare_stems(paths, genre)` | 导入 + clip gain + 推子平衡 | ✅ 二次调用报错 |
| `add_fx(track, name)` | 添加效果器 | — |
| `create_reverb_send(src)` | 创建 Aux Return + 发送 | — |
| `finalize_master(target_lufs)` | 母带响度优化 + 渲染 | ✅ 二次调用报错 |
| `render_mix(output_dir)` | 直接渲染（无母带） | — |
| `audit_mix(path)` | 渲染后质检 | — |
| `save_checkpoint(label)` | 快照保存 | — |
| `get_project_info()` | 当前工程元数据 | — |
| `preflight_plugins(names)` | 检测插件是否可用 | — |
| `reset()` | 清除幂等守卫 | — |

> **幂等性**：`prepare_stems` 和 `finalize_master` 在同一个 engine 实例上只能调用一次。调用 `reset()` 或 `create_project()` 会清除守卫。这防止意外重复执行导致增益叠加或双份 limiter。

## 已知限制

1. **仅 macOS**：DialogKiller 依赖 AppleScript
2. **REAPER 7.73 arm64**：仅在 ARM64 macOS + REAPER 7.73 上测试
3. **Python 3.14 不兼容**：REAPER 7.73 不支持 Python 3.14
4. **实时 REAPER 依赖**：单元测试不需要 REAPER（287 tests），但真实混音流程需要 REAPER 运行中
5. **非 WAV 输入**：track.py 的媒体导入仍需要 WAV（分析用 soundfile 支持多种格式）

## 故障排除

### `reapy.errors.DisabledDistAPIError`

REAPER 没有运行，或 `reapy` 未配置。确保 REAPER 已启动。

### `ImportError: No module named 'reapy'`

```bash
pip install python-reapy
```

### ARM64 元组解包错误

REAPER 7.73 的 ARM64 bug 导致部分 API 返回异常元组。代码已在 `fx.py`、`bridge.py` 中做了安全解包处理。

### 渲染失败 / 找不到输出文件

检查 `output_dir` 的写入权限。REAPER 弹窗可能阻塞渲染 — 使用 `watchdog=True` 启用 DialogKiller。

### Pro-L 2 参数设置失败

确保插件名完全匹配 REAPER 中的名称。不同安装方式可能导致不同名称：
- `"FabFilter Pro-L 2 (FabFilter)"` — VST3
- `"FabFilter Pro-L 2"` — VST2

## 开发

```bash
# 安装开发依赖
pip install -e ".[dev,test]"

# 运行单元测试（不需要 REAPER）
pytest tests/ -m unit

# 运行全部测试（需要 REAPER 运行中）
pytest tests/

# 代码风格
ruff check src/
mypy src/
```

## 许可证

MIT
