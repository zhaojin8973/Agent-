# Hermes 完整混音链参数设计

> 版本 1.2 | 2026-06-08 | Decapitator + Pro-DS 独立模块，mid-chain 频谱重分析
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
                              │   →Inflator→Shadow Hills→Maag好的       │
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
                                   延迟 → 混响 交叉发送 (-8dB)
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

#### 角色

FET 压缩器只抓**峰值**。身体压缩由后续 RVox + Shadow Hills 承担。

#### 参数总表

```
Input   = -40.4 + GR×0.8 - peak        clamped [-48, 0]
Output  = -GR × 3.25                    clamped [-48, 0]
Attack  = base - (crest-10)×k           clamped [1, 6.5]
Release = 60000/BPM × genre_factor → ms→knob表
          无BPM → 默认 knob 4.0
```

| 参数 | 驱动 | 依赖风格 | 公式 |
|------|------|---------|------|
| Input | GR目标 + peak | 否 | `-40.4 + GR×0.8 - peak` |
| Output | GR目标 | 否 | `-GR × 3.25` |
| Attack | crest + **流派** | **是** | `base - (crest-10)×k` |
| Release | BPM + **流派** | **是** | `60000/BPM × factor` |

#### Input

1176 固定阈值。Input 控制信号进入 FET 级之前的电平。校准数据（粉噪 -18dBFS RMS）：

```
Input(dB)   GR(dB)
──────────────────
 -32          0      刚触阈值
 -24          3      适度
 -20          8      重度
 -16         15      极重
```

公式中 `-40.4` 是 GR=0 的理论零点基线，`0.8` 是 GR 3→8 段的线性回归斜率。Input 值在 -28~-38 之间是正常的——1176 FET 级有 ~40dB 内部增益，信号经衰减→放大→压缩后净效果为 2-3dB GR。

#### Output

```
output_db = -GR × 3.25
```

系数 3.25 是经验电平匹配值（望归 vocal 校准，2026-05-31）。1176 的 Input 驱动 + FET 内部增益会抬高整体电平，Output 需要相应衰减来保持 unity。

#### Attack

CLA-76 旋钮 1-7（CW=快，与多数压缩器方向相反）：

| Knob | 等效时间 |
|------|---------|
| 7 | ~20 μs |
| 5 | ~800 μs |
| 3 | ~5 ms |
| 1 | ~8 ms |

流派参数：

| 流派 | Base | k | 理由 |
|------|------|---|------|
| folk | 3.0 | 0.05 | 最慢，保呼吸感 |
| ballad | 3.0 | 0.05 | 同上 |
| chinese_folk_bel_canto | 3.5 | 0.08 | 偏慢，大气线条 |
| pop | 4.0 | 0.10 | 标准 |
| rock | 4.0 | 0.10 | 标准 |
| electronic | 5.0 | 0.05 | 快，紧实控制 |

高波峰 → 慢 attack（保瞬态）；低波峰 → 快 attack（收紧）。

#### Release

ms → knob 表（线性插值）：

```
  ms    knob
─────────────
  50     7.0
 150     6.0
 300     5.0
 500     4.0
 700     3.0
 900     2.0
1100     1.0
```

流派系数（乘在 60000/BPM 上）：

| 流派 | factor | BPM=72时(ms→knob) |
|------|--------|-------------------|
| electronic | 0.50 | 417→4.5 |
| pop | 0.65 | 542→3.8 |
| rock | 0.65 | 542→3.8 |
| chinese_folk_bel_canto | 0.75 | 625→3.4 |
| folk | 0.80 | 667→3.1 |
| ballad | 0.85 | 708→2.7 |

#### GR 目标

```
gr_target = crest × ratio
```

| 流派 | Ratio |
|------|-------|
| folk | 0.12 |
| ballad | 0.12 |
| chinese_folk_bel_canto | 0.14 |
| pop | 0.17 |
| rock | 0.17 |
| electronic | 0.22 |

#### 代码位置

