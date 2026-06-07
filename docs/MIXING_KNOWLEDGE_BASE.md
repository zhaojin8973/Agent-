# Hermes 混音知识库

> 自动推导的参考依据。全插件库可用。
> 每条规则标注出处：大师实践 / 声学原理 / 厂商文档 / 网上印证。
> 最后更新：2026-06-07

---

## 一、完整人声处理链（9 段 Insert + AUX）

```
Insert: Pro-Q 3 → UAD 1176 Rev A → Decapitator → Pro-DS
      → Pultec EQP-1A → RVox → Oxford Inflator → UAD CL 1B
      → Maag EQ4

AUX:   MicroShift (100% wet) → Pro-Q 3 (MS HPF 500Hz)
       → Reverb ×3（流派专属配对）→ Delay ×3（EchoBoy）
       → Blackhole（仅 electronic）
```

| # | 插件 | 连接 | 为什么在这里 | 来源 |
|---|------|------|------------|------|
| 1 | Pro-Q 3 | Insert #1 | HPF 清理 + 动态共振抑制——压缩前切除垃圾频率 | Amped Studio / Rys Up 2026 |
| 2 | UAD 1176 Rev A | Insert #2 | FET 峰值压缩——抓瞬态、增能量 | UAD 官方 / Greg Wells |
| 3 | Decapitator | Insert #3 | 谐波饱和——放压缩**后**避免 clip gain 峰值过载失真 | Soundtoys 官方博客 / r/edmproduction |
| 4 | Pro-DS | Insert #4 | 齿音消除——1176 会突出齿音，紧随其后抓 | Gearspace / Rys Up 2026 |
| 5 | Pultec EQP-1A | Insert #5 | 电子管染色——60Hz 经典推拉 Trick + 高频光泽 | UAD 官方手册 / Greg Wells 终极链 |
| 6 | RVox | Insert #6 | RMS 体压缩——单旋钮稳坐混音 | Reddit "1176→Pultec→RVox" |
| 7 | Oxford Inflator | Insert #7 | 谐波密度——不靠压缩让声音「变大」 | Sonnox 官方 / Produce Mix Fix |
| 8 | UAD CL 1B | Insert #8 | 光电体压缩——tube 温暖塑形，2:1~4:1 | KMR Audio / Gearspace / Sweetwater |
| 9 | Maag EQ4 | Insert #9 | Air Band 抛光——20kHz 极高频空气感 | Song Mix Master / Md3sign Studio |
| — | MicroShift | **AUX Send** | 立体声展宽——100% wet, 副歌多送 | URM Academy / SonicScoop / Reddit |
| — | Reverb ×3 | AUX Send | 空间——流派专属混响器配对 | iZotope 2026 / LiquidSonics 官方 |
| — | Delay ×3 | AUX Send | 节奏/密度——EchoBoy 磁带延迟 | Soundtoys 官方 / Music Guy Mixing |

> **⚠️ 纠正 (2026-06-07)**：MicroShift 之前被设计为 Insert，经网上印证后更正为 AUX Send（与 Reverb/Delay 同类）。Delay 同样更正为 AUX Send。

---

## 二、1176 类压缩器专节

### ⚠️ 反向旋钮逻辑

1176 的 Attack 和 Release **顺时针 = 更快**。跟绝大多数压缩器相反。

| 旋钮位置 | Attack | Release |
|---------|--------|---------|
| 7（CW 到底）| 最快（~20μs）| 最快（~50ms）|
| 1（CCW 到底）| 最慢（~800μs）| 最慢（~1.1s）|

> 来源：Universal Audio 1176LN 官方手册

### UAD 1176 Collection（人声首选）

| 版本 | REAPER 插件名 | 特点 | 适合 |
|------|-------------|------|------|
| **Rev A** (Bluestripe) | `VST3: 1176 Rev A Compressor (Universal Audio)` | 最饱和、奇偶谐波 3:2、复古 | **人声首选** |
| Rev E (Blackface/LN) | `VST3: 1176LN Rev E Compressor (Universal Audio)` | 更干净、线性、低噪 | 精确控制场景 |
| AE (40周年) | `VST3: 1176AE Compressor (Universal Audio)` | 2:1 低比率 | 轻压缩 |

> Hermes 默认使用 **Rev A**。来源：UAD 1176 Collection 官方手册。

### 厂商差异

