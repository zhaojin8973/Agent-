# 段落差异化 Automation

`hermes_core.automation` — 按歌曲段落应用差异化混音参数。

## 概述

支持定义歌曲结构（主歌/副歌/桥段），为每个段落设置差异化的 FX 参数值，通过 REAPER 自动化包络写入。

## 数据结构

- `SectionDef` — 段落时间边界定义
- `AutomationIntent` — 参数自动化意图
- `TrackAutomation` — 轨道级便捷构造器

## API

::: hermes_core.automation
    options:
      members:
        - SectionDef
        - AutomationIntent
        - TrackAutomation
        - AutomationManager
        - make_pop_song_structure
