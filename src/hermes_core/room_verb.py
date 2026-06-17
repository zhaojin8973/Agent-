"""
Room Reverb (ValhallaRoom) — BPM 驱动的 Tight Room 混响参数推导。

三路混响架构：
  Room  — Tight (ValhallaRoom), 1/256 PDL, 目标 0.5-1.0s
  Plate — Small Room (LX480 v4), 1/128 PDL, 目标 1.0-2.0s
  Hall  — Large Room (Seventh Heaven / Cinematic Rooms), 1/64 PDL, 目标 2.0-4.0s

ValhallaRoom 专为 Tight Room/Early Reflections 设计：
  - Early/Late Size 分离控制
  - Early/Late Mix 精确平衡
  - Diffusion 独立控制
  - 线性 RTM/PDL/LoCut 曲线，校准简洁

时值公式：
  rtm_s  = 拍数 × 60 / bpm
  pdl_ms = 60,000 / bpm / 音符细分
  无BPM → 默认120

校准：2026-06-12 REAPER TrackFX_GetFormattedParamValue 自动扫描
      VST: ValhallaRoom (Valhalla DSP, LLC) — 26 params
      Decay:  线性 0.10–100s
      PreDelay: 线性 0–500ms
      LoCut:  线性 0–1000Hz
"""

import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# RTM — Decay: 线性 0.10s–100s
# 校准: 2026-06-12 REAPER TrackFX_GetFormattedParamValue
# ════════════════════════════════════════════════════════════════


def _rtm_s_to_norm(s: float) -> float:
    """RTM 秒 → norm。ValhallaRoom decay 近似线性：norm ≈ s / 100。"""
    return round(max(0.0, min(1.0, s / 100.0)), 4)


# ════════════════════════════════════════════════════════════════
# PDL — PreDelay: 线性 0–500ms
# 校准: 2026-06-12 REAPER TrackFX_GetFormattedParamValue
# ════════════════════════════════════════════════════════════════


def _pdl_ms_to_norm(ms: float) -> float:
    """PDL ms → norm。ValhallaRoom predelay 线性：norm = ms / 500。"""
    return round(max(0.0, min(1.0, ms / 500.0)), 4)


# ════════════════════════════════════════════════════════════════
# RTM — BPM 锚点平滑插值
# 思路：慢歌空间大、快歌空间紧，锚点之间线性过渡
# ════════════════════════════════════════════════════════════════

_RTM_ANCHORS: list[tuple[float, float]] = [
    (40,  1.2),   # 极慢速
    (80,  1.0),   # 慢速
    (120, 0.7),   # 中速
    (160, 0.5),   # 快速
    (200, 0.4),   # 极快速
]


from hermes_core.genre_tables import _bpm_to_rtm_s  # replaces local def


_DEFAULT_BPM = 120.0

# 流派 RTM 倍率：在 BPM 锚点曲线基础上按风格微调
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

_PDL_SUB = 64                  # 1/256 note
_PDL_NOTE = "1/256"

# ════════════════════════════════════════════════════════════════
# 非时值参数 — Tight Room 声学特性
# 全部基于 2026-06-12 REAPER TrackFX_GetFormattedParamValue 校准
# ════════════════════════════════════════════════════════════════

# ── HiCut (HFC) — 高切频率 ──
# 校准: norm=0.50→7550Hz, 0.57→8593Hz, 0.67→~10kHz, 0.80→12020Hz
_GENRE_HICUT: dict[str, float] = {
    "folk": 0.5600,                    # ~8.4kHz — 木质感温暖
    "ballad": 0.5300,                  # ~8.0kHz — 最暗最亲密
    "chinese_folk_bel_canto": 0.6300,  # ~9.5kHz — 明亮但不刺
    "pop": 0.6000,                     # ~9.0kHz
    "rock": 0.6300,                    # ~9.5kHz
    "rap": 0.6700,                     # ~10.0kHz — 保留清晰度
    "electronic": 0.8000,             # ~12.0kHz — 最亮
}

# ── LoCut (LFC) — 低切频率 ──
# 校准: 线性 norm×1000=Hz, norm=0.12→120Hz, 0.20→200Hz, 0.25→250Hz
_GENRE_LOCUT: dict[str, float] = {
    "folk": 0.1200,                    # 120Hz
    "ballad": 0.0800,                  # 80Hz — 保留低频温暖
    "chinese_folk_bel_canto": 0.1200,  # 120Hz
    "pop": 0.1500,                     # 150Hz
    "rock": 0.1800,                    # 180Hz
    "rap": 0.2000,                     # 200Hz
    "electronic": 0.2500,             # 250Hz — 切除最多低频
}