硬件 1176 没有两台完全一样的。插件同理：

| 厂商 | Attack | Release | 谐波 | 适合 |
|------|--------|---------|------|------|
| UAD 1176 Rev A | CW=快 | 更智能的释放曲线 | 奇偶 3:2, 复古 | 贝斯、鼓、摇滚人声 |
| Waves CLA-76 | CW=快 | 稍拖尾、"呼吸感" | 偶次为主, 温暖 | 人声、钢琴、流行 |
| Arturia 1176 | CW=快 | 平衡 | 温和 | 通用 |
| Softube FET | CW=快 | 线性 | 干净 | 贝斯 DI、精确 |

> 同名不同厂，即使写相同 0-1 值，结果不同。每个厂应独立注册。

### 人声 Ratio 选择

| Ratio | 听感 | 适用 |
|-------|------|------|
| 4:1 | 自然、保留动态 | folk / ballad / 民美 |
| 8:1 | 紧凑、有控制力 | pop / rock |
| 12:1 | 激进、贴脸 | electronic |

> 来源：UAD 官方手册（4:1/8:1/12:1 通用压缩，20:1 峰值限幅）+ WeTheSound Guide

---

## 三、CL 1B 光电压缩器专节

### 人声标准设置

| 参数 | 典型值 | 来源 |
|------|--------|------|
| Ratio | 2:1 ~ 4:1 | KMR Audio: "2:1 to 3:1" |
| Attack | 12-2 点（慢）| Gearspace 说唱人声 |
| Release | 12-2 点（中快）| Reddit r/audioengineering |
| GR | 2-5 dB | KMR Audio / Gearspace |
| Mode | Manual | 获取完整控制 |

> Sweetwater 报告有用户用 6:1 但这是极端嘻哈场景，不作为默认。

### CL 1B vs LA-2A

| | CL 1B | LA-2A |
|------|------|------|
| 控制 | Attack/Release/Ratio 全可控 | 仅 Peak Reduction + Gain |
| 染色 | Tube 温暖 | 光电平滑 |
| 定位 | "有完整控制的 LA-2A" | "傻瓜式经典" |

> 来源：Mix Protégé 论坛、Gearspace

---

## 四、Decapitator 饱和器专节

### 人声正确用法

| 参数 | 设置 | 说明 |
|------|------|------|
| Style | **E** (EMI) 或 **A** (Ampex) | 最平滑的人声模式 |
| Drive | **1-3** /10 | 极低起步，听到失真就退 |
| Mix | 30-50% | 干湿混合，不是全湿 |
| Tone | 12 点方向（0.5）| 微微右旋削毛刺 |

> **⚠️ 关键认知**：Drive 从 1 开始。Decapitator 人声目的不是过载失真而是谐波厚度。来源：Soundtoys 官方博客 / Music Guy Mixing。

### 饱和前后位置

| 位置 | 效果 | 适用 |
|------|------|------|
| 压缩**前** | 毛躁、过载感、压缩器吃谐波 | 鼓、贝斯 |
| 压缩**后** | 柔和、微妙增厚、不削波 | **人声（推荐）** |

> 来源：r/edmproduction、AudioSpectra。Hermes 默认放压缩后。

---

## 五、Oxford Inflator 专节

### 人声用法（保守）

| 参数 | 设置 | 说明 |
|------|------|------|
| Effect | **20-30%** | 人声上超过 50% 会明显失真 |
| Curve | **负值（0.0）** | 最透明模式 |
| Clip 0dB | **OFF** | 不削波，仅谐波增强 |
| Input/Output | 0 dB / -0.5 dB | 防推大下一级 |

> **⚠️ 纠正**：Inflator 不是限幅器——Sweetwater 用户明确指出 "NOT a limiter"。人声上必须保守使用。来源：Produce Mix Fix / Sonnox 官方教程 / KVR Audio。

---

## 六、Pultec EQP-1A 专节

### 经典「推拉」Trick

同时 Boost + Atten **同一低频**，产生谐振谷 + 增强冲击力。这是 Pultec 被称为「魔法」的原因。

```
Low Freq: 60Hz
Low Boost: 3-4
Low Atten: 1-3（根据 mud 调节）
→ 效果：低频既厚又不浑
```

### 高频用法

