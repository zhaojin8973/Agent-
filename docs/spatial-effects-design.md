# 人声空间效果器（Reverb + Delay）设计方案

## Context

EQ 和压缩管线已完成（Pro-Q 3 → CLA-76 → RVox → Pro-DS → 总线压缩），
母带还缺 **Pro-L 2** 和 **The God Particle**。最核心的**空间效果器**
是最后一块拼图——这是区分业余和专业混音的关键环节。

### 可用混响/延迟插件库

| 插件 | 类型 | 最适合 |
|------|------|--------|
| Relab LX480 v4 | Lexicon 480L 仿真 | 主混响（Hall/Plate/Room） |
| Relab REV6000 | Lexicon 960L 仿真 | 现代数字混响 |
| UAD EMT 140 | 经典板式混响 | 人声光泽/温暖 |
| LiquidSonics Seventh Heaven | Bricasti M7 卷积 | 真实空间模拟 |
| LiquidSonics Cinematic Rooms | 电影级空间 | 宏大氛围 |
| LiquidSonics Lustrous Plates | 奢华板式 | 人声专用板混响 |
| ValhallaVintageVerb | 复古数字混响 | 创意空间/染色 |
| ValhallaRoom | 房间建模 | 近场空间 |
| ValhallaPlate | 板式混响 | 快速光泽 |
| ValhallaDelay | 创意延迟 | 节奏延迟/Pitch |
| FabFilter Pro-R | 现代算法混响 | 自然透明空间 |
| Supernova Verb | 创意混响 | 太空/氛围效果 |
| **Soundtoys EchoBoy** | **全能延迟（核心）** | **人声延迟首选 — 模拟 Echoplex/Space Echo/DM-2/TC 2290 等 30+ 种经典延迟** |
| Soundtoys SuperPlate | 现代板式混响 | EMT 140 风格，带 Modulation/Pre-EQ |
| Soundtoys Little Plate | 极简板式混响 | 快速高质量板混响 |
| Soundtoys MicroShift | 立体声扩展 | 和声/伴唱宽度、混响返回轨加宽 |
| Soundtoys Crystallizer | 粒子/移调延迟 | 创意人声特效、词尾投掷 |
| Soundtoys PrimalTap | Lo-Fi 延迟 | 复古 8-bit 延迟质感 |
| Soundtoys Decapitator | 饱和器 | 延迟/混响返回轨染色 |
| Soundtoys FilterFreak | 调制滤波 | 延迟/混响节奏化滤波 |
| Soundtoys PhaseMistress | 相位器 | 和声空间旋转感 |
| Soundtoys PanMan | 自动声像 | 延迟重复声像移动 |
| Soundtoys Tremolator | 颤音 | 节奏化空间脉冲 |

---

## 一、核心技术调研

### 1. 三重混响分层法（Ryan Hewitt — 格莱美获奖工程师）

使用 **三台 EMT 140** 创造丰富的、随时间演变的空间感：

| 参数 | Reverb 1 (Mono) | Reverb 2 (Stereo) | Reverb 3 (Wide) |
|------|-----------------|---------------------|------------------|
| 板型号 | A | B | C |
| Decay | ~3.0s | <3.0s | ~2.0s |
| Pre-Delay | **100ms** | **25ms** | **25ms** |
| HPF | 180Hz | 250Hz | 180Hz |
| 宽度 | Mono | Stereo | 加宽 |

**时间演变**：
1. 0ms: 干声
2. 25ms: Reverb 2+3 先进入（立体声两侧）
3. 100ms: Reverb 1 进入（单声道中心）
4. 2s: Reverb 3 衰减完毕
5. 3s: Reverb 1 最后衰减完毕

**效果**：混响先宽后窄，从立体声扩散 "崩塌" 回单声道中心。

### 2. 标准多总线架构（各风格通用模板）

