"""
Delay (ValhallaDelay / EchoBoy) — BPM 驱动的三路延迟参数推导。

三路延迟架构：
  Slap    — 固定短延迟（~80ms），增加人声厚度，不与 BPM 同步
  Throw   — 1/8 音符 BPM 同步，句尾特定词"抛"出，无 BPM 时旁通
  PingPong — 附点 1/8 音符 BPM 同步，立体声交替，无 BPM 时旁通

插件策略（ValhallaDelay 优先，EchoBoy 回退）：
  ValhallaDelay  — 首选，Valhalla DSP（41 参数全可写，API 兼容性好）
  EchoBoy        — 回退，SoundToys（部分参数只读，API 兼容性差）

参数名策略：
  始终输出 ValhallaDelay 参数名（首选插件），由 _apply_spatial_params
  通过 _SPATIAL_PARAM_FALLBACK_MAP 映射到回退插件。

校准：2026-06-13 REAPER TrackFX_GetFormattedParamValue 全参数扫描
  - DelayL_Ms: 0-500ms 线性(norm=ms/1000), >500ms 非线, 1.0=20000ms
  - LowCut:    线性 10-2000Hz
  - HighCut:   线性 200-20000Hz
  - DriveIn:   线性 0-24dB
  - Feedback:  线性 0-200%
  - Mode:      17 个离散值 (Tape…Analog)
  - DelayStyle: Single/Dual/Ratio/PingPong/Quad
  - Era:        Past/Present/Future
  - DelayLSync: Msec/Note/Dotted/Triplet
"""

import logging

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# Slap — 固定时间
# ════════════════════════════════════════════════════════════════

_SLAP_FIXED_MS = 80.0

# ════════════════════════════════════════════════════════════════
# DelayL_Ms 校准 — ms → norm（>500ms 非线性，REAPER 扫描）
# ════════════════════════════════════════════════════════════════

_DELAYL_MS_CAL: list[tuple[float, float]] = [
    (0,      0.00),
    (10,     0.01),
    (100,    0.10),
    (250,    0.25),
    (500,    0.50),
    (619,    0.562),
    (776,    0.644),   # 插值: 0.562+(776-619)/(981-619)*0.188 ≈ 0.6435
    (981,    0.75),
    (20000,  1.00),
]


def _ms_to_delayl_norm(ms: float) -> float:
    """ms → ValhallaDelay ``DelayL_Ms`` norm（分段线性插值）。"""
    if ms <= _DELAYL_MS_CAL[0][0]:
        return _DELAYL_MS_CAL[0][1]
    for i in range(len(_DELAYL_MS_CAL) - 1):
        m0, n0 = _DELAYL_MS_CAL[i]
        m1, n1 = _DELAYL_MS_CAL[i + 1]
        if m0 <= ms <= m1:
            t = (ms - m0) / (m1 - m0) if m1 != m0 else 0.0
            return round(n0 + t * (n1 - n0), 4)
    return _DELAYL_MS_CAL[-1][1]


# ════════════════════════════════════════════════════════════════
# 流派倍率（仅 Slap 使用）
# ════════════════════════════════════════════════════════════════

_DEFAULT_BPM = 120.0

_GENRE_DELAY_MULT: dict[str, float] = {
    "folk":                    0.85,
    "ballad":                  1.00,
    "chinese_folk_bel_canto":  1.00,
    "pop":                     0.90,
    "rock":                    0.80,
    "rap":                     0.70,
    "electronic":              0.95,
}
_DEFAULT_MULT = 1.0

# ════════════════════════════════════════════════════════════════
# Feedback — 0-200% 线性
# ════════════════════════════════════════════════════════════════

def _pct_to_feedback_norm(pct: float) -> float:
    """百分比 → ValhallaDelay Feedback norm（0-200%）。"""
    return round(max(0.0, min(1.0, pct / 200.0)), 4)


# Slap: 单次回声
_SLAP_FEEDBACK_PCT: dict[str, float] = {
    "folk": 0, "ballad": 0, "pop": 0, "rock": 0,
    "rap": 0, "electronic": 0, "chinese_folk_bel_canto": 0,
}

