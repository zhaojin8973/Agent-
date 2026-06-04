# Agent Protocol API

`agent_protocol` 模块定义了 AI Agent 与 hermes-core 之间的结构化通信层。它将混音操作封装为声明式数据结构，适合 LLM / AI Agent 驱动的工作流。

## 导入

```python
from hermes_core.agent_protocol import (
    HermesAgentAPI,
    MixRequest,
    MixResponse,
    AdjustmentRequest,
    MixGenre,
    ReverbStyle,
    AdjustmentType,
)
```

## 枚举定义

### `MixGenre`

流派枚举，支持以下值：

```python
class MixGenre(str, Enum):
    POP = "pop"
    ROCK = "rock"
    FOLK = "folk"
    BALLAD = "ballad"           # 民谣
    ELECTRONIC = "electronic"   # 电子
    HIPHOP = "hiphop"
    RNB = "rnb"
    JAZZ = "jazz"
    CHINESE_FOLK_BEL_CANTO = "chinese_folk_bel_canto"  # 民美
```

### `ReverbStyle`

混响风格枚举：

```python
class ReverbStyle(str, Enum):
    PLATE = "plate"       # 板式
    HALL = "hall"         # 大厅
    ROOM = "room"         # 房间
    CHAMBER = "chamber"   # 腔室
    SPRING = "spring"     # 弹簧
```

### `AdjustmentType`

增量调整类型，对应常见的混音调整意图：

```python
class AdjustmentType(str, Enum):
    EQ_BRIGHTER = "brighter"           # 更亮
    EQ_WARMER = "warmer"               # 更暖
    EQ_LESS_MUDDY = "less_muddy"       # 减少浑浊
    COMPRESS_MORE = "more_compress"    # 更多压缩
    COMPRESS_LESS = "less_compress"    # 更少压缩
    REVERB_MORE = "more_reverb"        # 更多混响
    REVERB_LESS = "less_reverb"        # 更少混响
    VOCAL_LOUDER = "vocal_louder"      # 人声更大
    VOCAL_QUIETER = "vocal_quieter"    # 人声更小
    DELAY_MORE = "more_delay"          # 更多延迟
    DELAY_LESS = "less_delay"          # 更少延迟
```

---

## 数据类

### `MixRequest`

一次完整的混音请求：

```python
@dataclass
class MixRequest:
    project_name: str                  # 工程名称
    vocal_stem: str                    # 人声分轨路径
    backing_stem: str                  # 伴奏分轨路径
    genre: MixGenre = MixGenre.POP     # 流派
    target_lufs: float | None = None   # 目标响度（None = 流派默认）
    reverb_style: ReverbStyle = ReverbStyle.PLATE
    output_dir: str = "./output"       # 输出目录
    sample_rate: int = 48000
```

### `MixResponse`

混音操作的响应结构：

```python
@dataclass
class MixResponse:
    success: bool                      # 是否成功
    project_name: str                  # 工程名称
    render_path: str | None = None     # 渲染输出路径
    achieved_lufs: float | None = None # 实际响度
    error_message: str | None = None   # 错误信息（成功时为 None）
    duration_seconds: float = 0.0      # 执行耗时
```

### `AdjustmentRequest`

基于用户反馈的增量调整请求：

```python
@dataclass
class AdjustmentRequest:
    project_name: str                  # 目标工程
    adjustment: AdjustmentType         # 调整类型
    magnitude: float = 1.0             # 调整幅度（0.0-2.0，1.0 为默认）
    comment: str = ""                  # 用户原始反馈文本
```

---

## HermesAgentAPI

高层 Agent API，封装 `MixingEngine` 的声明式接口。

### 构造函数

```python
class HermesAgentAPI:
    def __init__(self, engine: MixingEngine | None = None)
```

如果传入 `None`，内部会自动创建 `MixingEngine` 实例。

### `create_and_mix`

执行完整的混音流程：

```python
def create_and_mix(self, request: MixRequest) -> MixResponse
```

流程：创建工程 → 导入分轨 → 增益分级 → 应用 FX 链 → 发送混响 → 母带渲染 → 返回响应。

### `adjust`

对已有工程进行增量调整：

```python
def adjust(self, request: AdjustmentRequest) -> MixResponse
```

根据 `AdjustmentType` 映射到具体的参数调整，例如 `EQ_BRIGHTER` 对应高频提升。

### `get_project_status`

```python
def get_project_status(self, project_name: str) -> dict
```

返回指定工程的运行状态信息。

---

## 使用示例

### Agent 驱动混音

```python
from hermes_core.agent_protocol import HermesAgentAPI, MixRequest, MixGenre

api = HermesAgentAPI()

request = MixRequest(
    project_name="张三_望归_Mix",
    vocal_stem="/path/to/vocal.wav",
    backing_stem="/path/to/backing.wav",
    genre=MixGenre.CHINESE_FOLK_BEL_CANTO,
    target_lufs=-12.0,
)

response = api.create_and_mix(request)

if response.success:
    print(f"混音完成: {response.render_path}")
    print(f"实际 LUFS: {response.achieved_lufs}")
else:
    print(f"混音失败: {response.error_message}")
```

### LLM 解析用户反馈

```python
from hermes_core.agent_protocol import (
    HermesAgentAPI,
    AdjustmentRequest,
    AdjustmentType,
)

api = HermesAgentAPI()

# 假设 LLM 解析用户说的 "人声再大一点，混响少一点" 得到：
adjustments = [
    AdjustmentRequest(
        project_name="张三_望归_Mix",
        adjustment=AdjustmentType.VOCAL_LOUDER,
        magnitude=1.2,
    ),
    AdjustmentRequest(
        project_name="张三_望归_Mix",
        adjustment=AdjustmentType.REVERB_LESS,
        magnitude=0.7,
    ),
]

for adj in adjustments:
    response = api.adjust(adj)
    if not response.success:
        print(f"调整失败: {response.error_message}")
```
