# 微渲染管线

`hermes_core.chain_renderer` — 将 DAG AudioNode 链渲染为独立缓存 WAV 文件。

## 概述

每个节点在临时 REAPER 轨道上渲染，应用 FX 和参数，独奏渲染后清理。干净的节点（`is_dirty == False`）在缓存有效时被跳过。

## API

::: hermes_core.chain_renderer
    options:
      members:
        - execute_chain
        - _make_chain_executor
