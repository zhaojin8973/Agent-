"""
Hall Reverb (Seventh Heaven / ValhallaVintageVerb) — BPM 驱动的厅堂混响参数推导。

三路混响架构：
  Room  — Tight (ValhallaRoom), 1/256 PDL, 目标 0.5-1.0s
  Plate — Small Room (LX480 v4), 1/128 PDL, 目标 1.0-2.0s
  Hall  — Large Room (Seventh Heaven / ValhallaVintageVerb), 1/64 PDL, 目标 2.0-3.5s

双插件策略（按流派）：
  Seventh Heaven      — folk, ballad, chinese_folk_bel_canto（自然温暖）
  ValhallaVintageVerb — pop, rock, rap, electronic（现代有态度）

Seventh Heaven 工作流：选预设 → 覆盖关键参数（Decay, Pre-delay, VLF, Ducker 等）

时值公式：
  rtm_s  ≈ BPM 锚点插值 × 流派倍率
  pdl_ms = 60,000 / bpm / 16（1/64 音符）
  无BPM → 默认120

Decay Time 校准：2026-06-12 REAPER TrackFX_GetFormattedParamValue 扫描
  VST3: Seventh Heaven Professional (LiquidSonics)
  范围 0.20–30.00s，非线性曲线
"""

import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# RTM — BPM 锚点平滑插值
# ════════════════════════════════════════════════════════════════

_RTM_ANCHORS: list[tuple[float, float]] = [
    (40,  3.5),   # 极慢速 → 大厅
    (80,  2.8),   # 慢速 → 自然衰减
    (120, 2.0),   # 中速 → 标准大厅
    (160, 1.5),   # 快速 → 紧凑
    (200, 1.2),   # 极快速 → 最短
]


from hermes_core.genre_tables import _bpm_to_rtm_s  # replaces local def


# ════════════════════════════════════════════════════════════════
# Decay Time 校准 — 秒 → norm（REAPER 扫描）
# ════════════════════════════════════════════════════════════════

_SH_DECAY_CAL: list[tuple[float, float]] = [
    (0.20, 0.00), (0.35, 0.02), (0.55, 0.05), (0.75, 0.08),
    (0.90, 0.10), (1.00, 0.12), (1.20, 0.15), (1.55, 0.20),
    (1.90, 0.25), (2.25, 0.30), (2.90, 0.40), (4.20, 0.50),
    (5.60, 0.60), (7.80, 0.70), (11.50, 0.80), (18.00, 0.90),
    (30.00, 1.00),
]


def _sh_rtm_s_to_norm(s: float) -> float:
    """Seventh Heaven Decay Time 秒 → norm（分段线性插值）。"""
    if s <= _SH_DECAY_CAL[0][0]:
        return _SH_DECAY_CAL[0][1]
    for i in range(len(_SH_DECAY_CAL) - 1):
        s0, n0 = _SH_DECAY_CAL[i]
        s1, n1 = _SH_DECAY_CAL[i + 1]
        if s0 <= s <= s1:
            return round(n0 + (s - s0) / (s1 - s0) * (n1 - n0), 4)
    return _SH_DECAY_CAL[-1][1]


# ════════════════════════════════════════════════════════════════
# PDL / HPF 校准 — REAPER 扫描
# ════════════════════════════════════════════════════════════════

# Seventh Heaven Pre-delay: 0-500ms, 非线性
_SH_PDL_CAL: list[tuple[float, float]] = [
    (0, 0.00), (1, 0.02), (2, 0.04), (5, 0.06),
    (7, 0.08), (11, 0.10), (15, 0.12), (21, 0.15),
    (34, 0.20), (67, 0.30), (157, 0.50), (345, 0.80),
    (500, 1.00),
]


def _sh_pdl_ms_to_norm(ms: float) -> float:
    """Seventh Heaven Pre-delay ms → norm。"""
    if ms <= _SH_PDL_CAL[0][0]:
        return _SH_PDL_CAL[0][1]
    for i in range(len(_SH_PDL_CAL) - 1):
        m0, n0 = _SH_PDL_CAL[i]
        m1, n1 = _SH_PDL_CAL[i + 1]
        if m0 <= ms <= m1:
            return round(n0 + (ms - m0) / (m1 - m0) * (n1 - n0), 4)
    return _SH_PDL_CAL[-1][1]


