# 大师空间模板

`hermes_core.master_templates` — 四位知名混音师的空间效果链模板。

## 概述

每个模板构建一组辅助轨道（延迟/混响返回），配置 EQ、空间插件和发送。所有函数接受 REAPER 子管理器作为显式参数。

## 可用模板

| 模板 | 混音师 | 特点 |
|------|--------|------|
| `cla` | Chris Lord-Alge | 3 延迟 + 3 混响 + 交叉发送 |
| `hewitt` | Ryan Hewitt | 3 层 EMT 140 板混响 |
| `serban` | Serban Ghenea | 5 返回轨 + Pro-C 2 侧链 |
| `townsend` | Devin Townsend | 不对称 L/R 延迟 + Little Plate 粘合 |

## API

::: hermes_core.master_templates
    options:
      members:
        - apply_master_template
        - AVAILABLE_TEMPLATES
