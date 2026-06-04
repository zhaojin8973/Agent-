# 插件注册表

`PLUGIN_REGISTRY` 是 hermes-core 中所有 REAPER 插件引用和参数规格的单一数据源。项目中所有对 REAPER 插件的引用都应通过此注册表获取名称，而非在代码中硬编码字符串。

## 导入

```python
from hermes_core import PLUGIN_REGISTRY
from hermes_core.normalize import normalize_param, normalize_params
from hermes_core.plugin_registry import PLUGIN_REGISTRY as CHAIN_REGISTRY
```

## 两个注册表

hermes-core 维护两个互补的注册表：

### 1. 参数规格注册表 (`normalize.py`)

定义每个插件支持的参数、物理范围、归一化方式：

```python
from hermes_core import PLUGIN_REGISTRY

# 示例：Pro-Q 3 的参数定义
PLUGIN_REGISTRY["VST: FabFilter Pro-Q 3 (FabFilter)"] = {
    "Band 1 Gain":    {"min": -30, "max": 30, "default": 0.0},
    "Band 1 Freq":    {"min": 20,  "max": 20000, "default": 1000.0, "log": True},
    "Band 1 Q":       {"min": 0.1, "max": 40, "default": 1.0},
    # ...
}
```

参数归一化工具：

```python
from hermes_core import normalize_param, normalize_params

# 归一化单个参数
normalized = normalize_param("VST: FabFilter Pro-Q 3 (FabFilter)", "Band 1 Freq", 500)
# normalized ≈ 0.48（对数归一化后的值）

# 批量归一化
params = normalize_params("VST: FabFilter Pro-Q 3 (FabFilter)", {
    "Band 1 Gain": 3.0,
    "Band 1 Freq": 2500,
})
```

### 2. 信号链分类注册表 (`plugin_registry.py`)

按处理类别组织的主选/回退插件映射：

```python
from hermes_core.plugin_registry import PLUGIN_REGISTRY

# 获取外科 EQ 的主选和回退插件
eq_config = PLUGIN_REGISTRY["eq_surgical"]
# {
#     "primary": "VST: FabFilter Pro-Q 3 (FabFilter)",
#     "fallback": "ReaEQ (Cockos)",
# }
```

## 信号链类别

| 类别键 | 处理角色 | 主选 | 回退 |
|--------|---------|------|------|
| `eq_surgical` | 外科 EQ | Pro-Q 3 | ReaEQ |
| `eq_color` | 染色 EQ | SSLEQ Mono | ReaEQ |
| `compressor_peak` | 峰值压缩 | CLA-76 | Pro-C 2 |
| `compressor_rms` | RMS 压缩 | RVox | Pro-C 2 |
| `deesser` | 去齿音 | Pro-DS | — |
| `limiter_true_peak` | 真峰限幅 | Pro-L 2 | — |
| `bus_compressor` | 总线压缩 | bx_townhouse | SSL G-Master |
| `saturation` | 饱和 | Decapitator | — |
| `doubler` | 加倍器 | MicroShift | — |

## 添加新插件

### 1. 添加到参数规格注册表

在 `normalize.py` 的 `PLUGIN_REGISTRY` 中添加新条目：

```python
PLUGIN_REGISTRY["VST3: 你的插件名 (厂商)"] = {
    "参数名1": {"min": 0, "max": 100, "default": 50},
    "参数名2": {"min": 0.0, "max": 1.0, "default": 0.5},
}
```

### 2. 添加到信号链注册表（如适用）

在 `plugin_registry.py` 中添加新的类别映射：

```python
PLUGIN_REGISTRY["your_category"] = {
    "primary": "VST3: 你的插件名 (厂商)",
    "fallback": None,
}
```

### 3. 编写 Profile

在 `profiles/` 中创建或修改 YAML Profile，引用新插件名。

## 插件命名规范

REAPER 中插件的完整名称遵循 `格式: 插件名 (厂商)` 模式：

| 格式 | 示例 |
|------|------|
| VST | `VST: FabFilter Pro-Q 3 (FabFilter)` |
| VST3 | `VST3: RVox Mono (Waves)` |
| AU | `AU: ValhallaVintageVerb (Valhalla DSP)` |
| JSFX | `JS: ReaEQ (Cockos)` |

要确定插件的完整名称，可以在 REAPER 中添加插件后查看 FX 链中的显示名称，或使用：

```python
from hermes_core import MixingEngine

with MixingEngine() as eng:
    names = eng.preflight_plugins(["FabFilter", "Waves", "Valhalla"])
    print(names)
```

## 使用示例

### 使用主选/回退机制

```python
from hermes_core.plugin_registry import PLUGIN_REGISTRY
from hermes_core import MixingEngine

# 获取外科 EQ 配置
eq_config = PLUGIN_REGISTRY["eq_surgical"]

with MixingEngine() as eng:
    # 先尝试主选
    available = eng.preflight_plugins([eq_config["primary"]])
    plugin_name = eq_config["primary"] if available[eq_config["primary"]] else eq_config["fallback"]

    if plugin_name:
        eng.add_fx(0, str(plugin_name))
    else:
        print("外科 EQ 不可用")
```