# Seventh Heaven High Pass Freq: 20-12000Hz, 非线性
_SH_HPF_CAL: list[tuple[float, float]] = [
    (20, 0.00), (21, 0.05), (26, 0.10), (41, 0.15),
    (76, 0.20), (138, 0.25), (237, 0.30), (585, 0.40),
    (1209, 0.50), (3669, 0.70), (12000, 1.00),
]


def _sh_hz_to_hpf_norm(hz: float) -> float:
    """频率 Hz → High Pass Freq norm。"""
    if hz <= _SH_HPF_CAL[0][0]:
        return _SH_HPF_CAL[0][1]
    for i in range(len(_SH_HPF_CAL) - 1):
        h0, n0 = _SH_HPF_CAL[i]
        h1, n1 = _SH_HPF_CAL[i + 1]
        if h0 <= hz <= h1:
            return round(n0 + (hz - h0) / (h1 - h0) * (n1 - n0), 4)
    return _SH_HPF_CAL[-1][1]


# ════════════════════════════════════════════════════════════════
# 流派时值定义
# ════════════════════════════════════════════════════════════════

_DEFAULT_BPM = 120.0

_GENRE_RTM_MULT: dict[str, float] = {
    "folk":                    0.85,
    "ballad":                  1.00,
    "chinese_folk_bel_canto":  1.00,
    "pop":                     0.90,
    "rock":                    0.80,
    "rap":                     0.70,
    "electronic":              0.95,
}
_DEFAULT_RTM_MULT = 1.0

_GENRE_HALL_PLUGIN: dict[str, str] = {
    "folk":                    "seventh_heaven",
    "ballad":                  "seventh_heaven",
    "chinese_folk_bel_canto":  "seventh_heaven",
    "pop":                     "vintage_verb",
    "rock":                    "vintage_verb",
    "rap":                     "vintage_verb",
    "electronic":              "vintage_verb",
}

PDL_SUB = 16
PDL_NOTE = "1/64"

# ════════════════════════════════════════════════════════════════
# Seventh Heaven 预设选择
# Content Bank/Preset 值由 REAPER 扫描确定
# ════════════════════════════════════════════════════════════════

# Bank norm 映射（中点位，bank_id/500）
_SH_BANK_AMBIENCE_1  = 0.010   # Ambience 1
_SH_BANK_CHAMBERS_1  = 0.020   # Chambers 1 — 人声亲密空间
_SH_BANK_HALLS_1     = 0.030   # Halls 1 — 大厅 v1（稳定立体声像）
_SH_BANK_HALLS_2     = 0.040   # Halls 2 — 大厅 v2（调制尾音）
_SH_BANK_INTERIORS_1 = 0.050   # Interiors 1
_SH_BANK_NONLINEAR   = 0.060   # Nonlinear
_SH_BANK_PLATES_1    = 0.070   # Plates 1
_SH_BANK_PLATES_2    = 0.080   # Plates 2
_SH_BANK_ROOMS_1     = 0.090   # Rooms 1
_SH_BANK_ROOMS_2     = 0.100   # Rooms 2
_SH_BANK_SPACES_1    = 0.110   # Spaces 1
_SH_BANK_SPACES_2    = 0.117   # Spaces 2

# 流派 → (Bank, Preset norm)
# Preset 索引从 0 开始，每个位置步长 ≈ 0.025 (1/40)
_SH_PRESET_STEP = 0.010  # 预设间步长（0.01 per index）

# 预设索引 × 步长 = norm 值
# #11 = A&M Chamber, #17 = Sunset Chamber（from REAPER readback: 0.170 = #17）
_SH_GENRE_PRESET: dict[str, tuple[float, float]] = {
    "folk":                    (_SH_BANK_CHAMBERS_1, 0.110),  # #11 A&M Chamber
    "ballad":                  (_SH_BANK_CHAMBERS_1, 0.170),  # #17 Sunset Chamber
    "chinese_folk_bel_canto":  (_SH_BANK_CHAMBERS_1, 0.170),  # #17 Sunset Chamber
}

# ════════════════════════════════════════════════════════════════
# Seventh Heaven 流派参数覆盖（在预设基础上微调）
# ════════════════════════════════════════════════════════════════