# Throw: 1-2 次重复
_THROW_FEEDBACK_PCT: dict[str, float] = {
    "folk": 15, "ballad": 15, "pop": 20, "rock": 20,
    "rap": 22, "electronic": 25, "chinese_folk_bel_canto": 15,
}

# PingPong: 2-4 次重复
_PINGPONG_FEEDBACK_PCT: dict[str, float] = {
    "folk": 25, "ballad": 25, "pop": 30, "rock": 32,
    "rap": 35, "electronic": 40, "chinese_folk_bel_canto": 25,
}

_FEEDBACK_TABLES: dict[str, dict[str, float]] = {
    "slap": _SLAP_FEEDBACK_PCT, "throw": _THROW_FEEDBACK_PCT,
    "pingpong": _PINGPONG_FEEDBACK_PCT,
}

# ════════════════════════════════════════════════════════════════
# DriveIn (dB) — 0-24dB 线性，替代 EchoBoy 的 Saturation
# ════════════════════════════════════════════════════════════════

def _db_to_drivein_norm(db: float) -> float:
    return round(max(0.0, min(1.0, db / 24.0)), 4)


_SLAP_DRIVE_DB: dict[str, float] = {
    "folk": 2.4, "ballad": 2.9, "pop": 3.6, "rock": 4.8,
    "rap": 2.9, "electronic": 1.9, "chinese_folk_bel_canto": 2.4,
}

_THROW_DRIVE_DB: dict[str, float] = {
    "folk": 1.9, "ballad": 2.4, "pop": 2.9, "rock": 3.6,
    "rap": 2.4, "electronic": 1.4, "chinese_folk_bel_canto": 1.9,
}

_PINGPONG_DRIVE_DB: dict[str, float] = {
    "folk": 1.4, "ballad": 1.9, "pop": 2.4, "rock": 2.9,
    "rap": 1.9, "electronic": 1.2, "chinese_folk_bel_canto": 1.4,
}

_DRIVE_TABLES: dict[str, dict[str, float]] = {
    "slap": _SLAP_DRIVE_DB, "throw": _THROW_DRIVE_DB,
    "pingpong": _PINGPONG_DRIVE_DB,
}

# ════════════════════════════════════════════════════════════════
# HPF (Hz) — LowCut: 线性 10-2000Hz
# ════════════════════════════════════════════════════════════════

def _hz_to_lowcut_norm(hz: float) -> float:
    """Hz → ValhallaDelay LowCut norm（线性 10-2000Hz）。"""
    return round(max(0.0, min(1.0, (hz - 10.0) / 1990.0)), 4)


_SLAP_HPF_HZ: dict[str, float] = {
    "folk": 140, "ballad": 160, "chinese_folk_bel_canto": 180,
    "pop": 220, "rock": 250, "rap": 260, "electronic": 300,
}

_THROW_HPF_HZ: dict[str, float] = {
    "folk": 220, "ballad": 250, "chinese_folk_bel_canto": 280,
    "pop": 320, "rock": 350, "rap": 360, "electronic": 400,
}

_PINGPONG_HPF_HZ: dict[str, float] = {
    "folk": 180, "ballad": 200, "chinese_folk_bel_canto": 230,
    "pop": 270, "rock": 300, "rap": 310, "electronic": 350,
}

_HPF_HZ_TABLES: dict[str, dict[str, float]] = {
    "slap": _SLAP_HPF_HZ, "throw": _THROW_HPF_HZ,
    "pingpong": _PINGPONG_HPF_HZ,
}

# ════════════════════════════════════════════════════════════════
# LPF (Hz) — HighCut: 线性 200-20000Hz
# ════════════════════════════════════════════════════════════════

def _hz_to_highcut_norm(hz: float) -> float:
    """Hz → ValhallaDelay HighCut norm（线性 200-20000Hz）。"""
    return round(max(0.0, min(1.0, (hz - 200.0) / 19800.0)), 4)