```
High Freq: 8kHz (正常) / 12kHz (暗声)
High Boost: presence_deficit × 0.4 (0~5)
High Atten: 20kHz 视齿音情况（sibilance>-30 → Atten 2-3）
High BW: 5（宽Q）
```

> 来源：UAD Pultec 官方手册 / MusicRadar / Mix:analog / Penny Cool

---

## 七、Maag EQ4 Air Band 专节

| 参数 | 设置 | 说明 |
|------|------|------|
| Air Freq | 10kHz (folk/ballad/民美) / 20kHz (pop/rock/elec) | |
| Air Boost | 0-6 | deficit × 0.3 + |air| × 0.2 |
| 160Hz | mud<-3 → Boost 1-2 | 补瘦声 |
| 2.5kHz | tilt_dark → Boost 1-2 | 补亮度 |

> Air Band 提的不是频率而是「空气感」——电子管输出级即使不推也会加微妙模拟质感。来源：Song Mix Master / Md3sign Studio。

---

## 八、MicroShift 专节（⚠️ 重要纠正）

### 连接方式：AUX Send，不是 Insert

| 来源 | 明确说法 |
|------|---------|
| URM Academy | "Place your stereo widener on a **parallel AUX channel**, Mix 100% wet" |
| SonicScoop | "as a traditional **parallel FX return**, blend with the return fader" |
| Reddit r/audioengineering | "parallel aux send during the chorus to make it wider" |
| YouTube 教程 | 标题直接叫 "**Wide Vocals Send**" |

### 标准设置

```
MicroShift AUX:
  Mix: 100% (AUX 全湿，发送量控制比例)
  Detune: 0.15
  Delay: 0.08
  Focus: 1-5kHz

→ Pro-Q 3 (MS 模式, Side 通道 HPF 500Hz — URM 推荐)
→ Send → 主唱混响/延迟（共享空间，融为一体）
```

> 来源：URM Academy / SonicScoop / Soundtoys 官方手册

---

## 九、压缩器 Attack / Release 与 BPM

### 公式
```
1/4 拍 (ms) = 60000 ÷ BPM
```

| BPM | 1/4 | 1/8 | 1/16 | 1/32 |
|-----|-----|-----|------|------|
| 60 | 1000 | 500 | 250 | 125 |
| 80 | 750 | 375 | 188 | 94 |
| 100 | 600 | 300 | 150 | 75 |
| 120 | 500 | 250 | 125 | 62.5 |
| 140 | 429 | 214 | 107 | 54 |
| 160 | 375 | 188 | 94 | 47 |

### BPM 预设（1176 用）

```python
FAST  = {"attack_ms": 3.0,  "release_ms": 60.0}   # BPM > 130
MED   = {"attack_ms": 5.0,  "release_ms": 100.0}  # BPM 90-130
SLOW  = {"attack_ms": 10.0, "release_ms": 200.0}  # BPM < 90
```

### Attack 推导（人声）

| Attack | 用途 | 对应音符 |
|--------|------|---------|
| 1-3ms | 激进（摇滚、说唱）| ~1/64 |
| 3-10ms | 标准人声 | ~1/32-1/16 |
| 10-30ms | 柔和（民谣、爵士）| ~1/16-1/8 |

### Release 推导

| Release | 听感 | 对应音符 |
|---------|------|---------|
| 40-80ms | 紧凑、有节奏感 | 1/16 |
| 80-150ms | 自然呼吸 | 1/8 |
| 150-300ms | 平滑、胶水感 | 1/4 |

---

## 十、压缩器类型 Reference

| 类型 | 代表 | Attack | Release | 染色 | 链中角色 |
|------|------|--------|---------|------|---------|
| FET | 1176 | 极快(20μs-800μs) | 快(50ms-1.1s) | 中-强 | #2 削峰/能量 |
| Opto | CL 1B, LA-2A | 慢(~10ms) | 慢(60ms-5s) | 暖 | #8 塑形/tube |
| VCA | SSL G, bx_townhouse | 可调 | 可调 | 干净 | Master 总线 |
| Vari-Mu | Fairchild, Manley | 慢(0.2-2ms) | 极慢(0.3-25s) | 强 | 厚声/母带 |

---

## 十一、EQ 频率 Reference