# Early / Late Level: 0=全早期, 1=全晚期
_SH_GENRE_EARLY_LATE: dict[str, float] = {
    "folk": 0.40, "ballad": 0.60, "chinese_folk_bel_canto": 0.55,
}

# VLF Reverb Level: 参照 Sunset Chamber (-20dB), 适当保持一些体感
_SH_GENRE_VLF: dict[str, float] = {
    "folk": 0.10,                    # -18dB (Sunset -20dB + 我们的设计)
    "ballad": 0.12,                  # -17.5dB
    "chinese_folk_bel_canto": 0.12,
}

# Reflection Pattern: 参照 Sunset Chamber (#7), 向近场靠拢
_SH_GENRE_REFLECTION: dict[str, float] = {
    "folk": 0.25,                    # #8 — 近场+，A&M Chamber 自然距离
    "ballad": 0.35,                  # #11 — 中近，Sunset #7 → 我们的 #17 → 取中
    "chinese_folk_bel_canto": 0.35,
}

# Early / Late Level: 参照 Sunset Chamber (-2.0dB/0dB ≈ 0.55), 已接近
# 不变: folk 0.40, ballad 0.60, 民美 0.55

# Early Rolloff: 参照 Sunset Chamber (6kHz), 从 10k 向预设靠拢
_SH_GENRE_EARLY_ROLLOFF: dict[str, float] = {
    "folk": 0.46,                    # ~7kHz
    "ballad": 0.48,                  # ~7.5kHz (Sunset 6k → 我们 10k → 取中)
    "chinese_folk_bel_canto": 0.48,
}

# Late Rolloff: 参照 Sunset Chamber (7.2kHz), 保持略暗
_SH_GENRE_LATE_ROLLOFF: dict[str, float] = {
    "folk": 0.46,                    # Sunset 7.2k, 保持自然
    "ballad": 0.46,
    "chinese_folk_bel_canto": 0.46,
}

# High Pass Freq: 切除混响低频（避免浑浊），单位 Hz
_SH_GENRE_HIPASS_HZ: dict[str, float] = {
    "folk": 300, "ballad": 250, "chinese_folk_bel_canto": 280,
}

# Low Pass Freq: 混响高频（控制亮度），0-1 norm
_SH_GENRE_LOPASS: dict[str, float] = {
    "folk": 0.60, "ballad": 0.55, "chinese_folk_bel_canto": 0.60,
}

_SH_DEFAULTS = {"EARLY_LATE": 0.55, "VLF": 0.10, "REFLECTION": 0.35,
                "HIPASS_HZ": 280, "LOPASS": 0.60, "EROLL": 0.48, "LROLL": 0.46}

# ════════════════════════════════════════════════════════════════
# ValhallaVintageVerb 参数（不变）
# ════════════════════════════════════════════════════════════════

def _vv_rtm_s_to_norm(s: float) -> float:
    """ValhallaVintageVerb Decay — 范围 0.05-70s，近似线性。"""
    return round(max(0.0, min(1.0, s / 50.0)), 4)


def _vv_pdl_ms_to_norm(ms: float) -> float:
    return round(max(0.0, min(1.0, ms / 200.0)), 4)


_GENRE_VV_SIZE: dict[str, float] = {
    "pop": 0.65, "rock": 0.55, "rap": 0.45, "electronic": 0.80,
}
_GENRE_VV_BASS: dict[str, float] = {
    "pop": 0.30, "rock": 0.25, "rap": 0.20, "electronic": 0.35,
}
_GENRE_VV_HICUT: dict[str, float] = {
    "pop": 0.70, "rock": 0.65, "rap": 0.75, "electronic": 0.85,
}
_GENRE_VV_LOCUT: dict[str, float] = {
    "pop": 0.20, "rock": 0.25, "rap": 0.30, "electronic": 0.35,
}
_GENRE_VV_EARLY_DIFF: dict[str, float] = {
    "pop": 0.60, "rock": 0.50, "rap": 0.45, "electronic": 0.70,
}
_GENRE_VV_LATE_DIFF: dict[str, float] = {
    "pop": 0.70, "rock": 0.60, "rap": 0.55, "electronic": 0.80,
}
_GENRE_VV_MODE: dict[str, float] = {
    "pop": 0.75, "rock": 0.50, "rap": 0.50, "electronic": 0.75,
}
_GENRE_VV_ATTACK: dict[str, float] = {
    "pop": 0.30, "rock": 0.25, "rap": 0.20, "electronic": 0.35,
}

