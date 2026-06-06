# FX 参数推导策略

`hermes_core.fx_builder` — 将 `_build_audio_chain` 中每种 FX 类型的参数推导逻辑提取为纯函数。

## 概述

每个策略函数接受统一的 `FXBuildContext` 上下文，返回物理参数字典。REAPER 交互由 `engine.py` 统一处理。

## 支持的 FX 类型

- `eq` — 频谱驱动 + 静态基线回退
- `comp` (vca/fet/opto/rvox/cla-76) — crest/peak → CompressionIntent
- `deesser` — 存在感缺失 → 阈值 + 流派感知 Range
- `saturation` — Crest Factor → Drive
- `dynamic_eq` — 共振检测 → Pro-Q 3 动态模式
- `doubler` — 默认宽度/失谐参数

## API

::: hermes_core.fx_builder
    options:
      members:
        - FXBuildContext
        - build_fx_params
        - get_fx_builder