| 关注点 | 文件 |
|--------|------|
| 参数范围 | `normalize.py` |
| 流派表（base/k/factor/ratio/GR表/ms↔knob） | `genre_tables.py` |
| Input/Output/Attack 公式 | `comp_engine.py` |
| Release 调度、类型分发 | `fx_builder.py` |
| 类型别名 "CLA-76"→"fet" | `profiles.py` |

---

### 2.3 Decapitator — 谐波饱和

**REAPER 插件名**: `VST3: Decapitator (Soundtoys)`
**模块**: `src/hermes_core/decapitator.py`

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Style** | **流派** | A(Ampex)=folk/ballad, E(EMI)=pop/CFBC, N(Neve)=rock, T(Triode)=electronic |
| **Drive** | `crest_db` 反比 | `BASE[genre] - (crest-10)×K`, clamp(0.05, 0.30) |
| **Tone** | `presence_deficit` + 流派 | `BASE[genre] + deficit×0.015`, clamp(0.42, 0.58) |
| **Mix** | 流派 | folk 30% / ballad 35% / pop 40% / rock 40% / electronic 50% / 民美 35% |
| **OutputTrim** | `Drive` | `1.0 - Drive×0.25`, clamp(0.825, 1.0) — unity gain 补偿 |
| **Punish/LowCut/HighCut/Thump** | 固定 | 全部 OFF |

**Style 流派差异化**（文献验证：Music Guy Mixing, Gearspace, Soundtoys 官方手册）：
AMPEX(0.0)=温暖磁带 → folk/ballad, EMI(0.25)=平滑控台 → pop/CFBC,
Neve(0.5)=质感中频 → rock, Triode(0.75)=管味偶次谐波 → electronic

**Tone 是 PRE-saturation tilt EQ**（官方手册确认），影响哪些频率被失真：
偏暗(<0.5)→低频先失真、温暖; 偏亮(>0.5)→高频先失真、清晰颗粒

Vocal A/B 无差异。放在压缩**之后**，避免 clip gain 后的高峰值直接触发过载失真。

---

### 2.4 Pro-DS — 齿音消除

**REAPER 插件名**: `VST: FabFilter Pro-DS (FabFilter)`
**模块**: `src/hermes_core/pro_ds.py`

#### Mid-chain 频谱重分析

Pro-DS 不再使用 Q3 前的干声频谱。engine.py 在 Decapitator 之后自动渲染人声轨道，
重跑 `SpectrumAnalyzer`，更新 `_last_spectrum`。Pro-DS 拿到的是经过
**Q3 + CLA-76 + Decapitator** 处理后的真实频谱数据。

#### 参数推导

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Threshold** | `band_rms` + `crest` | `Threshold = band_rms + crest×0.4`, clamp(-40, -10)<br>band_rms = mid-chain 重分析的 presence 频段(5-8k) A-weighted 能量<br>来源: FabFilter 手册 + SPL Auto Threshold 设计 |
| **Range** | 流派 | folk 6.0 / ballad 6.0 / pop 8.5 / rock 8.5 / electronic **9.0** / 民美 7.0 |
| **Detection HPF** | `sib_peak` + **性别** | `clamp(sib_peak-1500, 5000, 6500)` 女 / `clamp(sib_peak-1500, 4000, 5500)` 男 / 默认 4500-5500 |
| **Detection LPF** | `sib_peak` + **性别** | `clamp(sib_peak+3000, 10000, 12500)` 女 / `clamp(sib_peak+2500, 8500, 10500)` 男 / 默认 9500-12500 |
| **Mode** | 固定 | Wide Band（FabFilter 推荐单声道人声首选） |
| **Lookahead** | 固定 | 10 ms |

#### 设计原则

1. **Threshold = band_rms + crest_margin**: band_rms 确保高于常态能量（不误触发），
   crest-based margin 留空间给齿音峰。clamp(-40, -10) 防止暗声过度保守或亮声过度激进。
   文献验证: FabFilter Pro-DS 官方手册("Threshold sets the threshold of the side-chain level"),
   Sound On Sound, AudioSpectra