| Hz | 名称 | 操作 |
|----|------|------|
| 20-80 | Sub rumble / 风噪 | HPF 切除 |
| 100-200 | 胸声 / 体重感 | 保留（男声）/ 轻切（女声）|
| 250-500 | 浑浊 / 鼻音 | 宽切 -2~-4dB |
| 2k-5k | 存在感 / 清晰度 | Bell 提 +1~+3dB |
| 5k-8k | 齿音区 | De-esser |
| 8k-16k | 空气感 / 光泽 | HS +1~+2dB |

### 四套 EQ 风格

| 风格 | HPF | Cut | Boost | 听感 |
|------|-----|-----|-------|------|
| Abbey Road | 85Hz | 350Hz -2.5 | 2.8k +2.5, 8k HS +2 | 温润、圆滑 |
| Modern Pop | 100Hz/18dB | 250Hz -3 | 5k +3.5, 10k HS +3 | 凌厉、靠前 |
| R&B Warm | 60Hz 温和 | 400Hz 宽 -1.5 | 150 +1.5, 3k +1.5 | 丰满、保持胸声 |
| Hip-Hop | 120Hz 陡 | 300Hz -4 | 4-5k +4~6, 8k HS +2 | 干、近、咄咄逼人 |

---

## 十二、混响器流派配对

| AUX | folk | ballad | pop | rock | electronic | 民美 |
|-----|------|--------|-----|------|-----------|------|
| Room/Short | LiquidSonics Seventh Heaven Wood Room | Seventh Heaven Boston Hall A | UAD EMT 140 (1.2s) | UAD EMT 140 (1.5s) | Relab LX480 Plate | Seventh Heaven Concert Hall |
| Plate/Medium | Relab LX480 Random Hall (浅) | LX480 Concert Hall | LX480 Rich Plate | Reverb Foundry Tai Chi Vocal Cavern | Nuro Supernova Big Room | LX480 Rich Plate |
| Hall/Long | LiquidSonics Cinematic Rooms Scoring Light | Cinematic Rooms Amethyst Hall | Seventh Heaven Sunset Chamber | LX480 Random Hall | Eventide Blackhole Ambient | Cinematic Rooms Cathedral |

> **回退规则**：流派首选插件不存在 → `ValhallaVintageVerb` (对应模式)。
> 来源：iZotope 2026 最佳人声混响指南、LiquidSonics 官方博客、Reddit r/audioengineering

---

## 十三、混响自动化参数

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| Decay | BPM × 流派 | BPM<80→×1.3, BPM 80-120→×1.0, BPM>120→×0.7 |
| PreDelay | crest_factor_db | crest>15→40ms, 10-15→25ms, <10→15ms |
| HPF (返回EQ) | mud_ratio_db | mud>0→HPF 250→400Hz; mud<0→200Hz |
| LPF (返回EQ) | sibilance_peak_db | sibilance>-28→LPF 10→6kHz; sibilance<-32→12→16kHz |

> 来源：Music Guy Mixing、Unison Audio、Slate Digital、Sound On Sound

---

## 十四、段落自动化偏移

| 段落 | 混响 Decay | 混响 PreDelay | 混响 Send | 延迟 Send | MicroShift Send |
|------|-----------|--------------|----------|----------|----------------|
| intro | +0% | +0% | -2dB | -2dB | -2dB |
| verse | +0% | +0% | +0dB (基准) | +0dB | +0dB |
| pre-chorus | +10% | -5ms | +1dB | +1dB | +1dB |
| chorus | +15% | -10ms | +3dB | +2dB | +3dB |
| bridge | +5% | +5ms | +1dB | +1dB | +1dB |
| outro | +20% | +10ms | -1dB | -1dB | -2dB |

> 来源：Unison Audio Reverb Automation 101、Splice Mix Automation Tips

---

## 十五、母带 Master Bus

```
Master Bus:
  1. bx_townhouse Buss Compressor  — SSL G 总线压缩（Ratio 2:1, Attack 10-30ms）
  2. bx_2098 EQ                   — BAX 母带塑形（宽频 Shelf + Glow/Sheen）
  3. The God Particle             — Jaycen Joshua 母带链（穿过去）
  4. Pro-L 2                      — True Peak 限幅 + LUFS 目标
```

| 步骤 | 作用 | 驱动 |
|------|------|------|
| bx_townhouse | 总线条压缩粘合 | crest→Attack, BPM→Release |
| bx_2098 EQ | BAX 母带气质塑形 | mud→Low Shelf, air+presence→High Shelf |
| The God Particle | 多段压缩+谐波+宽度 | 默认模式（Jaycen Joshua 预设） |
| Pro-L 2 | TP 限幅+目标响度 | 流派 LUFS 二分搜索 |

