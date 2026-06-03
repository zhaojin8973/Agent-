# 空间效果器配置系统 — 流派预设 + 大师模板 V2

## Context

基于真实大师资料重写。流派参数需要在 REAPER 听验后定稿（当前为设计初稿），
大师模板完全按大师实际做法定义并使用 pop 作为统一参考风格。

---

## 一、流派空间链路（初稿，待 REAPER 听验调整）

### folk（民谣）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | — | — | — | — |
| Hall | LX480 | 1.5s | 60ms | 400/8000 |
| Room | ValhallaRoom | 0.8s | 10ms | 150/12000 |
| Slap/Rhythm | — | — | — | — |

### ballad（抒情）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | LX480 | 2.5s | 80ms | 200/10000 |
| Hall | LX480 | 3.0s | 60ms | 350/8000 |
| Room | ValhallaRoom | 1.2s | 10ms | 120/12000 |
| Slap | EchoBoy Echoplex | 100ms | — | 400/6000 |

### pop（流行）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | Little Plate | 2.0s | 40ms | 200/10000 |
| Hall | LX480 | 2.2s | 40ms | 400/8000 |
| Room | ValhallaRoom | 0.6s | 10ms | 180/12000 |
| Slap | EchoBoy StudioTape | 100ms | — | 500/6000 |
| Rhythm | EchoBoy 2290 | 1/4 Note | — | 500/6000 |

### rock（摇滚）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | LX480 | 1.5s | 30ms | 250/9000 |
| Hall | LX480 | 1.8s | 50ms | 450/7000 |
| Room | ValhallaRoom | 0.6s | 10ms | 200/11000 |
| Slap | EchoBoy Echoplex | 80ms | — | 600/5000 |
| Rhythm | EchoBoy MemoryMan | 1/8 Note | — | 500/5000 |

### electronic（电子）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | ValhallaPlate | 2.5s | 30ms | 300/8000 |
| Hall | LX480 | 3.5s | 30ms | 500/6000 |
| Room | ValhallaRoom | 0.4s | 5ms | 250/10000 |
| Slap | EchoBoy PingPong | 1/8D | — | 600/4000 |
| Rhythm | EchoBoy Digital | 1/4 Note | — | 600/4000 |
| Extra | Supernova | 氛围铺垫 | — | 500/6000 |

### chinese_folk_bel_canto（民美）
| 总线 | 插件 | Decay | Pre-Delay | HPF/LPF |
|------|------|-------|-----------|---------|
| Plate | LX480 | 2.0s | 50ms | 180/10000 |
| Hall | REV6000 | 2.8s | 40ms | 350/8000 |
| Room | ValhallaRoom | 1.0s | 10ms | 150/12000 |
| Slap | EchoBoy StudioTape | 100ms | — | 400/6000 |
| Rhythm | EchoBoy 2290 | 1/4 Note | — | 500/6000 |

---

## 二、四位大师空间模板（统一 pop 基准，完全按大师真实做法）

### Master A: Chris Lord-Alge

**出处**: Waves "Mixing with Depth" 系列, CLA Epic/EchoSphere 插件设计

**核心理念**:  8 路发送（4 延迟 + 4 混响），**延迟送入混响**是关键秘方

| 总线 | 插件（大师用） | 我们对应 | 参数 |
|------|--------------|---------|------|
| Slap Delay | 磁带延迟 ~100ms | EchoBoy (Echoplex) | 100ms, FB 10%, HPF 200Hz |
| Throw Delay | 长延迟 | EchoBoy (Echoplex) | 250ms, FB 15% |
| Tape Delay | 暗色磁带回声 | EchoBoy (SpaceEcho) | 80ms, FB 20%, LPF 3kHz |
| Plate Verb | 经典板混响 1.5-2.5s | Little Plate | Decay 2.0s, Pre-Delay 25ms |
| Room Verb | 自然房间 | ValhallaRoom | Decay 0.8s |
| Hall Verb | 音乐厅 | LX480 | Decay 2.2s |

**关键路由**: **所有延迟输出 → 送进混响**（延迟尾巴挂上混响光泽）

**特征**: HPF 激进而统一（延迟 200Hz, 混响 250Hz），发送量极保守（偏干）

---

### Master B: Ryan Hewitt

**出处**: PureMix "Complex Plate Reverb" (The Lumineers / Avicii 实际工程)

**核心理念**: 3 路 EMT 140 Plate，不同 Pre-Delay 创造时间层次 —「立体声→单声道崩塌」

| 路 | 插件（大师用） | 我们对应 | 参数 |
|----|--------------|---------|------|
| Plate 1 (Mono) | EMT 140 Plate A | ValhallaPlate (Mono) | Decay 3.0s, **Pre-Delay 100ms**, HPF 180Hz |
| Plate 2 (Stereo) | EMT 140 Plate B | ValhallaPlate (Stereo) | Decay 2.8s, **Pre-Delay 25ms**, HPF 250Hz |
| Plate 3 (Wide) | EMT 140 Plate C + 展宽 | ValhallaPlate + MicroShift | Decay 2.0s, **Pre-Delay 25ms**, HPF 180Hz, LPF 80Hz cut |