# ── earlyLateMix — 早/晚期混响平衡 (DIF) ──
# 0=全早期反射, 1=全晚期混响。Tight Room 偏早期
_GENRE_EARLY_LATE: dict[str, float] = {
    "folk": 0.2800,                    # 72% 早期 — 木质感
    "ballad": 0.3800,                  # 62% 早期 — 略丰满
    "chinese_folk_bel_canto": 0.3500,  # 65% 早期
    "pop": 0.3200,                     # 68% 早期
    "rock": 0.2000,                    # 80% 早期 — 紧实
    "rap": 0.2000,                     # 80% 早期 — 最紧
    "electronic": 0.5000,             # 50% — 均衡
}

# ── lateSize — 晚期混响空间大小 (SIZ) ──
_GENRE_LATE_SIZE: dict[str, float] = {
    "folk": 0.3000, "ballad": 0.4500, "chinese_folk_bel_canto": 0.4000,
    "pop": 0.3500, "rock": 0.2500, "rap": 0.2200, "electronic": 0.5500,
}

# ── earlySize — 早期反射空间大小 ──
# 校准: 近似线性 norm×1000≈ms。官方推荐 10–30ms 为人声 Room 标准区间
_GENRE_EARLY_SIZE: dict[str, float] = {
    "folk": 0.0150,                    # 15ms
    "ballad": 0.0250,                  # 25ms — 略宽但不散
    "chinese_folk_bel_canto": 0.0200,  # 20ms
    "pop": 0.0200,                     # 20ms
    "rock": 0.0120,                    # 12ms — 最紧
    "rap": 0.0120,                     # 12ms — 最紧
    "electronic": 0.0300,             # 30ms
}

# ── diffusion — 扩散量 ──
# 官方：Diffusion=1.0 最适合人声，高扩散不会产生金属感
_GENRE_DIFFUSION: dict[str, float] = {
    "folk": 0.9000, "ballad": 0.9500, "chinese_folk_bel_canto": 0.9500,
    "pop": 0.9000, "rock": 0.8500, "rap": 0.8500, "electronic": 1.0000,
}

# ── RTBassMultiply — 低频衰减倍率 (BAS) ──
# 官方：<0.5X 最干净。ValhallaRoom 最低 0.50X (norm=0.00)
_GENRE_BASS_MUL: dict[str, float] = {
    "folk": 0.0200,                    # ~0.53X
    "ballad": 0.0400,                  # ~0.56X
    "chinese_folk_bel_canto": 0.0400,  # ~0.56X
    "pop": 0.0200,                     # ~0.53X
    "rock": 0.0000,                    # 0.50X — 最紧
    "rap": 0.0000,                     # 0.50X — 最紧
    "electronic": 0.0800,             # ~0.62X
}

# ── RTHighMultiply — 高频衰减倍率 ──
# 校准: norm=0.18→0.25X, 0.25→0.32X, 0.30→0.37X, 0.50→0.55X
# Tight Room: 高频衰减快 → 更暗更亲密
_GENRE_HIGH_MUL: dict[str, float] = {
    "folk": 0.2000,                    # 0.28X — 高频衰减快
    "ballad": 0.2500,                  # 0.32X
    "chinese_folk_bel_canto": 0.2200,  # 0.30X
    "pop": 0.2000,                     # 0.28X
    "rock": 0.1800,                    # 0.25X — 最暗
    "rap": 0.1800,                     # 0.25X — 最暗
    "electronic": 0.3000,             # 0.37X — 最亮
}

# ── 默认回退值 ──
_DEFAULTS: dict[str, float] = {
    "HICUT": 0.6000, "LOCUT": 0.1500, "EARLY_LATE": 0.3200,
    "LATE_SIZE": 0.3500, "EARLY_SIZE": 0.0200, "DIFFUSION": 0.9000,
    "BASS_MUL": 0.0200, "HIGH_MUL": 0.2000,
    "TYPE": 0.0000,
}

# ── 房间类型 (type 参数) ──
# 来源: Valhalla DSP 官方文档 + 2026-06-12 REAPER 扫描
# 0.00=Large Room(自然/宽广) 0.20=Medium Room(Lexicon风格)
# 0.25=Bright Room(光泽/华丽) 0.40=Large Chamber(透明/无色)
# 0.50=Dark Chamber(大/暗) 1.00=Dense Room(密集早期回声)
_GENRE_TYPE: dict[str, float] = {
    "folk": 0.5000,                    # Dark Chamber — 温暖自然
    "ballad": 0.0000,                  # Large Room — 最自然平滑
    "chinese_folk_bel_canto": 0.0000,  # Large Room — 宽广空间感
    "pop": 0.4000,                     # Large Chamber — 透明无色
    "rock": 1.0000,                    # Dense Room — 密集冲击
    "rap": 1.0000,                     # Dense Room — 最紧
    "electronic": 0.2500,             # Bright Room — 光泽华丽
}