```
Master Bus
├── Submaster（所有轨道汇总）
│
├── Vocal Bus
│   ├── Lead Vocal（干声 → EQ → CL-76 → RVox → Pro-DS）
│   │   ├── Send → Plate Reverb Bus（EMT 140 / Lustrous Plates）
│   │   ├── Send → Hall Reverb Bus（LX480 / REV6000）
│   │   ├── Send → Room Reverb Bus（ValhallaRoom / Cinematic Rooms）
│   │   ├── Send → Slap Delay Bus（ValhallaDelay）
│   │   ├── Send → 1/4 Note Delay Bus（ValhallaDelay）
│   │   └── Send → Throw Delay Bus（自动化发送）
│   └── Backing Vocals（和声 → 更多混响、更宽、更暗）
│
├── Backing Bus
│   └── 伴奏轨道
│       ├── Send → Room Reverb（统一空间）
│       └── Send → Hall Reverb（少量）
│
├── Reverb Return 1: Plate（人声专用光泽）
│   ├── 输入 HPF @ 200Hz
│   ├── 输入 LPF @ 10kHz
│   └── 输出 3kHz 窄 Q 衰减（去齿音共振）
│
├── Reverb Return 2: Hall（空间维度）
│   ├── 输入 HPF @ 400Hz
│   ├── 输入 LPF @ 6kHz
│   └── Sidechain 压缩（干声触发 ducking）
│
├── Reverb Return 3: Room（近场黏合）
│   ├── 输入 HPF @ 150Hz
│   ├── 短 Decay（0.5-1.2s）
│   └── 少量发送统一空间感
│
├── Delay Return 1: Slap（厚度）
│   ├── 80-120ms
│   ├── HPF @ 150Hz, LPF @ 5kHz
│   └── 窄立体声
│
├── Delay Return 2: 1/4 Note（节奏延迟）
│   ├── BPM 同步
│   ├── HPF @ 500Hz, LPF @ 8kHz
│   └── 低 Feedback（1-2次重复）
│
└── Delay Return 3: Throw（自动化特效）
    ├── BPM 同步（1/8 或 1/4）
    ├── 仅在特定词尾发送
    └── 可送 → Hall Reverb（Delay → Reverb 链）
```

### 3. 流派空间设计速查表

| 流派 | 主混响 | 辅助混响 | 延迟 | 空间特征 |
|------|--------|---------|------|---------|
| **民谣** (folk/ballad) | Room (ValhallaRoom) 0.8-1.5s | 短Hall 1.2-1.8s | EchoBoy **Echoplex** 温暖少量 | 自然、亲近、保留呼吸感 |
| **流行** (pop) | Plate (EMT140/Lustrous) 1.5-2.5s | Hall 2.0-3.0s + Slap Delay | EchoBoy **Studio Tape** 1/4 + Throw (Crystallizer) | 光泽、宏大副歌、创意自动化 |
| **摇滚** (rock) | Room (ValhallaRoom) 1.2-2.0s | Plate (SuperPlate) 1.0-1.8s | EchoBoy **Echoplex** Slapback 80-120ms | 力量、实在感、不过分湿润 |
| **电子** (electronic) | Hall (LX480) 2.0-4.0s | Supernova Verb + Crystallizer | EchoBoy **Ping Pong** Dotted 1/8 | 宏大、氛围、实验性 |
| **民美** (chinese_folk_bel_canto) | Hall (REV6000) 1.8-2.5s | Room 0.8-1.2s + Plate (Lustrous) 1.5-2.0s | EchoBoy **Memory Man** 少量 1/4 | 宏大但自然、民族韵味 |
| **抒情** (ballad) | Plate (Lustrous/SuperPlate) 2.0-3.5s | Room 1.0-1.5s + Hall 2.5-4.0s | EchoBoy **Echoplex** 长尾温暖 | 浪漫、悠长、情感饱满 |

### 4. 核心参数对照表

#### Pre-Delay（干声与混响分离）
| 歌曲速度 | 推荐 Pre-Delay | 效果 |
|---------|---------------|------|
| 快速 (120+ BPM) | 20-40ms | 保持清晰 |
| 中速 (80-120 BPM) | 40-80ms | 自然分离 |
| 慢速 (<80 BPM) | 80-120ms | 戏剧性延迟进入 |

#### Decay Time（混响时长）
| 风格 | 主混响 | 辅助混响 |
|------|--------|---------|
| 民谣 | 0.8-1.5s | 1.2-1.8s |
| 流行 | 1.5-2.5s | 2.0-3.0s |
| 摇滚 | 1.0-1.8s | 1.5-2.0s |
| 电子 | 2.0-4.0s | 3.0-6.0s |
| 民美 | 1.8-2.5s | 1.5-2.0s |
| 抒情慢歌 | 2.0-3.5s | 2.5-4.0s |

#### Delay 时间设计
| 类型 | 时间 | Feedback | 用途 |
|------|------|----------|------|
| Slapback | 80-120ms | 1次 | 增加厚度、模拟双轨 |
| 1/4 Note | BPM × 0.25 | 1-2次 | 节奏填充、清晰重复 |
| 1/8 Note | BPM × 0.125 | 1-2次 | 快速节奏、电子风格 |
| Dotted 1/8 | BPM × 0.375 | 1-3次 | 经典 U2/The Edge 延迟 |
| Tape Echo | BPM / 4 | 温暖衰退 | 民谣/抒情温暖感 |
| Throw | BPM 同步 | 高 Feedback | 自动化特效、词尾投掷 |

### 5. 高级技巧