_VV_DEFAULTS = {"SIZE": 0.65, "BASS": 0.30, "HICUT": 0.70,
                "LOCUT": 0.25, "EARLY_DIFF": 0.60, "LATE_DIFF": 0.70,
                "MODE": 0.75, "ATTACK": 0.30}

_MIX_WET = 1.0
_BYPASS = 0.0


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """安全钳位：确保所有值在 [0.0, 1.0] 范围内。"""
    return {k: max(0.0, min(1.0, v)) for k, v in physical.items()}


def build_params(ctx, *, bpm: float | None = None) -> dict:
    """根据流派和 BPM 构建厅堂混响参数。

    Seventh Heaven 流派：选预设 → 覆盖 Decay/Pre-delay/VLF/Ducker
    ValhallaVintageVerb 流派：全参数手动构建
    """
    genre = getattr(ctx, "genre", "pop") or "pop"
    eff_bpm = bpm if (bpm and bpm > 0) else _DEFAULT_BPM
    plugin = _GENRE_HALL_PLUGIN.get(genre, "vintage_verb")

    if plugin == "seventh_heaven":
        return _build_seventh_heaven(genre, eff_bpm, bpm)
    else:
        return _build_vintage_verb(genre, eff_bpm, bpm)


# ════════════════════════════════════════════════════════════════
# Seventh Heaven: 预设先行 + 关键参数覆盖
# ════════════════════════════════════════════════════════════════

def _build_seventh_heaven(
    genre: str, eff_bpm: float, bpm: float | None,
) -> dict:
    """Seventh Heaven Professional 参数构建。

    工作流：
      1. 选 Bricasti M7 预设（Content Bank + Preset）
      2. 覆盖 Decay Time（BPM 锚点 × 流派倍率）
      3. 覆盖 Pre-delay（1/64 音符）
      4. 流派微调（Early/Late, VLF, Reflection, HP/LP）
      5. 启用 Ducker（Late-only 模式，保持人声清晰）
    """
    # ── 1. 预设选择 ──
    bank, preset = _SH_GENRE_PRESET.get(
        genre, (_SH_BANK_HALLS_2, 0.10),
    )

    # ── 2. RTM: BPM 锚点 × 流派倍率 → 秒 → norm ──
    base_s = _bpm_to_rtm_s(eff_bpm, _RTM_ANCHORS)
    mult = _GENRE_RTM_MULT.get(genre, _DEFAULT_RTM_MULT)
    rtm_s = round(base_s * mult, 2)
    rtm = _sh_rtm_s_to_norm(rtm_s)

    # ── 3. PDL: 1/64 音符 ──
    pdl_ms = round(60_000.0 / eff_bpm / PDL_SUB, 1)
    pdl = _sh_pdl_ms_to_norm(pdl_ms)

    # ── 4. 音色微调（参照预设但保留设计意图）──
    elm = _SH_GENRE_EARLY_LATE.get(genre, _SH_DEFAULTS["EARLY_LATE"])
    vlf = _SH_GENRE_VLF.get(genre, _SH_DEFAULTS["VLF"])
    ref = _SH_GENRE_REFLECTION.get(genre, _SH_DEFAULTS["REFLECTION"])
    hpf_hz = _SH_GENRE_HIPASS_HZ.get(genre, _SH_DEFAULTS["HIPASS_HZ"])
    hpf = _sh_hz_to_hpf_norm(hpf_hz)
    lpf = _SH_GENRE_LOPASS.get(genre, _SH_DEFAULTS["LOPASS"])
    ero = _SH_GENRE_EARLY_ROLLOFF.get(genre, 0.48)
    lro = _SH_GENRE_LATE_ROLLOFF.get(genre, 0.46)

    log.info(
        "Auto-Hall(SH): preset=bank%.4f/p%.4f RTM=%.4f(%.1fs) PDL=%.4f(%.0fms/%s) "
        "EarlyLate=%.2f VLF=%.2f Refl=%.2f Eroll=%.2f Lroll=%.2f HP=%dHz LP=%.2f "
        "(genre=%s bpm=%s)",
        bank, preset, rtm, rtm_s, pdl, pdl_ms, PDL_NOTE,
        elm, vlf, ref, ero, lro, hpf_hz, lpf, genre,
        f"{eff_bpm:.0f}" if bpm else "N/A",
    )

    return {
        # ── 预设选择 ──
        "Content Bank":             bank,
        "Content Preset":           preset,
        # ── BPM 驱动时值 ──
        "Decay Time":               rtm,
        "Pre-delay":                pdl,
        # ── 音色（参照 Sunset/A&M 预设校准）──
        "Early / Late Level":       elm,
        "Reflection Pattern":       ref,
        "VLF Reverb Level":         vlf,
        "Early Rolloff":            ero,
        "Late Rolloff":             lro,
        # ── 频率整形 ──
        "Low Pass Freq":            lpf,
        "High Pass Freq":           hpf,
        "High Pass Enable":         1.0,
        # ── 衰减倍率（中性，由预设决定）──
        "Low Decay Multiplier":     0.50,   # ~1.0X
        "High Decay Multiplier":    0.50,
        # ── Ducker（Late-only）──
        "Ducker Enable":            1.0,
        "Ducker Mode":              1.0,
        # ── 关闭 tempo sync ──
        "Pre-delay Sync":           0.0,
        # ── 固定 ──
        "Dry/Wet Mix":              _MIX_WET,
        "Master Gain":              0.50,
        "Bypass":                   _BYPASS,
        "Wet":                      1.0,
    }


