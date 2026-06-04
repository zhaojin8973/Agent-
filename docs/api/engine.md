# MixingEngine API

`MixingEngine` 是 hermes-core 的入口类，位于三层架构的 L3 层。它组合所有 L2 领域模块，提供统一的高层混音 API。

## 导入

```python
from hermes_core import MixingEngine
```

## 构造函数

### `MixingEngine.__init__`

```python
def __init__(
    self,
    watchdog: bool = False,
    config: HermesConfig | None = None,
) -> None
```

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| `watchdog` | `bool` | `False` | 是否启用 DialogKiller 弹窗自动关闭 |
| `config` | `HermesConfig \| None` | `None` | 全局配置对象 |

MixingEngine 支持上下文管理器协议：

```python
with MixingEngine(watchdog=True) as eng:
    eng.create_project(...)
    ...
# 退出时自动断开 REAPER 连接
```

---

## 项目管理

### `create_project`

```python
def create_project(
    self,
    name: str,
    output_dir: str,
    *,
    sample_rate: int = 48000,
) -> None
```

创建新的 REAPER 工程，设置采样率，配置自动保存和标准目录结构。

| 参数 | 类型 | 说明 |
|------|------|------|
| `name` | `str` | 工程名称 |
| `output_dir` | `str` | 输出根目录 |
| `sample_rate` | `int` | 采样率，默认 48000 |

### `save_checkpoint`

```python
def save_checkpoint(self, label: str) -> str
```

保存当前工程的快照，返回快照文件路径。

### `get_project_info`

```python
def get_project_info(self) -> dict
```

返回当前工程的元数据字典，包括名称、音轨数、FX 链等。

### `reset`

```python
def reset(self) -> None
```

清除幂等性守卫，允许重新调用 `prepare_stems` 和 `finalize_master`。

---

## 音频处理

### `prepare_stems`

```python
def prepare_stems(
    self,
    paths: list[str],
    genre: str,
    *,
    vocal_indices: list[int] | None = None,
) -> None
```

导入音频分轨，进行 clip gain 增益分级和推子平衡。**幂等操作**，同实例上只能调用一次。

| 参数 | 类型 | 说明 |
|------|------|------|
| `paths` | `list[str]` | WAV 文件路径列表 |
| `genre` | `str` | 流派名称（如 `"pop"`、`"folk"`） |
| `vocal_indices` | `list[int] \| None` | 人声轨道的索引位置 |

### `add_fx`

```python
def add_fx(self, track_index: int, fx_name: str) -> None
```

向指定轨道添加效果器插件。

| 参数 | 类型 | 说明 |
|------|------|------|
| `track_index` | `int` | 轨道编号（0-based） |
| `fx_name` | `str` | 插件名称（需与 REAPER 中的完全一致） |

### `create_reverb_send`

```python
def create_reverb_send(self, src_track_index: int, *, level_db: float = -8.0) -> None
```

创建混响辅助返回轨道，并从源轨道发送信号。

| 参数 | 类型 | 说明 |
|------|------|------|
| `src_track_index` | `int` | 源轨道编号 |
| `level_db` | `float` | 发送电平（dB），默认 -8.0 |

---

## 母带与渲染

### `finalize_master`

```python
def finalize_master(self, *, target_lufs: float | None = None) -> dict
```

在 Master 总线上执行响度优化并渲染导出。**幂等操作**。

| 参数 | 类型 | 说明 |
|------|------|------|
| `target_lufs` | `float \| None` | 目标 LUFS，默认从流派表获取 |

返回：

```python
{
    "output_path": str,      # 渲染输出文件路径
    "achieved_lufs": float,  # 实际达到的 LUFS 值
    "peak_db": float,        # 峰值电平
}
```

### `render_mix`

```python
def render_mix(self, output_dir: str) -> str
```

直接渲染混音（不含母带处理），返回输出文件路径。

### `audit_mix`

```python
def audit_mix(self, path: str) -> dict
```

对已渲染文件进行质量检测，返回响度、峰值、动态范围等指标。

---

## 工具方法

### `preflight_plugins`

```python
def preflight_plugins(self, names: list[str]) -> dict[str, bool]
```

检查指定插件是否在 REAPER 中可用。返回 `{插件名: 是否可用}` 字典。

### `apply_profile`

```python
def apply_profile(self, profile: MixingProfile) -> None
```

应用 MixingProfile 配置，自动加载 FX 链、发送和母带设置。

---

## 异常

所有引擎异常都继承自 `HermesError`：

| 异常 | 说明 |
|------|------|
| `BridgeConnectionError` | REAPER 连接失败或断开 |
| `InvalidStateError` | 违反幂等性约束 |
| `TrackError` | 音轨操作失败 |
| `RenderError` | 渲染失败 |
| `PluginNotFoundError` | 插件未找到 |

---

## 完整示例

```python
from hermes_core import MixingEngine, MixingProfile

profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")

with MixingEngine(watchdog=True) as eng:
    # 工程初始化
    eng.create_project("完整演示", "./output", sample_rate=48000)

    # 导入 + 增益
    eng.prepare_stems(
        ["vocal.wav", "backing.wav"],
        genre="pop",
        vocal_indices=[0],
    )

    # 应用 Profile（等价于手动 add_fx + create_reverb_send + finalize_master）
    eng.apply_profile(profile)

    # 质检
    report = eng.audit_mix(result["output_path"])
    print(f"质检结果: {report}")
```