#### A. 返回轨 EQ（最关键的习惯）
```
每个混响/延迟返回轨必须做 EQ：

Plate Return:  HPF @ 200Hz | LPF @ 10kHz | 5-8kHz 窄Q -2dB（去齿音共振）
Hall Return:   HPF @ 400Hz | LPF @ 6kHz  | 300-600Hz -2dB（去鼻音）
Room Return:   HPF @ 150Hz | LPF @ 12kHz | 平坦
Delay Return:  HPF @ 500Hz | LPF @ 5kHz  | 1-3kHz 略微提升（清晰度）
```

#### B. Sidechain Ducking（空间随人声呼吸）
```
在混响/延迟返回轨插入压缩器：
- Sidechain 输入 = 干声轨道
- Threshold: 干声触发时衰减 -3 到 -6 dB
- Attack: 快 (1-5ms)
- Release: 中 (50-200ms)
效果：人声唱时空间收缩，句间空间自然恢复
```

#### C. Delay → Reverb 串联
```
干声 → Send → Delay (Slap 80-120ms) → Reverb (Plate)
效果：延迟先给人声厚度，然后混响把延迟尾巴融入空间
这是比直接用混响 Pre-Delay 更"活"的做法
```

#### D. 自动化
```
- 副歌：Plate 发送 +3dB, Hall 发送 +4dB
- 尾音词：Throw Delay 发送瞬间拉满
- 过渡段：Hall Decay 自动化延长
- 最后一句：混响尾音渐弱保留
```

#### E. Soundtoys EchoBoy — 延迟核心引擎

EchoBoy 是 Soundtoys 的旗舰延迟插件，内置 **30+ 种经典延迟仿真**
（Echoplex、Space Echo、DM-2、TC 2290、Memory Man 等），是声乐延迟的终极工具：

**流派延迟选型**：

| 风格 | EchoBoy Style | 时间 | 特点 |
|------|-------------|------|------|
| 民谣 | **Echoplex** | BPM/4 | 温暖、有弹性的磁带饱和，低反馈 1-2 次 |
| 流行 | **Studio Tape** 或 **TC 2290** | 1/4 Note | 干净/精准节奏延迟 |
| 摇滚 | **Echoplex** 或 **Space Echo** | 80-120ms | Slapback，有砂砾感的磁带音色 |
| 电子 | **Digital Delay** 或 **Ping Pong** | Dotted 1/8 | 精确、宽广、节奏化 |
| 民美 | **Memory Man** | BPM/4 | 温暖模拟延迟，不过分突出 |
| 抒情慢歌 | **Echoplex** 长反馈 | BPM × 0.5 | 悠长尾音，配合混响柔软衰减 |

**EchoBoy 关键参数控制**：

```
Style:       Echoplex / Space Echo / Studio Tape / TC 2290 / Memory Man / Ping Pong
Echo Time:   BPM 同步 (1/4, 1/8, 1/8D, 1/2)
Feedback:    1-3 次重复（人声）；高反馈 + 自动化 = 投掷特效
Mix:         100% Wet（Send/Return 方式）
Saturation:  2-4（增加模拟温暖感，不抢眼）
Low Cut:     200-500Hz（清除低频堆积）
High Cut:    3-8kHz（暗化重复，保持在人声后面）
Modulation:  微量（给重复增加"活着"的感觉，不跟人声打架）
```

**Crystallizer 人声特效**：

```
用于 Throw Delay 场景：
- 词尾单独词汇 → Crystallizer 投掷
- Pitch + Octave Up/Down + Reverse Grain
- 制造"碎玻璃"或"星空闪烁"的尾音效果
- 配合自动化仅在特定词触发
```

**MicroShift 在空间链中的应用**：

```
用途 1: 和声/伴唱宽度
  - 插入 MicroShift 在和声轨道
  - 左右略微 detune（±8 cents）+ 微小延迟（10-20ms）
  - 不增加混响就能让和声"展开"

用途 2: 混响返回轨增宽
  - Plate Reverb 返回轨插入 MicroShift（微量）
  - 让混响尾音在立体声场中更宽
  - 但不要推太高，会导致相位问题

用途 3: 延迟返回轨增宽
  - Delay Return 插入 MicroShift
  - 让延迟重复在左右声道间"浮动"
```

### 6. 大师模板参考

#### A. Ryan Hewitt 三 Plate 法
- 3 × EMT 140（A/B/C 板）
- 不同 Pre-Delay 创造时间层次
- Mono/Stereo/Wide 三种宽度
- 适合：慢歌、抒情、民谣

#### B. Devin Townsend "Dev Lay" 法 (密集混音)
- 两路 Mono Delay L/R: 300ms / 500ms
- 高 Feedback 产生类混响尾音
- 后接"便宜混响"粘合
- 激进 EQ 过滤
- 适合：摇滚、电子、密集编曲