2. **HPF/LPF 仅为安全窗口**: 行业共识（FabFilter/Waves/iZotope/Sonible）——
   de-esser 靠内部智能算法（Pro-DS "Single Vocal" mode）区分齿音和乐音，
   HPF/LPF 只是限定搜索范围。sib_peak 不作为齿音锚点，仅微调窗口位置。
   整体上移 500 Hz 保护共振峰区（女声 5k+/男声 4k+）。

3. **性别感知**: 女声齿音 5-9k (Produce Like A Pro: 7-8k; Audio Issues: 5-9k)，
   男声齿音 3-7k (Produce Like A Pro: 5-6k; Audio Issues: 3-7k)

Vocal A/B 无差异。

---

### 2.5 染色 EQ

**Vocal B**: `VST3: EQP-1A Legacy (Universal Audio)` — UAD Pultec EQP-1A
**Vocal A**: `Bettermaker EQ232D (Plugin Alliance)` — Pultec 风格母带 EQ

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Low Freq** | 固定 | 60 Hz (经典 Pultec Trick 频率) |
| **Low Boost** | 固定 | 3-4 (温暖厚底) |
| **Low Atten** | `mud_ratio_db` | mud>3: Atten 2-3 (经典推拉塑形); mud 0-3: Atten 1-2; mud<0: Atten 0 |
| **High Freq** | `presence_deficit_db` | deficit>0: 12kHz (补亮); deficit<=0: 8kHz (保自然) |
| **High Boost** | `presence_deficit_db` | deficit×0.4, clamp(0, 5) |
| **High BW** | 固定 | 5 (宽Q，自然过渡) |

> Vocal A 上 EQ232D 的参数推导与 Pultec 相同，实际生效取决于插件参数名匹配。

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
  1. MicroShift (Mix 100%, Detune 0.15, Delay 0.08, Focus 1-5kHz)
  2. Pro-Q 3 (MS 模式, Side 通道 HPF 500Hz — 低频保持中置)
  3. Send → 混响 Bus（和主唱共享同一个混响 AUX）
  4. Send → 延迟 Bus（和主唱共享同一个延迟 AUX）
```

| 参数 | 驱动信号 | 推导逻辑 |
|------|---------|---------|
| **Mix** | 固定 | 100% (AUX 上始终全湿，发送量控制比例) |
| **Detune** | 固定 | 0.15 (±9 音分微妙偏移) |
| **Delay** | 固定 | 0.08 (中短延迟) |
| **Focus** | 固定 | 1-5kHz (中高频聚焦，低音不展宽) |
| **Send Level** | 流派 × section | `_compute_spatial_sends` → `microshift` key; verse 基准, chorus +3dB |

**逻辑**：MicroShift 是 AUX 发送而非 Insert。MS EQ 切 Side 低频是 URM Academy 推荐的标准做法。MicroShift AUX 的光泽通过共享混响/延迟来和主唱融为一体。

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

| 流派 | Slap | Throw | PingPong |
|------|------|-------|-----------|
| folk | -∞ | -∞ | -∞ |
| ballad | -21 | -24 | -∞ |
| pop | -15 | -18 | -20 |
| rock | -15 | -18 | -18 |
| electronic | -11 | -14 | -16 |
| 民美 | -15 | -18 | -20 |

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
每条 Delay AUX → Send (-8dB) → 每条 Reverb AUX
```

**默认行为**，所有流派开启。

---

### 3.6 MicroShift → 空间总线交叉发送

```
MicroShift AUX → Send (ms_send - 6dB) → 每条 Reverb AUX
MicroShift AUX → Send (ms_send - 6dB) → 每条 Delay AUX
```

让展宽后的信号和主唱共享同一空间效果器。

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
| Pro-DS Range | 6.0 | 6.0 | 8.5 | 8.5 | **9.0** | 7.0 |
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
