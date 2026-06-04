# 流派 Profile 编写指南

MixingProfile 是用 YAML 声明的混音配置，它将混音管线（FX 链、发送、母带）描述为数据，让你无需修改引擎代码即可调整混音流程。

## 基本结构

```yaml
name: "配置名称"
description: "配置描述"
clip_gain_ref_db: -18.0     # 增益分级参考电平
target_lufs: -12.0           # 母带目标响度

vocal_chain:                 # 人声处理链
  - name: "插件名"
    type: "插件类型"          # 可选，用于压缩参数选择

backing_chain:               # 伴奏处理链（可选）
  - name: "插件名"

bus_reverb:                  # 混响发送
  name: "混响插件名"
  level_db: -8.0             # 发送电平

master_limiter:              # 母带限幅器
  name: "限幅器插件名"
  ceiling_db: -0.5           # 输出峰值限制

genre_table:                 # 流派表（人声/伴奏推子比例）
  pop: [6, 9]                # [人声推子增益, 伴奏推子增益]
  folk: [3, 6]
```

## 完整示例

### `profiles/vocal_pop.yaml`

```yaml
name: "Vocal Pop"
description: "标准流行人声混音配置"
clip_gain_ref_db: -18.0
target_lufs: -12.0

vocal_chain:
  - name: "FabFilter Pro-Q 3 (FabFilter)"
    type: "eq"
  - name: "Waves RVox (Waves)"
    type: "rvox"

bus_reverb:
  name: "ValhallaVintageVerb (Valhalla DSP)"
  level_db: -8.0

master_limiter:
  name: "FabFilter Pro-L 2 (FabFilter)"
  ceiling_db: -0.5

genre_table:
  folk: [3, 6]
  pop: [6, 9]
  rock: [4, 7]
  ballad: [5, 8]
```

### `profiles/chinese_folk_bel_canto.yaml`

```yaml
name: "民美唱法"
description: "中式民美唱法混音配置，突出人声共鸣和空间感"
clip_gain_ref_db: -18.0
target_lufs: -14.0        # 民美响度更保守

vocal_chain:
  - name: "FabFilter Pro-Q 3 (FabFilter)"
    type: "eq"
  - name: "Waves RVox (Waves)"
    type: "rvox"

bus_reverb:
  name: "ValhallaVintageVerb (Valhalla DSP)"
  level_db: -6.0           # 更大的混响

master_limiter:
  name: "FabFilter Pro-L 2 (FabFilter)"
  ceiling_db: -0.3

genre_table:
  chinese_folk_bel_canto: [9, 12]
  folk: [6, 9]
```

## 插件类型映射

`type` 字段用于告知引擎如何为该插件选择压缩参数。支持以下类型：

| type 值 | 含义 | 常用插件 |
|---------|------|---------|
| `fet` | FET 压缩 | CLA-76, 1176 |
| `opto` | 光学压缩 | LA-2A, CL-1B |
| `rvox` | 人声压缩 | RVox |
| `vca` | VCA 压缩 | RComp, API 2500, Pro-C, SSL |
| `eq` | 均衡器 | Pro-Q, ReaEQ |
| `limiter` | 限幅器 | Pro-L |
| `reverb` | 混响 | Valhalla, ReaVerb |
| `saturation` | 饱和 | Decapitator, Saturn |
| `doubler` | 加倍器 | MicroShift, Doubler |

如果不指定 `type`，引擎会尝试根据插件名称自动推断。

## 压缩器预设

引擎为不同角色和流派维护了压缩器预设表：

| 角色 | 流派 | attack_ms | release_ms |
|------|------|-----------|------------|
| vocal | pop | 5.0 | 80.0 |
| vocal | folk | 10.0 | 120.0 |
| vocal | rock | 3.0 | 60.0 |
| backing | pop | 10.0 | 150.0 |
| backing | folk | 15.0 | 200.0 |
| backing | rock | 5.0 | 100.0 |

## 使用 Profile

```python
from hermes_core import MixingEngine, MixingProfile

# 从 YAML 加载
profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")

# 或直接构造
profile = MixingProfile(
    name="自定义配置",
    clip_gain_ref_db=-18.0,
    target_lufs=-12.0,
    vocal_chain=[
        FXPreset(name="FabFilter Pro-Q 3 (FabFilter)", type="eq"),
        FXPreset(name="Waves RVox (Waves)", type="rvox"),
    ],
    bus_reverb=FXPreset(name="ValhallaVintageVerb (Valhalla DSP)", level_db=-8.0),
    master_limiter=FXPreset(name="FabFilter Pro-L 2 (FabFilter)", ceiling_db=-0.5),
    genre_table={"pop": [6, 9]},
)

with MixingEngine() as eng:
    eng.create_project("测试", "./output")
    eng.prepare_stems(["vocal.wav", "backing.wav"], genre="pop", vocal_indices=[0])
    eng.apply_profile(profile)
    eng.finalize_master()
```

## 预检

在运行混音前，可以检查 Profile 中声明的插件是否已安装：

```bash
hermes check --profile profiles/vocal_pop.yaml
```

或在代码中：

```python
from hermes_core import MixingEngine

with MixingEngine() as eng:
    profile = MixingProfile.from_yaml("profiles/rock.yaml")
    missing = eng.preflight_plugins(profile.get_all_plugin_names())
    if any(not v for v in missing.values()):
        print("以下插件未找到:", [k for k, v in missing.items() if not v])
```