# ════════════════════════════════════════════════════════════════
# ValhallaVintageVerb: 全参数手动构建
# ════════════════════════════════════════════════════════════════

def _build_vintage_verb(
    genre: str, eff_bpm: float, bpm: float | None,
) -> dict:
    base_s = _bpm_to_rtm_s(eff_bpm, _RTM_ANCHORS)
    mult = _GENRE_RTM_MULT.get(genre, _DEFAULT_RTM_MULT)
    rtm_s = round(base_s * mult, 2)
    rtm = _vv_rtm_s_to_norm(rtm_s)
    pdl_ms = round(60_000.0 / eff_bpm / PDL_SUB, 1)
    pdl = _vv_pdl_ms_to_norm(pdl_ms)

    siz = _GENRE_VV_SIZE.get(genre, _VV_DEFAULTS["SIZE"])
    bas = _GENRE_VV_BASS.get(genre, _VV_DEFAULTS["BASS"])
    hic = _GENRE_VV_HICUT.get(genre, _VV_DEFAULTS["HICUT"])
    loc = _GENRE_VV_LOCUT.get(genre, _VV_DEFAULTS["LOCUT"])
    edf = _GENRE_VV_EARLY_DIFF.get(genre, _VV_DEFAULTS["EARLY_DIFF"])
    ldf = _GENRE_VV_LATE_DIFF.get(genre, _VV_DEFAULTS["LATE_DIFF"])
    mod = _GENRE_VV_MODE.get(genre, _VV_DEFAULTS["MODE"])
    atk = _GENRE_VV_ATTACK.get(genre, _VV_DEFAULTS["ATTACK"])

    log.info(
        "Auto-Hall(VV): RTM=%.4f(%.1fs) PDL=%.4f(%.0fms/%s) "
        "Size=%.2f Mode=%.2f Bass=%.2f HiCut=%.2f LoCut=%.2f "
        "EarlyDiff=%.2f LateDiff=%.2f (genre=%s bpm=%s)",
        rtm, rtm_s, pdl, pdl_ms, PDL_NOTE,
        siz, mod, bas, hic, loc, edf, ldf, genre,
        f"{eff_bpm:.0f}" if bpm else "N/A",
    )

    return {
        "Decay":                    rtm,
        "PreDelay":                 pdl,
        "Size":                     siz,
        "ReverbMode":               mod,
        "Attack":                   atk,
        "BassMult":                 bas,
        "BassXover":                0.30,
        "HighShelf":                0.50,
        "HighFreq":                 0.50,
        "HighCut":                  hic,
        "LowCut":                   loc,
        "EarlyDiffusion":           edf,
        "LateDiffusion":            ldf,
        "ModRate":                  0.10,
        "ModDepth":                 0.10,
        "ColorMode":                0.50,
        "Mix":                      _MIX_WET,
    }
