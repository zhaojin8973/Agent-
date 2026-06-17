# Hermes 完整混音链参数设计

> 版本 1.2 | 2026-06-08 | Vocal A（无 UAD）+ Vocal B（UAD）双链 | EQ232D 独立模块 + 流派差异化
> 所有参数由信号分析自动驱动

---

## 目录

- [〇、Vocal A / Vocal B 双链](#〇vocal-a--vocal-b-双链)
- [一、信号流总图](#一信号流总图)
- [二、人声 Insert 链](#二人声-insert-链)
- [三、空间效果器 AUX](#三空间效果器-aux)
- [四、伴奏 Backing 链](#四伴奏-backing-链)
- [五、母带 Master Bus](#五母带-master-bus)
- [六、驱动信号速查](#六驱动信号速查)
- [七、流派参数速查](#七流派参数速查)

---

## 〇、Vocal A / Vocal B 双链

| 位置 | Vocal B（UAD 链） | Vocal A（无 UAD 快速链） |
|------|------------------|------------------------|
| EQ | FabFilter Pro-Q 3 | ← 相同 |
| FET 压缩 | **UAD 1176 Rev A** | **Waves CLA-76** |
| 饱和 | Decapitator (Soundtoys) | ← 相同 |
| 齿音 | FabFilter Pro-DS | ← 相同 |
| 染色 EQ | **UAD Pultec EQP-1A** | **Bettermaker EQ232D (PA)** |
| 体压缩 | RVox Mono (Waves) | ← 相同 |
| 谐波 | Oxford Inflator (Sonnox) | ← 相同 |
| 光电压缩 | **UAD Tube-Tech CL 1B** | **Shadow Hills Mastering Compressor (PA)** |
| 空气 | Maag EQ4 (PA) | ← 相同 |
| 空间混响（Room） | UAD EMT 140 (pop/rock) | ValhallaPlate |
| 空间混响（其他） | 见 §三 | 见 §三（同 Vocal B） |

- **Vocal A** YAML: `profiles/vocal_a_{genre}.yaml`，加载 `for_genre(genre, variant="a")`
- **Vocal B** YAML: `profiles/vocal_{genre}.yaml`，加载 `for_genre(genre)` 或 `variant="b"`

---

## 一、信号流总图

```
                              ┌─────────────────────────────────────┐
                              │         人声 Insert 链 (9段)         │
                              │                                     │
                              │ Vocal B: Pro-Q3→1176→Decapitator    │
                              │   →Pro-DS→Pultec→RVox              │
                              │   →Inflator→CL1B→Maag              │
                              │                                     │
                              │ Vocal A: Pro-Q3→CLA-76→Decapitator  │
                              │   →Pro-DS→EQ232D→RVox              │
                              │   →Inflator→Shadow Hills→Maag          │
                              └──────────────┬──────────────────────┘
                                             │
              ┌──────────────────────────────┼──────────────────────────────┐
              │                              │                              │
     ┌────────▼────────┐          ┌─────────▼─────────┐          ┌─────────▼─────────┐
     │  MicroShift AUX  │          │   Reverb AUX ×3    │          │   Delay AUX ×3     │
     │  Mix=100%        │          │  流派专属混响配对    │          │  EchoBoy           │
     │  Detune=0.15     │          │  Room/Plate/Hall    │          │  Slap/Throw/PingPong│
     │  Focus=1-5kHz    │          │  +Blackhole(elec)   │          │                    │
     │    ↓             │          │    ↓                │          │    ↓               │
     │  Pro-Q3 MS HPF   │          │  混响内置 HPF/damp   │          │  EchoBoy 内置 HPF   │
     └────────┬─────────┘          └─────────┬───────────┘          └─────────┬───────────┘
              │                              │                              │
              └──────────────────────────────┼──────────────────────────────┘
                                             │
                                   延迟 → 混响 交叉发送 (-12dB)
                                             │
                              ┌──────────────▼──────────────────────┐
                              │          伴奏 Backing 链             │
                              │  Pro-Q3 (轻HPF) → bx_townhouse (轻)  │
                              └──────────────┬──────────────────────┘
                                             │
                              ┌──────────────▼──────────────────────┐
                              │          母带 Master Bus            │
                              │  bx_townhouse → bx_2098 EQ          │
                              │    → The God Particle → Pro-L 2     │
                              └─────────────────────────────────────┘
```

> **EQ 策略说明**：经多方查证（Production Expert、Sonible、iZotope、Bobby Owsinski、Gearspace、Reddit），
> 现代混响插件（Seventh Heaven、Cinematic Rooms、LX480 v4、Valhalla）自带优秀的 HPF/damping 控制，
> 不再外挂 Pro-Q3。仅 MicroShift 因 Mid-Side 路由需求保留 Pro-Q3 MS HPF @ 500Hz。
> Abbey Road Trick 的 LPF 不是铁律——人声暗时混响补亮度不应切高频。Blackhole/Supernova 等创意混响全频段通过。

---

## 二、人声 Insert 链

### Vocal B（UAD 链）完整插件路径

```
Pro-Q3 → UAD 1176 Rev A → Decapitator → Pro-DS
  → UAD Pultec EQP-1A → RVox → Oxford Inflator
  → UAD CL 1B → Maag EQ4
```

### Vocal A（无 UAD 快速链）完整插件路径

```
Pro-Q3 → CLA-76 (Waves) → Decapitator → Pro-DS
  → Bettermaker EQ232D (PA) → RVox → Oxford Inflator
  → Shadow Hills (PA) → Maag EQ4
```

---

### 2.1 Pro-Q 3 — HPF + 动态共振抑制

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **HPF Freq** | `sub_excess` + `role` | vocal: 80Hz基准, sub>3→max 120Hz; backing: 40Hz基准, sub>3→max 80Hz |
| **HPF Q** | 固定 | 0.71 (Butterworth 12dB/oct) |
| **Low Shelf Cut** | `mud_ratio_db` | mud>0: -mud×0.5 dB @ 250Hz (减浑); mud<0: 不处理 |
| **Presence Boost** | `presence_deficit_db` | deficit>2: +deficit×0.4 dB @ 3-5kHz (补亮) |
| **Air Shelf** | `air_level_db` + `spectral_tilt` | tilt<-3 且 air<-22: +1.0dB @ 8kHz; tilt<-4.5 且 air<-30: +1.5dB @ 8kHz |
| **动态共振抑制** | `resonances[Q>15]` | 每共振: cut_db=-min(prominence,6)dB, q=min(q_factor×0.5,10) |

Vocal A/B 无差异。

---

### 2.2 FET 峰值压缩

**Vocal B**: `VST3: 1176 Rev A Compressor (Universal Audio)`
**Vocal A**: `VST3: CLA-76 Mono (Waves)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Input** | `raw_rms_db` | RMS+18dB 归一化基准 (0 VU对齐) |
| **Output** | 自动补偿 | 补偿 GR 量，保持前后 RMS 一致 |
| **Attack** | `crest_factor_db` + 流派 | `_compute_cla76_attack_knob(crest, genre)` → knob 1-7 |
| **Release** | `BPM` | BPM有效: `60000/BPM` → `_ms_to_cla76_release()` 查表; 无BPM: knob 4 (默认) |
| **Ratio** | 流派 | folk 4:1 / ballad 4:1 / pop 8:1 / rock 8:1 / electronic 12:1 / 民美 4:1 |

**逻辑**：CLA-76 是 Waves 对 1176 Rev A 的建模，参数推导逻辑同 1176。crest 大 → attack 慢（保瞬态），crest 小 → attack 快（控制力强）。

---

### 2.3 Decapitator — 谐波饱和

**REAPER 插件名**: `VST3: Decapitator (Soundtoys)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Style** | 固定 | `E` (EMI — 最平滑人声模式) |
| **Drive** | `crest_factor_db` 反比 | crest>15: 1-2 / crest 10-15: 2-3 / crest<10: 3-4 (10档满量程) |
| **Tone** | 固定 | 0.5 (中位，微微削毛刺) |
| **Mix** | 流派 | folk 30% / ballad 35% / pop 40% / rock 40% / electronic 50% / 民美 35% |
| **High Cut** | 固定 | OFF |
| **Low Cut** | 固定 | OFF |

Vocal A/B 无差异。放在压缩**之后**，避免 clip gain 后的高峰值直接触发过载失真。

---

### 2.4 Pro-DS — 齿音消除

**REAPER 插件名**: `VST: FabFilter Pro-DS (FabFilter)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Threshold** | `presence_deficit_db` | -32 + deficit×0.1, clamp(-60, 0) |
| **Range** | 流派 | folk 6.0 / ballad 6.0 / pop 8.5 / rock 8.5 / electronic 10.0 / 民美 7.0 |
| **Detection HPF** | 固定 | 5.5kHz |
| **Detection LPF** | 固定 | 12kHz |
| **Mode** | 固定 | Single Vocal |

Vocal A/B 无差异。

---

### 2.5 染色 EQ

**Vocal B**: `VST3: EQP-1A Legacy (Universal Audio)` — UAD Pultec EQP-1A
**Vocal A**: `Bettermaker EQ232D (Plugin Alliance)` — Pultec 风格母带 EQ

> EQ232D 为独立模块 `eq232d.py`，遵循 Decapitator/Pro-DS 的单文件模式。
> 频谱分析沿用 mid-chain（Decapitator 后）数据，不额外渲染。

#### Pultec EQP-1A (Vocal B)

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Low Freq** | 固定 | 60 Hz (经典 Pultec Trick 频率) |
| **Low Boost** | 固定 | 3-4 (温暖厚底) |
| **Low Atten** | `mud_ratio_db` | mud>3: Atten 2-3 (经典推拉塑形); mud 0-3: Atten 1-2; mud<0: Atten 0 |
| **High Freq** | `presence_deficit_db` | deficit>0: 12kHz (补亮); deficit<=0: 8kHz (保自然) |
| **High Boost** | `presence_deficit_db` | deficit×0.4, clamp(0, 5) |
| **High BW** | 固定 | 5 (宽Q，自然过渡) |

参数为物理值（Hz/dB），需经 normalize.py 映射到 VST3 归一化值。

#### Bettermaker EQ232D (Vocal A)

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **LO CPS** | 固定 | 0.33（≈60Hz Pultec 经典值） |
| **LO BOOST** | `genre` | 流派查表: folk 0.30 → rap 0.38 → electronic 0.42 |
| **LO ATTEN** | `presence_deficit` × `genre` | deficit>3: base×1.2; deficit>0: base; else: base×0.4 |
| **HI BOOST** | `presence_deficit` × `genre` | deficit × K_by_genre, clamp(0, 1) |
| **HI ATTEN** | 预留 | 0（sibilance 数据待接入，Pro-DS 已处理齿音） |
| **HI BW** | `genre` | 流派查表: folk 0.55 → rap 0.38 → electronic 0.42 |
| **CHANNEL** | 固定 | 1.0（Dual Mono，人声单声道） |
| **Ch2 / MS / KCS / HPF / EQ1-2** | 固定 | 全部关闭（Pro-Q 3 已做手术 EQ + HPF） |

参数全部 0-1 归一化（VST3 原生），无 normalize 转换（pass-through clamp）。

**流派参数表**：

| 流派 | LO BOOST | LO ATTEN | HI BOOST K | HI BW | 设计意图 |
|------|----------|----------|------------|-------|---------|
| folk | 0.30 | 0.20 | 0.030 | 0.55 | 自然温暖，保守存在感 |
| ballad | 0.32 | 0.22 | 0.032 | 0.55 | 柔和温暖 |
| 民美 | 0.35 | 0.25 | 0.035 | 0.50 | 大气均衡 |
| pop | 0.38 | 0.28 | 0.040 | 0.48 | 商业质感 |
| rock | 0.40 | 0.30 | 0.045 | 0.45 | 穿透吉他墙 |
| **rap** | **0.38** | **0.36** | **0.060** | **0.38** | **咬字至上，低频紧致，强力打开暗声** |
| electronic | 0.42 | 0.32 | 0.050 | 0.42 | 穿透密集 synth/sub |

---

### 2.6 RVox — RMS 体压缩

**REAPER 插件名**: `VST3: RVox Mono (Waves)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Compression** | `gr_target_db` × 流派 multiplier | folk 1.0 / ballad 1.2 / pop 1.7 / rock 1.7 / electronic 1.8 / 民美 1.5 |
| **Gate** | 固定 | -40dB |
| **Gain** | 自动补偿 | 补偿 Compression 量的 50% |

Vocal A/B 无差异。

---

### 2.7 Oxford Inflator — 谐波密度增强

**REAPER 插件名**: `VST3: Oxford Inflator (Sonnox)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Effect** | 流派 | folk 20% / ballad 25% / pop 30% / rock 35% / electronic 40% / 民美 25% |
| **Curve** | 固定 | 负值 (0.0 — 最透明，人声推荐) |
| **Clip 0dB** | 固定 | OFF (不削波，仅谐波增强) |
| **Input** | 固定 | 0 dB |
| **Output** | 固定 | -0.5 dB |

Vocal A/B 无差异。

---

### 2.8 光电压缩 + Tube 塑形

**Vocal B**: `VST3: Tube-Tech CL 1B MkII (Universal Audio)` — 光电体压缩
**Vocal A**: `Shadow Hills Mastering Compressor (Plugin Alliance)` — 光学部分

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Ratio** | 流派 | folk 2:1 / ballad 2:1 / pop 3:1 / rock 3:1 / electronic 4:1 / 民美 2:1 |
| **Threshold** | post-RVox RMS | RMS + 4dB (目标 GR 2-4dB) |
| **Attack** | 固定 | 5ms (慢起振，保护自然起振) |
| **Release** | BPM | BPM有效: `60000/BPM × 0.25` (16分音符体感); 无BPM: 0.3s |
| **Gain** | 自动补偿 | 补偿 GR 量的 80% |
| **Sidechain HPF** | 固定 | 80Hz |

> Vocal A 上 Shadow Hills 用光学压缩部分（Optical Section），参数推导同 CL 1B，实际生效取决于插件参数名匹配。

---

### 2.9 Maag EQ4 — Air Band 抛光

**REAPER 插件名**: `VST3: Maag EQ4 (Plugin Alliance)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Air Freq** | 流派 | folk/ballad/民美 10kHz / pop/rock 20kHz / electronic 20kHz |
| **Air Boost** | `presence_deficit_db` + `air_level_db` | deficit×0.3 + \|air\|×0.2, clamp(0, 6) |
| **160Hz** | `mud_ratio_db` | mud<-3: Boost 1-2 (补瘦声); 否则 0 |
| **2.5kHz** | `spectral_tilt_db_per_octave` | tilt<-4: Boost 1-2 (补亮度); 否则 0 |

Vocal A/B 无差异。

---

## 三、空间效果器 AUX

### EQ 策略

- **Room / Plate / Hall**：使用混响内置 HPF/damping（Seventh Heaven→Low Cut, Cinematic Rooms→Crossover, LX480 v4→内置 EQ, Valhalla→Low Cut）。不设 LPF（保留亮度通道），必要时靠混响器自身 tone 控制
- **Blackhole / Supernova**：不加任何 EQ，全频段通过（创意效果设计的本意）
- **Delay (EchoBoy)**：使用 EchoBoy 内置 HPF + Saturation
- **MicroShift**：唯一需要外挂 EQ 的场景 → Pro-Q3 MS 模式，Side 通道 HPF @ 500Hz

---

### 3.1 MicroShift AUX（立体声展宽）

**REAPER 插件名**: `VST3: MicroShift (Soundtoys)`

```
人声 Post-Fader Send → MicroShift AUX:
  1. MicroShift (Mix 100%, Detune/ Delay 由 crest 驱动, Focus/Style 由流派选择)
  2. Pro-Q 3 (MS 模式, Side 通道 HPF — 低频保持中置)
```

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Mix** | 固定 | 100% (AUX 上始终全湿，发送量控制比例) |
| **Detune** | crest 反向 | 动态越大 detune 越保守 (0.25-0.70) |
| **Delay** | crest 反向 | 动态越大 delay 越短 (0.25-0.65) |
| **Focus** | 流派查表 | 民美/folk 850Hz → pop 750Hz → rock/electronic 650Hz |
| **Style** | 流派选择 | I(H3000)/II/III(AMS) |
| **Send Level** | 流派基准 + crest_bias + presence_bias + section_bias | `_compute_spatial_sends` |

**逻辑**：MicroShift 作为独立 AUX 与 Reverb/Delay 平行，不交叉发送。业界最佳实践：doubler/widener 应独立控制，不与空间效果串联。

---

### 3.2 混响 AUX（流派专属配对 + Valhalla 回退）

#### 3.2.1 插件配对

> 插件名 `LX480 v4` = Relab Development LX480 Dual-Engine Reverb V4（Lexicon 480L 建模）

| AUX | folk | ballad | pop | rock | electronic | 民美 |
|-----|------|--------|-----|------|-----------|------|
| **Room/Short** | Seventh Heaven | Seventh Heaven | UAD EMT 140 →Vocal A: ValhallaPlate | UAD EMT 140 →Vocal A: ValhallaPlate | LX480 v4 | Seventh Heaven |
| **Plate/Medium** | LX480 v4 | LX480 v4 | LX480 v4 | Tai Chi | Supernova | LX480 v4 |
| **Hall/Long** | Cinematic Rooms | Cinematic Rooms | Seventh Heaven | LX480 v4 | ValhallaVintageVerb | Cinematic Rooms |

> **电子流派额外混响**：Blackhole 作为第 4 条独立 AUX（§3.4），不计入 Hall

> **回退规则**: 流派首选插件不存在 → 列表下一候选 → 最终 Valhalla 通用回退

#### 3.2.2 自动化参数

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Decay** | BPM × 流派 | BPM<80→decay×1.3, BPM 80-120→decay×1.0, BPM>120→decay×0.7 |
| **PreDelay** | `crest_factor_db` | crest>15→40ms, crest 10-15→25ms, crest<10→15ms |
| **Mix** | 固定 | 100% (AUX 全湿) |
| **HPF (混响内置)** | `mud_ratio_db` | mud>0→HPF 升 250→400Hz; mud<0→HPF 200Hz。通过混响自带的 Low Cut / Crossover 控制 |
| **Send Level** | crest + presence + section + 流派 | `_compute_spatial_sends` |

#### 3.2.3 段落偏移

| 段落 | Decay | PreDelay | Send |
|------|-------|----------|------|
| intro | +0% | +0% | -2dB |
| verse | +0% | +0% | +0dB (基准) |
| pre-chorus | +10% | -5ms | +1dB |
| chorus | +15% | -10ms | +3dB |
| bridge | +5% | +5ms | +1dB |
| outro | +20% | +10ms | -1dB (渐远) |

---

### 3.3 延迟 AUX（EchoBoy ×3）

#### 3.3.1 基本设定

| AUX | 模式 | 音符值 | Feedback | Saturation | HPF（内置） |
|------|------|--------|----------|------------|-------------|
| **Slap** | Studio Tape | 1/16 | 10% | 0.10 | 400Hz |
| **Throw** | Memory Man | 1/8 dot | 20% | 0.15 | 300Hz |
| **PingPong** | EchoPlex | 1/4 | 15% | 0.10 | 250Hz |

#### 3.3.2 发送量

Delay 发送量从 Plate 混响推导，保证所有流派的 delay/reverb 比例一致：

| Delay 类型 | 推导公式 | 含义 |
|-----------|---------|------|
| **Slap** | `Plate dB - 7` | 加厚工具，应被感觉到而非听到 |
| **Throw** | `Plate dB - 7` | 回声效果，与 Slap 同级避免过于突出 |
| **PingPong** | `Plate dB - 10` | 立体声交替已自带宽度，极微量即可 |

加上信号偏差（crest/presence/section）和限幅 `[-24, -6] dB`。Folk 流派全部禁用。

> **设计理由**：旧方案使用独立的 `_GENRE_DELAY_SEND_BASE` 表手动维护，
> 导致 pop Slap 仅比 Plate 低 2dB（应低 7dB）。改为相对推导后比例一致。

#### 3.3.3 自动化参数

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Time** | BPM × 音符值 | `note_to_ms(note_value, BPM)` |
| **Feedback** | `crest_factor_db` | crest>15: FB-5%; crest<10: FB+5% |
| **HPF** | EchoBoy 内置 | mud>3: HPF+50Hz |

---

### 3.4 Blackhole AUX（Electronic 专属第 4 条混响）

**REAPER 插件名**: `VST3: Blackhole (Eventide)`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Mix** | 固定 | 100% |
| **Gravity** | section | verse=Normal, chorus=Reverse, bridge=Normal |
| **Size** | BPM | BPM<100→80, BPM 100-140→60, BPM>140→40 |
| **Decay** | 固定 | 8s |
| **Send Level** | 固定 | -12dB (verse 基准) |

> **仅在 `genre=electronic` 时创建。** 不经过 Pro-Q3 外部 EQ，全频段通过。Blackhole 不作为 electronic 的 Hall 使用（Hall 用 ValhallaVintageVerb）。

---

### 3.5 混响↔延迟交叉发送

```
Slap AUX  → Send (-12dB) → Plate Reverb AUX（加厚+板式质感）
Throw AUX → Send (-12dB) → Hall Reverb AUX（回声+大厅空间）
PingPong AUX → 无交叉发送（立体声交替不需要）
```

**默认行为**，所有流派开启（folk 除外，delay 总线不创建）。

---

### 3.6 MicroShift → 空间总线

```
MicroShift AUX — 独立 AUX，不与 Reverb/Delay 交叉发送
```

> **设计理由**：业界最佳实践是独立发送（方案 A），doubler/widener 不与空间效果串联。
> 交叉发送（方案 B）会导致 MicroShift 展宽信号和混响/延迟尾音绑在一起，失去独立控制。

---

## 四、伴奏 Backing 链

```
Backing Bus (Post-Fader):
  1. Pro-Q 3 — HPF 40Hz (固定), mud>3→-2dB @ 300Hz
  2. bx_townhouse Bus Comp — Ratio 2:1, Attack 10ms, Release auto, GR 1-2dB
```

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **HPF** | 固定 | 40Hz |
| **Mud Cut** | `mud_ratio_db` | mud>3: -2dB @ 300Hz |
| **Bus Comp GR** | 流派 | folk -3~-6LU, pop -6~-9LU, rock -6~-10LU, elec -9~-15LU |

Vocal A/B 无差异。

---

## 五、母带 Master Bus

```
Master Bus:
  1. bx_townhouse Buss Compressor — SSL 风格总线压缩
  2. bx_2098 EQ — BAX 宽频母带塑形
  3. The God Particle — 母带谐波增强
  4. Pro-L 2 — True Peak 限幅 + LUFS 目标
```

Vocal A/B 无差异，详见 v1.0 文档。

### 目标 LUFS

| 流派 | 目标 LUFS |
|------|----------|
| folk | -13 LUFS |
| ballad | -13 LUFS |
| pop | -10 LUFS |
| rock | -10 LUFS |
| electronic | -9 LUFS |
| 民美 | -11 LUFS |

---

## 六、驱动信号速查

| 信号名 | 含义 | 来源 |
|--------|------|------|
| `raw_rms_db` | 原始 RMS 电平 | `SignalAnalyzer.analyze()` |
| `crest_factor_db` | 波峰因子 | raw_peak - raw_rms |
| `sub_excess` | 次低频过量 | band_energy |
| `mud_ratio_db` | 浑浊度 | `SpectrumReport` |
| `presence_deficit_db` | 存在感缺失 | `SpectrumReport` |
| `air_level_db` | 空气感电平 | `SpectrumReport` |
| `sibilance_peak_db` | 齿音峰值 | `SpectrumReport` |
| `spectral_tilt_db_per_octave` | 频谱倾斜 | `SpectrumReport` |
| `gr_target_db` | 目标压缩量 | `_derive_compressor_intent()` |
| `BPM` | 工程速度 | 用户输入或 MIDI 检测 |

---

## 七、流派参数速查

| 参数 | folk | ballad | pop | rock | electronic | 民美 |
|------|------|--------|-----|------|-----------|------|
| 目标 LUFS | -13 | -13 | -10 | -10 | -9 | -11 |
| 1176/CLA76 Ratio | 4:1 | 4:1 | 8:1 | 8:1 | 12:1 | 4:1 |
| Decap Mix | 30% | 35% | 40% | 40% | 50% | 35% |
| Pro-DS Range | 6.0 | 6.0 | 8.5 | 8.5 | 10.0 | 7.0 |
| RVox Mult | 1.0 | 1.2 | 1.7 | 1.7 | 1.8 | 1.5 |
| Inflator Effect | 20% | 25% | 30% | 35% | 40% | 25% |
| CL 1B/Shadow Hills Ratio | 2:1 | 2:1 | 3:1 | 3:1 | 4:1 | 2:1 |
| Maag Air Freq | 10k | 10k | 20k | 20k | 20k | 10k |
| Vocal B Room | SH | SH | EMT 140 | EMT 140 | LX480 v4 | SH |
| Vocal B Plate | LX480 v4 | LX480 v4 | LX480 v4 | Tai Chi | Supernova | LX480 v4 |
| Vocal B Hall | CR | CR | SH | LX480 v4 | ValhallaVerb | CR |
| Vocal A Room | SH | SH | ValhallaPlate | ValhallaPlate | LX480 v4 | SH |
| Vocal A Plate | LX480 v4 | LX480 v4 | LX480 v4 | Tai Chi | Supernova | LX480 v4 |
| Vocal A Hall | CR | CR | SH | LX480 v4 | ValhallaVerb | CR |
| Slap | -∞ | -21 | -15 | -15 | -11 | -15 |
| Throw | -∞ | -24 | -18 | -18 | -14 | -18 |
| PingPong | -∞ | -∞ | -20 | -18 | -16 | -20 |
| Blackhole (第4AUX) | - | - | - | - | -12 | - |
| Pro-L 2 Style | Transparent | Transparent | Allround | Allround | Aggressive | Transparent |
