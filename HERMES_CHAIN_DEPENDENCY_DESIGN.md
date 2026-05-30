# hermes-core 智能音频引擎：串联效果器链路的非线性耦合难题与 DAG 状态机架构设计

## 一、 问题背景与本质：非线性串联耦合 (Serial Non-linear Coupling)

在传统混音与自动化音频处理中，当效果器以串联形式排列（例如：`EQ 1 → 压缩 1 → EQ 2 → 压缩 2`）时，各节点之间的参数调节存在严重的**相互干涉与非线性耦合问题**。

### 1.1 相互干涉的具体表现
* **EQ 1 (减法) 漂移效应**：EQ 1 滤除了低频浑浊或切除了某些恶性共振点，这会直接改变信号的整体 RMS 电平。如果此时没有精准的增益补偿，原本为下游“压缩 1”计算好的物理阈值（Threshold）就会瞬间脱靶，导致压缩器要么压得过狠，要么完全触发不到。
* **压缩 1 包络改变**：压缩 1 动态下压后，改变了声音的瞬态（Transient）和能量包络（Envelope）。这会导致原本在干声状态下听起来很完美的“EQ 2”频段，在被压缩后变得异常刺耳或沉闷。
* **EQ 2 (加法) Pump 效应**：如果 EQ 2 在中高频进行了音色塑形提升，高频能量的暴涨会直接导致下游“压缩 2”被高频信号提前错误触发，从而产生严重的抽吸效应（Pumping）。

### 1.2 传统黑盒引擎的死锁困境
如果将混音引擎设计为“一次性静态分析”（即只在最开始扫描一次原始干声，算出所有插件参数并一次性下发），这种非线性耦合会直接摧毁混音结果。因为任何前级插件的生效，都会导致后级插件面对的音频物理状态发生改变。在传统的非交互式工作流中，这种“相互回调”甚至可能导致算法陷入逻辑死锁。

---

## 二、 Agent 范式下的思维转变 (Paradigm Shift)

在讨论此问题的架构解法时，必须明确 `hermes-core` 的角色定位：**它本身就是一个拥有完整“分析、决策、执行”闭环的自主智能体（AI Agent），而不是一个被动的 API 接口层。**

### 2.1 物理耦合对决策大脑的透明化
智能体在产生音乐和混音意图时（例如：“觉得人声低频太脏，我要把 EQ 1 的 Low Cut 推到 120Hz”），其决策是基于主观听感或高层业务逻辑的。
架构设计上**绝对不能**指望 Agent 在做出这一修改时，还能肉眼推理并精准计算出：“因为我推了 120Hz 导致总 RMS 下降，所以我需要把下游压缩器的 Threshold 调低 1.5dB”。
让 Agent 大脑去承担非线性的声学物理计算会导致严重的幻觉和计算过载。因此，`hermes-core` 内部必须封装一套**“自动抗干扰”的状态机机制**，把复杂的声学物理擦屁股工作彻底对上层透明化。

---

## 三、 核心架构：基于 DAG 的音频节点流水线 (Audio Node Pipeline)

为了彻底解决非线性串联耦合问题，`hermes-core` 将效果器链路抽象为一个**有向无环图（DAG）响应式依赖系统**。每一个效果器插件和处理步骤都被视为图中的一个独立节点（Node）。

### 3.1 节点化状态管理 (Node-Based State)
每个 `AudioNode` 内部强制封装并维护三个核心数据状态：
1. **输入状态 (Input State)**：该节点接收到的音频数据及其声学特征（如局部 RMS、Crest Factor、LUFS 分布）。
2. **处理意图 (Intent/Params)**：当前节点由 Agent 逻辑决策出的物理参数（如 `LowCut=120Hz` 或 `Compression=Medium`）。
3. **输出缓存 (Output Cache)**：当前节点在宿主（REAPER）内部经离线微渲染（Micro-Render）产生的中间层音频文件（如 `vocal_node_1_out.wav`）。

### 3.2 级联作废与惰性重算机制 (Dirty Flagging & Cascade Invalidation)
当引擎响应指令或自发决策修改了链路上某一个插件的参数时，底层状态机遵循以下“蝴蝶效应”自愈流程：

```
[Agent 修改 EQ 1 参数] 
        │
        ▼
[Node 1 标记为 Dirty] ────► [销毁 Node 1 输出缓存]
        │
        ▼
[依赖追踪：自动将下游 Node 2 (压缩)、Node 3 (EQ 2) 全部级联标记为 Dirty]
        │
        ▼
[惰性执行计算循环 (Lazy Execution)]
        ├─► Node 1 读取上游输入 ──► 调用 REAPER 极速渲染 ──► 更新 Node 1 缓存
        ├─► Node 2 检测到 Dirty ──► 读取 Node 1 最新缓存 ──► 重新跑分析 (Crest Factor) ──► 自动更新 Threshold
        └─► 后续节点依此类推...
```