_SLAP_LPF_HZ: dict[str, float] = {
    "folk": 3500, "ballad": 3800, "chinese_folk_bel_canto": 4000,
    "pop": 4200, "rock": 4500, "rap": 4600, "electronic": 5000,
}

_THROW_LPF_HZ: dict[str, float] = {
    "folk": 4500, "ballad": 4800, "chinese_folk_bel_canto": 5000,
    "pop": 5200, "rock": 5500, "rap": 5600, "electronic": 6000,
}

_PINGPONG_LPF_HZ: dict[str, float] = {
    "folk": 3000, "ballad": 3300, "chinese_folk_bel_canto": 3500,
    "pop": 3700, "rock": 4000, "rap": 4100, "electronic": 4500,
}

_LPF_HZ_TABLES: dict[str, dict[str, float]] = {
    "slap": _SLAP_LPF_HZ, "throw": _THROW_LPF_HZ,
    "pingpong": _PINGPONG_LPF_HZ,
}

# ════════════════════════════════════════════════════════════════
# ValhallaDelay 固定/枚举值
# ════════════════════════════════════════════════════════════════

_MIX_WET = 1.0
_WIDTH = 1.0               # 100% 宽度
_AGE = 0.0                  # 无老化伪影
_DIFFUSION = 0.0            # 关闭扩散（延迟保持干净）
# 设计理由：Slap→Plate / Throw→Hall 已有交叉发送提供混响感，
# 加 Diffusion 会造成二次混响化。PingPong 刻意不送混响，
# 加 Diffusion 自相矛盾。Diffusion=0 时 DiffSize 被旁通不生效。
_DIFFSIZE = 1.0             # 无影响（Diffusion=OFF 时旁通，占位值）
_DUCKING = 0.0              # 关闭
_MODRATE = 0.0              # 关闭调制
_MODDEPTH = 0.0             # 关闭调制

# ValhallaDelay Mode 枚举（REAPER 密集扫描确定）
_MODE_TAPE        = 0.04
_MODE_HIFI        = 0.11
_MODE_BBD         = 0.15
_MODE_DIGITAL     = 0.17
_MODE_CHROMETAPE  = 0.69
_MODE_ANALOG      = 0.73

# Era 枚举
_ERA_PAST    = 0.0
_ERA_PRESENT = 0.7
_ERA_FUTURE  = 1.0

# ── 流派 × 延迟类型 → Mode ──
_GENRE_MODE: dict[str, dict[str, float]] = {
    "chinese_folk_bel_canto": {"slap": _MODE_CHROMETAPE, "throw": _MODE_DIGITAL,    "pingpong": _MODE_CHROMETAPE},
    "ballad":                 {"slap": _MODE_TAPE,       "throw": _MODE_TAPE,        "pingpong": _MODE_ANALOG},
    "folk":                   {"slap": _MODE_TAPE,       "throw": _MODE_DIGITAL,     "pingpong": _MODE_ANALOG},
    "pop":                    {"slap": _MODE_CHROMETAPE, "throw": _MODE_DIGITAL,     "pingpong": _MODE_HIFI},
    "rock":                   {"slap": _MODE_BBD,        "throw": _MODE_BBD,         "pingpong": _MODE_ANALOG},
    "rap":                    {"slap": _MODE_DIGITAL,    "throw": _MODE_BBD,         "pingpong": _MODE_ANALOG},
    "electronic":             {"slap": _MODE_HIFI,       "throw": _MODE_DIGITAL,     "pingpong": _MODE_HIFI},
}

# ── 流派 × 延迟类型 → Era ──
_GENRE_ERA: dict[str, dict[str, float]] = {
    "chinese_folk_bel_canto": {"slap": _ERA_PRESENT, "throw": _ERA_FUTURE,  "pingpong": _ERA_PRESENT},
    "ballad":                 {"slap": _ERA_PAST,    "throw": _ERA_PAST,     "pingpong": _ERA_PRESENT},
    "folk":                   {"slap": _ERA_PAST,    "throw": _ERA_FUTURE,   "pingpong": _ERA_PRESENT},
    "pop":                    {"slap": _ERA_PRESENT, "throw": _ERA_FUTURE,   "pingpong": _ERA_FUTURE},
    "rock":                   {"slap": _ERA_PAST,    "throw": _ERA_PAST,      "pingpong": _ERA_PRESENT},
    "rap":                    {"slap": _ERA_FUTURE,  "throw": _ERA_PRESENT,   "pingpong": _ERA_PRESENT},
    "electronic":             {"slap": _ERA_FUTURE,  "throw": _ERA_FUTURE,   "pingpong": _ERA_FUTURE},
}