#### C. Jaycen Joshua The God Particle 母带链
```
Mix Bus:
  1. 增益 staging（Kick 打 -5dB）
  2. The God Particle（默认设置，混合"穿过去"）
  3. Pro-L 2（最终限制器，+5dB gain，-1dB True Peak）
  
TGP 默认设置等同 Jaycen Joshua 的个人混音总线：
  - 3dB 150Hz 低频提升
  - 3dB 2.6kHz 中频衰减
  - 多段压缩（低段 0.5-1.5dB GR，中高段 0-1dB）
  - 奇次谐波饱和
  - 立体声宽度
  - 内置限制器（可关，用 Pro-L 2 替代）
```

#### D. Chris Lord-Alge 风格
- SSL 风格总线压缩 + 多路混响
- 激进的高通滤波（混响返回 HPF 高达 600Hz）
- 短 Plate + Slap Delay 组合
- 适合：摇滚、流行朋克

### 7. 常见问题与解决

| 问题 | 原因 | 解决 |
|------|------|------|
| 人声被淹没 | 湿声太多 / 无 Pre-Delay | 降低发送量 / 增加 Pre-Delay |
| 混响浑浊 | 低频堆积 | 返回轨 HPF 200-500Hz |
| 齿音放大 | Plate 高频共振 | 返回轨 5-8kHz 窄 Q -2dB |
| 空间感"平" | 只有一种混响 | 2-3 路混响分层 |
| 延迟盖过人声 | 延迟音量太大 | Sidechain Ducking |
| 混音不统一 | 每轨混响不同 | 至少 1 路 Room Reverb 共享 |

---

## 二、实施计划

### Phase 1: 母带插件（先补齐基础设施）

**Pro-L 2 限制器**
- 在 `_MASTER_LIMITER` 基础上实现 Pro-L 2 参数自动化
- 关键参数：Gain (from finalize_master), True Peak Ceiling (-1.0 dB), Style (Transparent/Allround)
- 位置：The God Particle 之后，最终输出之前

**The God Particle**
- 作为混音总线插件插入
- 默认设置即可（等同 Jaycen Joshua 个人设置）
- 需要实现：Input trim / Amount knob / Limiter on-off
- 位置：总线压缩（bx_townhouse）之后，Pro-L 2 之前

### Phase 2: 空间效果器框架

**首选插件分配**（基于你的插件库）：

| 角色 | 首选插件 | 备选 |
|------|---------|------|
| 主 Plate 混响 | **Soundtoys SuperPlate** 或 **UAD EMT 140** | LiquidSonics Lustrous Plates |
| 主 Hall 混响 | **Relab LX480 v4** | Relab REV6000 |
| Room 混响 | **ValhallaRoom** | LiquidSonics Cinematic Rooms |
| 人声延迟 | **Soundtoys EchoBoy** | ValhallaDelay |
| 创意投掷 | **Soundtoys Crystallizer** | — |
| 和声加宽 | **Soundtoys MicroShift** | — |
| 延迟/混响饱和 | **Soundtoys Decapitator**（微量） | — |

**音轨架构**
1. 为每条人声轨道创建 6 路 Send：
   - Send → Plate Reverb（SuperPlate/EMT 140）
   - Send → Hall Reverb（LX480/REV6000）
   - Send → Room Reverb（ValhallaRoom）
   - Send → Slap Delay（EchoBoy — Echoplex mode, 80-120ms）
   - Send → Rhythm Delay（EchoBoy — 1/4 BPM sync）
   - Send → Throw Delay（Crystallizer — 自动化控制）

2. 返回轨处理链：
   - 每个 Reverb Return: HPF → EQ → Sidechain Comp（干声触发 ducking）
   - 每个 Delay Return: HPF → LPF → Sidechain Comp
   - Room Return 可选加 MicroShift（微量增宽）

3. EchoBoy 参数按流派自动化：
   - Style 选择（Echoplex / Studio Tape / Memory Man / Ping Pong）
   - Saturation 值
   - Echo Time BPM 同步
   - Feedback 值
   - Low Cut / High Cut

**genre 参数表**
- 新增 `_GENRE_REVERB_CONFIG`：Plate/Hall/Room 的 decay、pre-delay、EQ 频率
- 新增 `_GENRE_DELAY_CONFIG`：EchoBoy Style、时间、feedback、saturation
- 新增 `_GENRE_SPATIAL_CONFIG`：组合以上两者 + 发送量

### Phase 3: 端到端集成

- 将空间效果器集成到 `apply_profile()` 流程中
- 在 `post_fx_balance` 之后、`finalize_master` 之前执行
- 添加流派感知的空间参数测试

---

## 三、验证计划

1. 单元测试：空间参数表完整性（所有流派覆盖）
2. 集成测试：在 REAPER 中验证混响/延迟轨道创建
3. 听感验证：每个流派用实际音频跑一遍，主观评估空间感