**关键**: 不用 Hall、不用 Room、不用 Delay。纯板混响三层。

**效果**: 0ms 干声 → 25ms 宽立体声涌入 → 100ms 单声道撞击 → 空间从宽变窄「崩塌」

---

### Master C: Serban Ghenea

**出处**: 多届格莱美年度制作人 (Taylor Swift/Adele/Bruno Mars)，访谈整理

**核心理念**: 极度透明、一致性。FabFilter 全栈。Sidechain Ducking 保证人声绝对清晰。

| 总线 | 插件（大师用） | 我们对应 | 参数 |
|------|--------------|---------|------|
| Plate | DMG TrackComp / 硬件板 | FabFilter Pro-R | Decay 1.8s, Post-EQ +2k |
| Hall | 硬件 Lexicon 480L | LX480 | Decay 2.2s, Pre-Delay 40ms |
| Room | 短数字混响 | ValhallaRoom | Decay 0.4s |
| Slap | 精准延迟 | EchoBoy (2290) | 100ms, FB 10% |
| Rhythm | 精准延迟 | EchoBoy (2290) | 1/4 Note, FB 20% |

**关键**: **所有返回轨挂 Sidechain 压缩**（人声干声触发，衰减 3-5dB）

**特征**: 最干净的空间 — 人声永远在最前面，空间只在句间「浮现」

---

### Master D: Devin Townsend

**出处**: Nail The Mix 大师课 "Genesis" 工程解密

**核心理念**: 不对称立体声延迟 → 便宜混响粘合 → 激进 EQ 过滤

| 信号链 | 插件（大师用） | 我们对应 | 参数 |
|--------|--------------|---------|------|
| L Delay | 任意立体声延迟 | EchoBoy (SpaceEcho) | **300ms**, FB 40%, 硬左 |
| R Delay | 同上 | EchoBoy (SpaceEcho) | **500ms**, FB 40%, 硬右 |
| Glue Verb | 低 CPU 混响（大师用"垃圾混响"）| Little Plate | Decay 1.5s |
| 后 EQ | 激进滤波 | Pro-Q 3 | HPF 400Hz, LPF 3kHz |

**关键**: 不用传统 Hall/Plate/Room。两个延迟的高 Feedback 产生类混响尾音。

**效果**: 人声周围有「雾状」空间但不占频率。适合密集编曲。

---

## 三、用户自定义模板机制

### 是什么

一套保存/加载/管理用户自己创建的空间链路配置的系统。

### 工作流

```
用户加载 pop 预设
  → REAPER 里调了 Plate Decay 从 2.0 改成 2.8
  → 把 LX480 换成了 REV6000
  → 觉得很好，想保存
  → hermes spatial save "My Pop Wet"
  → 下次可以直接 hermes spatial load "My Pop Wet"
```

### 存储方式

模板以 JSON 文件存储在用户目录：
```
~/.hermes/spatial-templates/
├── My Pop Wet.json
├── Ballad Dry.json
├── Zhang Custom.json
└── ...
```

每个 JSON 文件包含完整的 SpatialTemplate 定义：
```json
{
  "name": "My Pop Wet",
  "based_on": "pop",          // 基于哪个流派预设
  "description": "Plate +50%, Hall 用 REV6000 替 LX480",
  "buses": {
    "plate": {"plugin": "Little Plate", "decay_s": 2.8, ...},
    "hall": {"plugin": "REV6000", "decay_s": 2.5, ...},
    ...
  },
  "send_modifiers": {
    "reverb_plate": 2.0,      // +2 dB 相对于流派基准
    "reverb_hall": 0.0,
    ...
  }
}
```

### 继承链

```
内置流派预设 (pop/folk/...)
  └─ 用户自定义模板 A ("My Pop Wet")
       └─ 大师模板叠加 (Master C ducking)
            └─ 实时手动微调 (REAPER 里拧旋钮)
                 └─ 最终定稿
```

模板不修改内置预设，始终以「增量/覆盖」方式工作。用户可以随时回到原始预设。

### 为什么有价值

1. **项目沉淀**: 每首歌的混音经验变成可复用的模板
2. **A/B 对比**: 加载两个自定义模板快速切换对比
3. **跨项目复用**: 在《望归》上满意的空间方案可以直接用在下一首民美歌曲
4. **无损叠加**: 内置预设不受影响，自定义模板是「差异层」

---

## 四、实施计划

### Phase 1: 空间效果器参数设置
- 实现按流派设置混响/延迟的 Decay、Pre-Delay 等音色参数
- 当前 `build_spatial_chain` 只创建了插件但没设参数

### Phase 2: 大师模板
- 实现 4 个 Master 的独立模板定义
- 模板加载/切换逻辑

### Phase 3: 自定义模板
- JSON 存储/读取
- CLI: `hermes spatial save/load/list`
- 继承叠加逻辑

### Phase 4: REAPER 听验 + 参数定稿
- 每个流派在 REAPER 里用实际音频验证
- 根据听感调整 Decay/Pre-Delay/EQ 数值

---

## 五、验证

- 流派预设: 6 流派全部单元测试覆盖
- 大师模板: 4 模板参数验证 + 结构完整性测试
- 自定义模板: JSON 序列化/反序列化 + 继承链测试
