# Hermes-Core

**REAPER DAW 精益三层 Python 自动化引擎** — 非交互式、无界面的混音工作流。

[![Python](https://img.shields.io/badge/python-3.11%20%7C%203.12%20%7C%203.13-blue)](https://www.python.org/)
[![REAPER](https://img.shields.io/badge/REAPER-7.73%2B-orange)](https://www.reaper.fm/)
[![License](https://img.shields.io/badge/license-MIT-green)](https://github.com/zhaojin/hermes-core/blob/master/LICENSE)

## 一句话

把混音流程写成 Python 脚本，让 REAPER 自动完成：**导入分轨 → 增益分级 → 添加效果 → 发送混响 → 母带响度优化 → 渲染导出**。

## 快速开始

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

## 核心特性

- **三层架构**：L1 桥接层（REAPER 连接）→ L2 领域层（音轨/FX/发送/渲染）→ L3 入口层（统一 API）
- **流派感知**：内置 18 个流派参数表，自动适配 EQ/压缩/发送量/响度目标
- **YAML 配置驱动**：通过 `MixingProfile` 声明混音管线，无需改代码即可换插件
- **幂等性守卫**：关键操作（增益分级、母带）防止意外重复执行
- **安全沙箱**：路径验证、操作限流、弹窗自动关闭
- **Agent Protocol**：声明式数据结构，适合 AI Agent 调用

## 前置条件

| 依赖 | 版本 | 说明 |
|------|------|------|
| **REAPER** | 7.73+ | 必须运行中，reapy 通过网络连接 |
| **Python** | 3.11–3.13 | 3.14 不兼容 REAPER 7.73 |
| **reapy** | >=0.10 | `pip install python-reapy` |

详细文档见 [快速开始](quickstart.md)。