1. **脏标记 (Dirty Flag)**：Node 1 参数一动，自身立刻变脏。
2. **递归污染**：状态机沿着 DAG 的依赖链条，递归地将所有依赖 Node 1 输出的下游节点全部标记为 `Dirty`，同时彻底作废它们先前保存的音频输出缓存。
3. **惰性计算 (Lazy Execution)**：Agent 可以连续修改多个插件的参数，引擎不会立刻频繁调用宿主。只有当需要最终合成结果或执行验收时，才会从第一个脏节点开始，向后重新“编译”和分析。

### 3.3 响度强制守恒（RMS Matching）辅助防御
为了减轻全链路级联重算的频率与压力，引擎在 EQ 翻译器层引入了“严格增益守恒”硬编码：
任何 EQ 节点在执行完频响曲线调整后，翻译器内部会自动对比处理前后的 RMS 电平差值。如果因为切除低频整体电平掉了 2dB，引擎会自动利用该 EQ 插件的 `Output Gain`（输出增益旋钮）默默补回这 2dB。
这种**“引擎在底下默默垫砖”**的机制，可以保证下游压缩器接收到的总能量基准在日常调整中保持恒定，极大维持了动态处理器的安全，切断了大部分因增益波动触发的级联失效。

---

## 四、 架构路线选择：手搓轻量状态机 vs. 开源框架

| 维度 | 方案 A：通用任务编排框架 (如 Airflow) | 方案 B：图论数学库 (如 NetworkX) | 方案 C：Python 手搓轻量状态机 (最推荐) |
| :--- | :--- | :--- | :--- |
| **本质用途** | 分布式微服务、大数据工程。 | 纯粹的图拓扑、连通性数学计算。 | 高度定制的面向对象（OOP）链表结构。 |
| **开销与延迟** | **极重**。每次调度产生秒级延迟。 | **轻量数学计算**。不涉及执行调度逻辑。 | **极轻、纳秒级**。无第三方依赖，直接常驻内存。 |
| **音频契合度** | **极差**。音频引擎需要毫秒级交互。 | **一般**。可以算跨轨道侧链。 | **极强**。完美契合音轨 FX Chain 的直线/多叉树特性。 |
| **执行逻辑** | 框架托管执行。 | 必须自己写执行器。 | 状态标记、翻译、宿主调用融为一体。 |

**架构定论：** 对于本地高性能 AI Agent 混音引擎，必须选择方案 C。手搓一套轻量级的惰性链表代码，即可换来最极致的零开销秒级响应速度。

---

## 五、 代码蓝图实现 (Blueprint)

```python
import logging

logger = logging.getLogger('hermes.core.dag')

class AudioNode:
    def __init__(self, name: str, fx_type: str):
        self.name = name                # 节点唯一标识，如 'Vocal_EQ_1'
        self.fx_type = fx_type          # 插件类型，如 'eq', 'comp'
        self.params = {}                # Agent 决策并下发的物理参数
        self.is_dirty = True            # 核心脏标记
        
        self.input_audio_path = None    # 上游传过来的输入音频缓存路径
        self.output_audio_path = None   # 本节点处理渲染完的输出音频缓存路径
        self.downstream_nodes = []      # 下游依赖节点列表

    def add_downstream(self, node):
        if node not in self.downstream_nodes:
            self.downstream_nodes.append(node)

    def update_params(self, new_params: dict):
        if self.params != new_params:
            logger.info(f'[DAG] Node {self.name} params changed. Triggering invalidation.')
            self.params = new_params
            self.invalidate()

    def invalidate(self):
        self.is_dirty = True
        self.output_audio_path = None   # 清理本级失效缓存
        
        for downstream in self.downstream_nodes:
            if not downstream.is_dirty:
                downstream.invalidate()

    def process(self, session) -> str:
        if not self.is_dirty and self.output_audio_path:
            return self.output_audio_path

        logger.info(f'[DAG] Executing recalibration pipeline for {self.name}...')
        
        # 1. 确保拿到前级最新的音频 (input_audio_path)
        # 2. 如果是压缩器，基于新音频重新跑分析
        if self.fx_type == 'comp':
            logger.info(f'  -> Analyzed fresh audio profile for {self.name}')
            # self.calc_params = analyze_and_translate(self.input_audio_path, self.params)
        
        # 3. 驱动 REAPER 微渲染本节点
        # self.output_audio_path = session.render_node(self.name, self.calc_params)
        
        self.is_dirty = False
        return self.output_audio_path
```