# DelayStyle 枚举值（REAPER 扫描确定）
_STYLE_SINGLE = 0.0             # Single
_STYLE_PINGPONG = 5.0 / 8.0    # PingPong (0.625)

# DelayLSync / DelayRSync 枚举值
_SYNC_MSEC   = 0.0   # Msec（手动 ms，Slap 用）
_SYNC_NOTE   = 0.5   # Note（音符同步，Throw 用）
_SYNC_DOTTED = 0.75  # Dotted（附点音符，PingPong 用）

# DelayLNote / DelayRNote 枚举值
_NOTE_1_8 = 0.3  # 1/8 音符

_DEFAULTS: dict[str, float] = {
    "FEEDBACK_PCT": 15, "DRIVE_DB": 2.4,
    "HPF_HZ": 250, "LPF_HZ": 4500,
}


# ════════════════════════════════════════════════════════════════
# 公共 API
# ════════════════════════════════════════════════════════════════


def normalize_params(physical: dict[str, float]) -> dict[str, float]:
    """安全钳位：确保所有值在 [0.0, 1.0] 范围内。"""
    return {k: max(0.0, min(1.0, v)) for k, v in physical.items()}


def build_params(
    ctx,
    *,
    bpm: float | None = None,
    delay_type: str = "slap",
) -> dict:
    """根据流派和 BPM 构建延迟参数（ValhallaDelay 参数名）。

    Parameters
    ----------
    ctx : object
        需提供 ``ctx.genre`` 属性（str）。
    bpm : float | None
        歌曲速度。为 None 时 Slap 正常输出，
        Throw/PingPong 旁通（Bypass=1.0）。
    delay_type : str
        ``"slap"`` | ``"throw"`` | ``"pingpong"``。

    Returns
    -------
    dict
        ValhallaDelay 参数 {name: norm} 映射。
    """
    genre = getattr(ctx, "genre", "pop") or "pop"
    has_bpm = bpm is not None and bpm > 0
    eff_bpm = bpm if has_bpm else _DEFAULT_BPM

    # ── Slap: 固定 ms，Msec 模式 ──
    # ── Throw: 1/8 音符 Note 同步 ──
    # ── PingPong: 附点 1/8 Dotted 同步 ──
    if delay_type == "slap":
        mult = _GENRE_DELAY_MULT.get(
            genre, _GENRE_DELAY_MULT.get("pop", _DEFAULT_MULT),
        )
        delay_ms = round(_SLAP_FIXED_MS * mult, 1)
        delayl_norm = _ms_to_delayl_norm(delay_ms)
        sync_l = _SYNC_MSEC
        sync_r = _SYNC_MSEC
        delayl_note = _NOTE_1_8   # 不影响（Msec 模式），填默认值
        delayr_note = _NOTE_1_8
    elif delay_type == "throw":
        # 1/8 音符，Note 同步，自动跟工程 BPM
        delay_ms = round(60_000.0 / eff_bpm * 0.5, 1)
        delayl_norm = _ms_to_delayl_norm(delay_ms)
        sync_l = _SYNC_NOTE
        sync_r = _SYNC_NOTE
        delayl_note = _NOTE_1_8
        delayr_note = _NOTE_1_8
    else:  # pingpong
        # 附点 1/8，Dotted 同步
        delay_ms = round(60_000.0 / eff_bpm * 0.75, 1)
        delayl_norm = _ms_to_delayl_norm(delay_ms)
        sync_l = _SYNC_DOTTED
        sync_r = _SYNC_DOTTED
        delayl_note = _NOTE_1_8
        delayr_note = _NOTE_1_8

    # ── Bypass: Throw/PingPong 无 BPM 时旁通 ──
    if delay_type == "slap":
        bypass = 0.0
    else:
        bypass = 0.0 if has_bpm else 1.0

    # ── DelayStyle: Single / PingPong ──
    style = _STYLE_PINGPONG if delay_type == "pingpong" else _STYLE_SINGLE

    # ── Mode（流派 × 延迟类型）──
    mode = _GENRE_MODE.get(genre, {}).get(
        delay_type, _GENRE_MODE["pop"].get(delay_type, _MODE_DIGITAL),
    )

    # ── Era（流派 × 延迟类型）──
    era = _GENRE_ERA.get(genre, {}).get(
        delay_type, _GENRE_ERA["pop"].get(delay_type, _ERA_FUTURE),
    )

    # ── Feedback ──
    fb_tab = _FEEDBACK_TABLES.get(delay_type, _SLAP_FEEDBACK_PCT)
    fb_pct = fb_tab.get(genre, fb_tab.get("pop", _DEFAULTS["FEEDBACK_PCT"]))
    feedback = _pct_to_feedback_norm(fb_pct)

    # ── DriveIn (Saturation) ──
    drv_tab = _DRIVE_TABLES.get(delay_type, _SLAP_DRIVE_DB)
    drive_db = drv_tab.get(genre, drv_tab.get("pop", _DEFAULTS["DRIVE_DB"]))
    drive = _db_to_drivein_norm(drive_db)

    # ── LowCut (HPF) ──
    hpf_tab = _HPF_HZ_TABLES.get(delay_type, _SLAP_HPF_HZ)
    hpf_hz = hpf_tab.get(genre, hpf_tab.get("pop", _DEFAULTS["HPF_HZ"]))
    lowcut = _hz_to_lowcut_norm(hpf_hz)

    # ── HighCut (LPF) ──
    lpf_tab = _LPF_HZ_TABLES.get(delay_type, _SLAP_LPF_HZ)
    lpf_hz = lpf_tab.get(genre, lpf_tab.get("pop", _DEFAULTS["LPF_HZ"]))
    highcut = _hz_to_highcut_norm(lpf_hz)

    # ── 日志 ──
    log.info(
        "Auto-Delay[VD](%s): sync=%.2f time=%.4f(%.1fms) fb=%.4f(%d%%) "
        "drive=%.4f(%.1fdB) lcut=%.4f(%dHz) hcut=%.4f(%dHz) "
        "style=%.4f mode=%.4f bypass=%.0f (genre=%s bpm=%s)",
        delay_type, sync_l, delayl_norm, delay_ms, feedback, fb_pct,
        drive, drive_db, lowcut, hpf_hz, highcut, lpf_hz,
        style, mode, bypass, genre,
        f"{eff_bpm:.0f}" if has_bpm else "N/A",
    )

    return {
        # ── 时值 ──
        "DelayL_Ms":       delayl_norm,
        "DelayR_Ms":       delayl_norm,
        "DelayLSync":      sync_l,          # Msec / Note / Dotted
        "DelayRSync":      sync_r,
        "DelayLNote":      delayl_note,     # 1/8 音符（Note/Dotted 模式生效）
        "DelayRNote":      delayr_note,
        # ── 模式 ──
        "DelayStyle":      style,           # Single / PingPong
        "Mode":            mode,
        "Era":             era,
        # ── 反馈 / 染色 ──
        "Feedback":        feedback,
        "DriveIn":         drive,
        "Age":             _AGE,
        "Diffusion":       _DIFFUSION,
        "DiffSize":        _DIFFSIZE,
        # ── 滤波 ──
        "LowCut":          lowcut,
        "HighCut":         highcut,
        # ── 立体声 ──
        "Width":           _WIDTH,
        # ── 其他 ──
        "Mix":             _MIX_WET,
        "ModRate":         _MODRATE,
        "ModDepth":        _MODDEPTH,
        "Ducking":         _DUCKING,
        "Bypass":          bypass,
    }