### bx_2098 EQ 母带用法

| 参数 | 设置 | 说明 |
|------|------|------|
| Low Shelf | 70Hz, ±1dB | mud 驱动——浑减瘦补 |
| High Shelf | 12kHz, 0~+1.5dB | air+presence 驱动 |
| Glow | ON | BAX 经典高频光泽 |
| Sheen | 流派 | pop/rock/elec ON; folk/ballad/民美 OFF |
| Notch | OFF | 母带不用外科切除 |

> bx_2098 是 Dangerous Music BAX EQ 的建模。来源：Integraudio 2025、Reddit r/mixingmastering、The Pro Audio Files

---

## 十六、伴奏处理原则

```
伴奏 Backing — 甲方认可版本，不过度处理：
  1. Pro-Q 3 — HPF 40Hz (固定)
  2. 仅当 mud>3 → -2dB @ 300Hz (为人声让中低频)
  3. bx_townhouse Bus Comp — Ratio 2:1, GR 1-2dB (轻压缩粘合)
  4. 流派 LU 衰减：folk -3~-6, pop -6~-9, elec -9~-15
```

> 不添加额外 EQ/饱和/压缩。伴奏已经混好，不是重混对象。

---

## 十七、大师 Reference

| 大师 | 链特征 | 核心理念 |
|------|--------|---------|
| Chris Lord-Alge | 1176→LA-2A→Pultec | "1176 推到前面，LA-2A 让它坐住" |
| Greg Wells | 1073→1176→CL 1B→Pultec | 终极人声链：双压缩+电子管EQ |
| Serban Ghenea | Pro-Q 3→Pro-DS→RVox→Inflator(?) | 极简但每个插到极致 |
| Jaycen Joshua | The God Particle 母带 | "穿过去就好" |
| Dave Pensado | 重度 parallel comp, 60-100Hz shelf | "压缩是让你听到音乐" |
| Andrew Scheps | 有时前面不压, Pultec boost+cut 魔法 | "压缩是音乐性的" |
| Michael Brauer | FET/Opto/VCA/Vari-Mu/Dry 五总线 | 不追求一个压缩器解决一切 |

---

## 十八、Hermes 注册策略

1. 同型号不同厂商 → **独立注册**（Waves CLA-76 ≠ UAD 1176 Rev A ≠ Arturia 1176）
2. 1176 类：标注 **CW = 快** 的参数方向
3. 录入厂商文档的 sweet spot range
4. Reverb = **Send**（非 Insert）
5. Delay = **Send**（非 Insert）⚠️ 2026-06-07 纠正
6. MicroShift = **Send**（非 Insert）⚠️ 2026-06-07 纠正
7. 混响按流派配对专属插件，ValhallaVintageVerb 作为全局回退
8. Decapitator 放在压缩**后**——clip gain 峰值已控制

---

## 十九、参考来源

- UA 1176LN Official Manual / UAD 1176 Collection Manual
- UAD Pultec Passive EQ Collection Manual
- Sonnox Oxford Inflator User Guide
- Soundtoys Decapitator / MicroShift / EchoBoy Manual
- Tube-Tech CL 1B Official: tube-tech.com/cl-1b-opto-compressor
- KMR Audio: CL 1B typical starting points
- Produce Mix Fix: "Plugins To Try — Oxford Inflator"
- Music Guy Mixing: "Decapitator Style Button", "Best Reverb Settings for Vocals"
- URM Academy: "Mixing Secrets Volume 1 — Vocals"
- SonicScoop: "3 Techniques — Decapitator with Mixer Qmillion"
- iZotope: "The Best Reverb Plugins for Vocals 2026"
- LiquidSonics: "The Big Six Reverb Types", "What Reverb Presets Do The Pros Use"
- Integraudio: "Top 7 EQ Plugins For Mastering 2025"
- Gearspace / Reddit r/audioengineering / r/mixingmastering
- Greg Wells (Puremix): 1176 Settings on Vocal
- WeTheSound: "Using the 1176 on Vocals"
- MusicRadar: "The Producer's Guide to the Pultec EQP-1"
- Mix:analog: "Pultec Tube Equalizer Tutorial"
