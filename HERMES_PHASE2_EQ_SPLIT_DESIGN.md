# hermes-core Phase 2：串联链 EQ 拆分与 SSL EQ 集成方案

## 一、 背景与目标

在 Phase 1 中，`spectrum.py` 成功实现了纯数学的频谱分析，但 EQ 推导是“solo 模式”，每个 EQ 独立跑满 6 条规则。这在真实工业管线中是不成立的。
真实的 Vocal Chain 必须各司其职：
`Pro-Q 3 (数字减法) → CLA-1176 (削峰) → SSL EQ (模拟加法) → RVox (平衡)`

**Phase 2 核心目标：**
1. **职责隔离：** 按 `pre-comp`（修正减法）和 `post-comp`（塑形加法）拆分 EQ 推导规则。
2. **插件扩展：** 集成 SSL EQ，并完成物理参数的归一化映射。
3. **安全防御：** 在 `pre-comp` 阶段实现 Headroom 保护（增益越界自动回调）。

---

## 二、 核心重构设计

### 1. 压前与压后的职责拆分 (eq_position)

在 `_derive_eq_intent` 中引入 `position` 参数，对 6 条规则进行硬截断：

**EQ1 (pre-comp): 纯粹的修正与减法（由 FabFilter Pro-Q 3 执行）**
| 规则 | 动作 |
| :--- | :--- |
| HPF | 低切，清理 80Hz 以下杂音 |
| 共振峰切 | 窄峰 (Q>15) Bell 衰减 |
| 浑浊衰减 | 350Hz 宽 Bell 衰减 |
| *存在感/空气感* | **严格跳过 (Skip)** |

**EQ2 (post-comp): 纯粹的塑形与加法（由 SSL EQ 执行）**
| 规则 | 动作 |
| :--- | :--- |
| 存在感提升 | HMF Bell 提升 |
| 空气搁架 | HF Shelf 提升 |
| 低频温暖 | LF Shelf 轻微提升 (可选) |
| *HPF/共振/浑浊* | **严格跳过 (已在压前处理)** |
| Analog | 永远保持开启 (1.0) |

### 2. EQ1 的 Headroom 保护机制

为了防止 Pro-Q 3 意外提升导致下游 1176 模拟建模压缩器输入过载（偏离 -18dBFS 甜点区），在生成 EQ1 参数时硬编码动态余量保护：
```python
total_boost = sum(max(0, band.gain_db) for band in eq_intent.bands)
if total_boost > 0:
    # Pro-Q 3 的 Output Gain 范围通常为 ±36dB，需要查阅实际映射公式
    # 此处衰减输出，抵消总提升量，确保不爆音
    params["Output Level"] = normalize_gain(-total_boost)
```

### 3. SSL EQ (Waves) 的翻译器集成

在 `normalize.py` 的 `PLUGIN_REGISTRY` 中注册 Waves SSLEQ Mono 的全部参数。
相比于全数字的 Pro-Q 3，SSL EQ 是基于经典模拟调音台建模的，其参数通常是**步进式（Stepped）**而非连续线性的。翻译器 `_apply_ssleq_eq` 需要实现：
1. **频段映射：** 存在感提升映射到 `HMF`，空气感映射到 `HF`。
2. **步进对齐：** 如果 Agent 要求的频率是 3500Hz，翻译器需自动对齐到 SSL EQ `HMF Frq` 旋钮上最近的物理档位（如 3kHz 或 4kHz）。

---

## 三、 文件改动与管线流程

### 涉及文件：
1. `normalize.py`: 注册 SSLEQ 及其步进查表逻辑。
2. `profiles.py`: 定义工业级 `DEFAULT_VOCAL_CHAIN` (`Q3 → 1176 → SSL EQ → RVox`)，并标注 `eq_position`。
3. `engine.py`: 
   - 修改 `_build_audio_chain`，将 `eq_position` 传递给分析器。
   - 修改 `_derive_eq_intent`，根据 position 筛选规则。
   - 新增 `_apply_ssleq_eq()` 翻译器。

### 测试策略 (Pytest)：
1. **拆分测试**：给 `_derive_eq_intent` 喂同一个 `SpectrumReport`，断言 `pre` 模式下 `EqIntent` 不包含高频提升，`post` 模式下不包含低切衰减。
2. **保护测试**：构造一个带有 +3dB 提升的恶意 `EqIntent` 给 Pro-Q 3 翻译器，断言返回的参数字典中 `Output Level` 成功衰减了相应的数值。
3. **SSL 映射测试**：验证输入任意频率，都能正确捕捉到 SSL EQ 旋钮的合法档位。