# ── 固定参数 ──
_MIX_WET = 1.0             # AUX 发送式，插件内 100% wet
_MOD_RATE_OFF = 0.0        # Tight Room 不需要调制
_MOD_DEPTH_OFF = 0.0       # Tight Room 不需要调制
_BYPASS = 0.0              # 不旁通


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """安全钳位：确保所有值在 [0.0, 1.0] 范围内。"""
    return {k: max(0.0, min(1.0, v)) for k, v in physical.items()}


def build_params(ctx, *, bpm: float | None = None) -> dict:
    """根据流派和 BPM 构建 Tight Room 混响参数 (ValhallaRoom)。

    Parameters
    ----------
    ctx : object
        需提供 ``ctx.genre`` 属性（str）。
    bpm : float | None
        歌曲速度。为 None 时使用默认 120 BPM。

    Returns
    -------
    dict
        13 个 ValhallaRoom 参数的 {name: norm} 映射。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"
    eff_bpm = bpm if (bpm and bpm > 0) else _DEFAULT_BPM

    # ── RTM: BPM 锚点插值 × 流派倍率 → 秒 → norm ──
    base_s = _bpm_to_rtm_s(eff_bpm, _RTM_ANCHORS)
    mult = _GENRE_RTM_MULT.get(genre, _DEFAULT_RTM_MULT)
    rtm_s = round(base_s * mult, 2)
    rtm = _rtm_s_to_norm(rtm_s)

    # ── PDL: 1/256 音符 ──
    pdl_ms = round(60_000.0 / eff_bpm / _PDL_SUB, 1)
    pdl = _pdl_ms_to_norm(pdl_ms)

    # ── 非时值参数 ──
    hic = _GENRE_HICUT.get(genre, _DEFAULTS["HICUT"])
    loc = _GENRE_LOCUT.get(genre, _DEFAULTS["LOCUT"])
    elm = _GENRE_EARLY_LATE.get(genre, _DEFAULTS["EARLY_LATE"])
    lsz = _GENRE_LATE_SIZE.get(genre, _DEFAULTS["LATE_SIZE"])
    esz = _GENRE_EARLY_SIZE.get(genre, _DEFAULTS["EARLY_SIZE"])
    dif = _GENRE_DIFFUSION.get(genre, _DEFAULTS["DIFFUSION"])
    bml = _GENRE_BASS_MUL.get(genre, _DEFAULTS["BASS_MUL"])
    hml = _GENRE_HIGH_MUL.get(genre, _DEFAULTS["HIGH_MUL"])
    typ = _GENRE_TYPE.get(genre, _DEFAULTS["TYPE"])

    log.info(
        f"Auto-Room: RTM=%.4f(%.1fs) PDL=%.4f(%.0fms/{_PDL_NOTE}) "
        f"HiCut=%.4f LoCut=%.4f EarlyLate=%.4f "
        f"LateSize=%.4f EarlySize=%.4f Diff=%.4f BassMul=%.4f HiMul=%.4f "
        f"Type=%.4f (genre=%s bpm=%s)",
        rtm, rtm_s, pdl, pdl_ms,
        hic, loc, elm, lsz, esz, dif, bml, hml, typ, genre,
        f"{eff_bpm:.0f}" if bpm else "N/A",
    )

    return {
        # ── 时值 ──
        "decay":        rtm,
        "predelay":     pdl,
        # ── 频率 ──
        "HiCut":        hic,
        "LoCut":        loc,
        # ── 空间 ──
        "earlyLateMix": elm,
        "lateSize":     lsz,
        "earlySize":    esz,
        "diffusion":    dif,
        "type":         typ,
        # ── 衰减 ──
        "RTBassMultiply":  bml,
        "RTXover":         0.0300,    # ~400Hz，低频衰减分频点
        "RTHighMultiply":  hml,
        "RTHighXover":     0.2600,    # ~4000Hz，高频衰减分频点
        # ── 早期发送 ──
        "earlySend":        0.5000,    # 早期反射 → 晚期混响，增加密度
        # ── 调制（关闭）──
        "lateModRate":      _MOD_RATE_OFF,
        "lateModDepth":     _MOD_DEPTH_OFF,
        "earlyModRate":     _MOD_RATE_OFF,
        "earlyModDepth":    _MOD_DEPTH_OFF,
        # ── 固定 ──
        "mix":              _MIX_WET,
        "Bypass":           _BYPASS,
    }
