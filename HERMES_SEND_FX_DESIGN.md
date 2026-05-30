# hermes-core 智能音频引擎：并行发送效果器（Send FX）的旁路观察者模式设计

## 一、 痛点与挑战：为什么主干道不能修“立交桥”？

在混音工程中，主干 Insert 链路（如 `EQ -> 压缩 -> 饱和`）是一条纯粹的线性结构。而混响（Reverb）和延迟（Delay）等空间类效果器，属于**并行发送（Send FX）**。
如果为了处理混响，强行在 `hermes-core` 刚刚确立的“线性链表 MVP”主干中引入复杂的分支网络和图遍历（DAG拓扑排序），不仅会破坏主干的极速线性计算，还会引发毫无意义的级联重算。因为在声学逻辑上，混响轨只是主干的一条**“只读旁路（Read-Only Bypass）”**，它的输出直接去往 Mix Bus，绝不会回流污染主干道。

---

## 二、 核心设计：旁路观察者模式 (Sidecar / Observer Pattern)

在这个模式下，主干依然是一条笔直的高速公路，而混响节点被设计成挂载在高速公路旁边的“照相机”（观察者叶子节点）。它们之间存在着**不对称的单向依赖（Asymmetric Dirty Flagging）**——即“本体与影子”的关系：

### 1. 人动了，影子必须跟着动（主干变脏 → 混响必脏）
如果大模型 Agent 修改了主干上的 EQ（比如把人声调亮了），最终出来的人声本体变了。混响节点检测到上游“粮草”变了，自身立刻标记为 `Dirty`，在底层自动用新的人声去跑一次全新的混响渲染。以保证本体和影子的音色是统一的。

### 2. 动影子，人不改（混响变脏 → 主干无感）
如果 Agent 只是下达指令“把混响尾音拉长一点”。此时只有混响节点变 `Dirty`，主干链表**完全不需要**重新分析和微渲染，直接复用上一次输出的人声干声缓存。这极大地节省了算力。

---

## 三、 极限工程优化：微渲染与“缓存叠加”机制

为了满足自动化批量测试和 LLM 的极速验证需求，引擎在处理空间类 FX 时，引入了**100%纯湿声缓存（Wet WAV Caching）**机制。这种机制在处理不同参数修改时，执行逻辑有天壤之别：

### 1. 调大小（发送量/干湿比）：Python 内存秒算
如果 Agent 的决策是“混响不够，加大 2dB”。
* **原理：** 发送量只是在调节影子的“浓淡”，混响内部的回声形状完全没变。
* **执行逻辑：** 引擎**不唤醒 REAPER**，直接读取提前离线渲染好的 100% `vocal_reverb_wet.wav` 缓存，在 Python 内存中利用 numpy 对音频数组进行增益乘法计算（如乘以 1.25），然后与干声 WAV 叠加。
* **耗时：** 0.01 秒（纯内存运算，真正的速度飞起）。

### 2. 调形状（Decay / Pre-delay）：必须回炉重造
如果 Agent 的决策是“把衰减时间（Decay）从 2 秒改成 3 秒”或“修改空间大小（Size）”。
* **原理：** 引擎不能把一个 2 秒的音频文件强行拉伸成 3 秒（会变调变慢）。改变这些参数意味着“影子的形状”变了，必须让混响核心算法重新去计算声波反射。
* **执行逻辑：** 引擎比对参数（Diff）发现算法核心参数变动，立刻作废旧的缓存，强行唤醒 REAPER，重新耗费算力跑一次全曲纯湿声的离线导出。
* **耗时：** 大约 10 ~ 15 秒。

**💡 给 Agent 的 Prompt 策略建议：**
> “优先通过调整发送量（Send Level）来解决空间感问题；除非严重不匹配，否则尽量不要去动 Decay 和 Size 等算法参数。”（以此来强行优化引擎的执行速度）

---

## 四、 引擎底层的“保姆”机制：封装 Abbey Road Trick

在专业混音中，直接把干声送进混响往往会导致低频轰鸣（Muddy）和高频刺耳（Sibilance）。人类通常会在混响前加一个 EQ 切除低频和极高频（即 Abbey Road 技巧）。

但作为自动化引擎，**绝不能指望 AI 懂这么细**去自己搭建 `[发送轨 -> EQ -> 混响]` 的微型链表。因此，将 `SendNode` 设计成了一个**微型黑盒组合**：
只要 Agent 下达包含空间意图的指令，底层的翻译器会自动在 REAPER 的发送轨里挂载：
1. **EQ（安全过滤器，底层锁死不开放给 AI）**
2. **Reverb（100% Wet 全湿状态）**
3. **Volume（用于控制最终 Send Level）**

Agent 只需要当个“指挥家”发号施令，所有去泥泞化（De-mudding）的物理动作和脏活累活全被底层架构默默包办消化了。

---

## 五、 代码蓝图：SendNode 的优雅集成

在原有的 `AudioNode` 基础上，新增 `SendNode` 继承自基础节点，但作为观察者，不占用主干链表的位置：

```python
class SendNode(AudioNode):
    def __init__(self, name, fx_type, source_node):
        super().__init__(name, fx_type)
        self.source_node = source_node  # 监听的主干节点
        source_node.add_observer(self)  # 注册为只读观察者

    def process(self, session):
        if not self.is_dirty and self.output_audio_path:
            return self.output_audio_path
            
        # 1. 自动拿 source_node（如主干最后一个EQ）的最新输出作为干声输入
        self.input_audio_path = self.source_node.output_audio_path
        
        # 2. 空类类插件不需要做 RMS 对齐，底层自动套用 Abbey Road EQ + Reverb 组合
        # 3. 在 REAPER 中离线渲染一条 100% Wet（全湿）的音频轨缓存
        self.output_audio_path = session.render_send(self.name, self.params)
        
        self.is_dirty = False
        return self.output_audio_path
```
