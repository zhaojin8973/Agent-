# Hermes 混音知识库

> 自动推导的参考依据。全插件库可用。
> 每条规则标注出处：大师实践 / 声学原理 / 厂商文档。

---

## 一、完整人声处理链

```
Insert: Saturation → Corrective EQ → FET Comp → De-Esser
      → Dynamic EQ/Resonance → Opto Comp → Tonal EQ
      → Doubler/Micro-Shift → Delay
Send:   Reverb (1-2 个全工程共享)
```

| # | 类型 | 连接 | 为什么在这里 | 代表 |
|---|------|------|------------|------|
| 1 | Saturation | Insert | 最前——谐波染色，后续都在染色后工作 | Decapitator, Saturn 2, Softube Tape |
| 2 | Corrective EQ | Insert | 切除问题频率，不让压缩器吃垃圾 | Pro-Q 3, MDWEQ6 |
| 3 | FET Comp | Insert | 削峰、抓瞬态、增能量 | 1176, CLA-76, Softube FET |
| 4 | De-Esser | Insert | 压缩会凸显齿音，压后除最有效 | Pro-DS, Eiosis e2deesser |
| 5 | Dynamic EQ / Resonance | Insert | 动态处理偶尔冒出的共振 | Soothe2, Pro-Q 3 (dynamic) |
| 6 | Opto Comp | Insert | 柔和胶水、复古谐波 | CL-1B, LA-2A |
| 7 | Tonal EQ | Insert | 音色雕琢：空气感、温暖、存在感 | Pultec EQP-1A, Maag EQ4 |
| 8 | Doubler / Micro-Shift | Insert | ±10 cents L/R 微妙宽度 | MicroShift, Waves Doubler |
| 9 | Delay | Insert | 节奏/密度效果（slap/ping-pong）| EchoBoy, H-Delay |
| — | Reverb | Send | 空间——保持人声清晰，不插入 | ValhallaVintageVerb, Lexicon 224 |

> **关于 Insert Reverb（音色混响）**：极短 room/ambience 偶尔可插在压缩后当密度工具。非标准做法，不放入默认链。

---

## 二、1176 类压缩器专节

### ⚠️ 反向旋钮逻辑

1176 的 Attack 和 Release **顺时针 = 更快**。跟绝大多数压缩器相反。

| 旋钮位置 | Attack | Release |
|---------|--------|---------|
| 7（CW 到底）| 最快（~20μs）| 最快（~50ms）|
| 1（CCW 到底）| 最慢（~800μs）| 最慢（~1.1s）|

> 来源：Universal Audio 1176LN 官方手册

### 厂商差异

硬件 1176 没有两台完全一样的。插件同理：

| 厂商 | Attack | Release | 谐波 | 适合 |
|------|--------|---------|------|------|
| UAD 1176 Rev A | CW=快 | 更智能的释放曲线 | 奇偶 3:2, 复古 | 贝斯、鼓、摇滚人声 |
| Waves CLA-76 | CW=快 | 稍拖尾、"呼吸感" | 偶次为主, 温暖 | 人声、钢琴、流行 |
| Arturia 1176 | CW=快 | 平衡 | 温和 | 通用 |
| Softube FET | CW=快 | 线性 | 干净 | 贝斯 DI、精确 |
| Black Lion Bluey | CW=慢！| CW=快 | CLA 改装机 | CLA 风格 |

> 同名不同厂，即使写相同 0-1 值，结果不同。每个厂应独立注册。

---

## 三、压缩器 Attack / Release 与 BPM

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

### Attack 推导

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

### BPM 预设

```python
FAST  = {"attack_ms": 3.0,  "release_ms": 60.0}   # BPM > 130
MED   = {"attack_ms": 5.0,  "release_ms": 100.0}  # BPM 90-130
SLOW  = {"attack_ms": 10.0, "release_ms": 200.0}  # BPM < 90
```

---

## 四、压缩器类型 Reference

| 类型 | 代表 | Attack | Release | 染色 | 链中角色 |
|------|------|--------|---------|------|---------|
| FET | 1176 | 极快(20μs-800μs) | 快(50ms-1.1s) | 中-强 | #3 削峰/能量 |
| Opto | LA-2A, CL-1B | 慢(~10ms) | 慢(60ms-5s) | 暖 | #6 胶水 |
| VCA | SSL G, Pro-C 2 | 可调 | 可调 | 干净 | 总线 |
| Vari-Mu | Fairchild, Manley | 慢(0.2-2ms) | 极慢(0.3-25s) | 强 | #6 厚声 |

---

## 五、EQ 频率 Reference

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

## 六、四套贴唱效果链

### 链 1: Modern Pop — 亮、靠前、干净

```
Insert: Pro-Q 3 → 1176 → Pro-DS → CL-1B → Maag EQ4 → MicroShift → Delay(slap)
Send:   Plate Reverb (~1.2s decay)
```

### 链 2: Rock / Indie — 边缘、能量、攻击性

```
Insert: Pro-Q 3 → 1176(激进) → Pro-DS → Decapitator → Pultec EQP-1A → LA-2A → Delay(slapback)
Send:   Hall Reverb (high pre-delay ~60ms)
```

### 链 3: R&B / Soul — 暖、圆润、亲密

```
Insert: Pro-Q 3 → CL-1B(先!) → Pro-DS → Soothe2 → 1176(后) → Maag EQ4 → MicroShift
Send:   Chamber Reverb (~1.5s decay, 30ms pre-delay)
```

### 链 4: Hip-Hop — 干、近、咄咄逼人

```
Insert: Pro-Q 3 → 1176(极限) → Pro-DS → Decapitator → SSL EQ → VCA Comp → Delay(ping-pong)
Send:   Plate/Chamber (量极低——只推气息)
```

---

## 七、大师 Reference

| 大师 | 链特征 | 核心理念 |
|------|--------|---------|
| Chris Lord-Alge | 1176→LA-2A→Pultec | "1176 推到前面，LA-2A 让它坐住" |
| Dave Pensado | 重度 parallel comp, 60-100Hz shelf | "压缩是让你听到音乐" |
| Andrew Scheps | 有时前面不压, Pultec boost+cut 魔法 | "压缩是音乐性的" |
| Michael Brauer | Brauerize: FET/Opto/VCA/Vari-Mu/Dry 五总线 | 不追求一个压缩器解决一切 |

---

## 八、Hermes 注册策略

1. 同型号不同厂商 → **独立注册**（Waves CLA-76 ≠ UAD 1176 ≠ Arturia 1176）
2. 1176 类：标注 **CW = 快** 的参数方向
3. 录入厂商文档的 sweet spot range
4. Reverb = Send，Delay = Insert

---

## 九、参考来源

- UA 1176LN Official Manual / Waves CLA-76 User Guide
- MusicRadar: "How to design a mixing chain" (2024)
- pureMix: CLA Mixing Lifeboats / Dave Pensado "Into The Lair"
- Andrew Scheps interviews / Michael Brauer "Brauerize" technique
- Gearspace / 《Mixing Secrets for the Small Studio》— Mike Senior
- 《The Mixing Engineer's Handbook》— Bobby Owsinski
