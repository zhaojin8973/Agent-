"""
空间效果发送量计算引擎。

基于人声信号分析（波峰因子、存在感缺失、浑浊度、齿音峰值）
计算流派感知的混响和延迟发送量。同时提供空间效果器参数解析和应用的辅助函数。
"""

import logging
from typing import TYPE_CHECKING

from hermes_core.genre_tables import (
    _GENRE_REVERB_SEND_BASE,
    _GENRE_DELAY_SEND_BASE,
    _GENRE_MICROSHIFT_SEND,
    _SEND_LEVEL_MIN,
    _SEND_LEVEL_MAX,
    _SEND_DISABLED_THRESHOLD,
    _CREST_REFERENCE,
    _PRESENCE_DEFICIT_THRESHOLD,
    _SIBILANCE_REFERENCE_PEAK,
    _SECTION_BOOST,
    _SPATIAL_PARAM_FALLBACK_MAP,
    _GENRE_SPATIAL_PARAMS,
    _SPATIAL_PLUGIN,
    _GENRE_RETURN_EQ,
    _DELAY_BUS_TYPES,
)
from hermes_core.normalize import PLUGIN_REGISTRY, normalize_params
from hermes_core.loudness_optimizer import EqIntent, EqBandIntent
from hermes_core.eq_engine import _apply_proq3_eq

if TYPE_CHECKING:
    from hermes_core.fx import FxManager

log = logging.getLogger(__name__)


def _compute_spatial_sends(
    genre: str,
    crest_factor_db: float,
    presence_deficit_db: float,
    mud_ratio_db: float,
    sibilance_peak_db: float | None = None,
    section: str = "verse",
) -> dict[str, float | None]:
    """根据人声信号分析计算混响和延迟发送量。

    每条发送量由流派参考基准值推导，再通过四个客观偏差调整：

    - **crest_bias**：高波峰因子的 vocals 已经听起来"大" —
      减少混响以避免冲淡动态。
    - **density_bias**：浑浊的 vocals 获得更少混响，避免在浑浊上
      堆积低频能量。
    - **presence_bias**：沉闷的人声（高存在感缺失）应保持前置 —
      混响会将其推远。
    - **sibilance_bias**（仅 plate）：plate 混响在 5–8 kHz 共振，
      所以明亮齿音的人声获得更少 plate 发送。

    Returns a dict mapping bus keys to send levels in dB.
    ``None`` means the bus is disabled for this genre (no need to
    create it).
    """
    _DEFAULT_REVERB = _GENRE_REVERB_SEND_BASE["pop"]
    _DEFAULT_DELAY = _GENRE_DELAY_SEND_BASE["pop"]

    base_reverb = _GENRE_REVERB_SEND_BASE.get(genre, _DEFAULT_REVERB)
    base_delay = _GENRE_DELAY_SEND_BASE.get(genre, _DEFAULT_DELAY)

    # ── 偏差计算 ──────────────────────────────────────────
    crest_bias = -(crest_factor_db - _CREST_REFERENCE) * 0.5
    density_bias = mud_ratio_db * 0.3
    presence_bias = -(presence_deficit_db - _PRESENCE_DEFICIT_THRESHOLD) * 0.3
    section_bias = _SECTION_BOOST.get(section, 0.0)

    sibilance_bias = 0.0
    if sibilance_peak_db is not None:
        sibilance_bias = -max(0.0, sibilance_peak_db - _SIBILANCE_REFERENCE_PEAK) * 0.1

    # ── 组装发送量 ─────────────────────────────────────────
    sends: dict[str, float | None] = {}

    for bus_type, base_db in base_reverb.items():
        bias = crest_bias + density_bias + presence_bias + section_bias
        if bus_type == "plate":
            bias += sibilance_bias
        sends[f"reverb_{bus_type}"] = round(
            max(_SEND_LEVEL_MIN, min(_SEND_LEVEL_MAX, base_db + bias)), 1,
        )

    for bus_type, base_db in base_delay.items():
        if base_db <= _SEND_DISABLED_THRESHOLD:
            sends[f"delay_{bus_type}"] = None
        else:
            bias = crest_bias + presence_bias + section_bias
            sends[f"delay_{bus_type}"] = round(
                max(_SEND_LEVEL_MIN, min(_SEND_LEVEL_MAX, base_db + bias)), 1,
            )

    # ── MicroShift AUX（§3.1）──────────────────────────────
    base_microshift = _GENRE_MICROSHIFT_SEND.get(genre, -12.0)  # 回退到 pop
    bias = crest_bias + presence_bias + section_bias
    sends["microshift"] = round(
        max(_SEND_LEVEL_MIN, min(_SEND_LEVEL_MAX, base_microshift + bias)), 1,
    )

    return sends


# ════════════════════════════════════════════════════════════════
# 空间效果器插件名解析
# ════════════════════════════════════════════════════════════════


def _resolve_spatial_plugin_key(fx_name: str) -> str | None:
    """将 REAPER 返回的插件名匹配到 PLUGIN_REGISTRY 键。

    先用子串匹配查找，失败后用 _SPATIAL_PARAM_FALLBACK_MAP 的键匹配。
    返回 PLUGIN_REGISTRY 的键名，找不到返回 None。
    """
    # 精确匹配
    if fx_name in PLUGIN_REGISTRY:
        return fx_name
    # 子串匹配（如 "VST3: EchoBoy (Soundtoys)" 匹配 PLUGIN_REGISTRY 键）
    name_lower = fx_name.lower()
    for key in PLUGIN_REGISTRY:
        if key.lower() in name_lower or name_lower in key.lower():
            return key
    # 回退映射键匹配（如 "ValhallaPlate" 匹配 "ValhallaPlate (Valhalla DSP, LLC)"）
    for fallback_key in _SPATIAL_PARAM_FALLBACK_MAP:
        if fallback_key.lower() in name_lower:
            return fallback_key
    return None


# ════════════════════════════════════════════════════════════════
# 空间效果器参数应用
# ════════════════════════════════════════════════════════════════


def _apply_abbey_road_eq(
    fx: "FxManager", aux_track: int, eq_fx_idx: int,
) -> None:
    """配置 ReaEQ 作为 Abbey Road 安全滤波器。

    Band 1: HPF @ 600 Hz（去除混响中的低频浑浊）。
    Band 2: LPF @ 10 kHz（去除齿音/刺耳声）。

    这些参数**不暴露给 Agent** — 它们是引擎级别的安全保护，
    自动应用于每条混响发送。
    """
    physical = {
        "Band 1 Type":    0.0,       # HPF
        "Band 1 Freq":    600.0,     # Hz
        "Band 1 Gain":    0.0,       # 不适用（HPF 无增益）
        "Band 1 Q":       0.7,       # 标准 12dB/oct 斜率
        "Band 1 Enabled": 1.0,
        "Band 2 Type":    4.0,       # LPF
        "Band 2 Freq":    10000.0,   # Hz
        "Band 2 Gain":    0.0,
        "Band 2 Q":       0.7,
        "Band 2 Enabled": 1.0,
        "Band 3 Enabled": 0.0,
        "Band 4 Enabled": 0.0,
    }
    try:
        normalized = normalize_params("ReaEQ (Cockos)", physical)
        for pname, pval in normalized.items():
            fx.set_param(aux_track, eq_fx_idx, pname, pval)
        log.debug(
            "Abbey Road EQ applied: HPF@600Hz + LPF@10kHz on aux %d slot %d",
            aux_track, eq_fx_idx,
        )
    except Exception as exc:
        log.warning(
            "Abbey Road EQ failed on aux %d slot %d: %s — "
            "reverb may have excess mud/sibilance",
            aux_track, eq_fx_idx, exc,
        )


def _apply_spatial_params(
    fx: "FxManager",
    aux_track: int,
    fx_idx: int,
    loaded_name: str,
    bus: str,
    genre: str,
    bpm: float | None = None,
) -> None:
    """对流派空间插件应用预设参数。

    1. 从 _GENRE_SPATIAL_PARAMS[genre][bus] 获取归一化参数
    2. 将 REAPER 返回的插件名匹配到 PLUGIN_REGISTRY
    3. 如果是回退插件，通过 _SPATIAL_PARAM_FALLBACK_MAP 转换参数名
    4. 通过 FxManager.set_param() 应用
    5. 特殊处理：音符值（如 "1/4"）需要 BPM 转换
    """
    genre_params = _GENRE_SPATIAL_PARAMS.get(
        genre, _GENRE_SPATIAL_PARAMS["pop"],
    )
    bus_params = genre_params.get(bus)
    if not bus_params:
        return  # 该流派/总线无预设参数

    registry_key = _resolve_spatial_plugin_key(loaded_name)
    if registry_key is None:
        log.debug(
            "_apply_spatial_params: plugin '%s' not in PLUGIN_REGISTRY "
            "— skipping param application", loaded_name,
        )
        return

    # 判断是否为回退插件（非首选插件）
    primary_candidates = _SPATIAL_PLUGIN.get(bus, [])
    is_fallback = (
        len(primary_candidates) > 0
        and not any(
            c.lower() in loaded_name.lower()
            for c in primary_candidates[:1]
        )
    )

    # 如果是回退插件，加载参数名映射
    fallback_map: dict[str, str] = {}
    if is_fallback:
        for fk in _SPATIAL_PARAM_FALLBACK_MAP:
            if fk.lower() in loaded_name.lower():
                fallback_map = _SPATIAL_PARAM_FALLBACK_MAP[fk]
                log.info(
                    "Using fallback param map for %s → %s (%d mappings)",
                    bus, fk, len(fallback_map),
                )
                break

    applied = 0
    skipped = 0
    for pname, pval in bus_params.items():
        # 如果是回退插件，先查映射表
        actual_pname = fallback_map.get(pname, pname) if fallback_map else pname

        # 检查参数是否在 PLUGIN_REGISTRY 的该插件条目中
        plugin_entry = PLUGIN_REGISTRY.get(registry_key, {})
        plugin_params = plugin_entry.get("params", {})
        if actual_pname not in plugin_params:
            skipped += 1
            continue

        ok = fx.set_param(aux_track, fx_idx, actual_pname, pval)
        if ok:
            applied += 1
        else:
            skipped += 1

    if applied > 0 or skipped > 0:
        log.info(
            "Spatial params (%s/%s/%s): %d applied, %d skipped",
            genre, bus, loaded_name, applied, skipped,
        )


def _apply_return_eq(
    fx: "FxManager",
    aux_track: int,
    eq_fx_idx: int,
    bus: str,
    genre: str,
) -> None:
    """配置 Pro-Q 3 作为返回轨道安全滤波器。

    Band 1: HPF — 去除混响/延迟中的低频浑浊。
    Band 2: LPF — 抑制尾音中的齿音和刺耳声。

    频率通过 :data:`_GENRE_RETURN_EQ` 进行流派和总线感知。
    """
    eq_defaults = _GENRE_RETURN_EQ.get(genre, _GENRE_RETURN_EQ["pop"])
    # 延迟总线共享 "delay" EQ 条目；混响总线使用其特定类型
    eq_key = "delay" if bus in _DELAY_BUS_TYPES else bus
    eq_cfg = eq_defaults.get(eq_key, {"hpf": 300, "lpf": 8000})

    hpf_hz = eq_cfg["hpf"]
    lpf_hz = eq_cfg["lpf"]

    # 构建最小 EqIntent：仅 HPF + LPF，无增益频段
    eq_intent = EqIntent(
        bands=[
            EqBandIntent(
                band_type="hp", freq_hz=hpf_hz, gain_db=0.0,
                q=1.0, reason=f"Return {bus} HPF @ {hpf_hz:.0f} Hz",
            ),
            EqBandIntent(
                band_type="lp", freq_hz=lpf_hz, gain_db=0.0,
                q=1.0, reason=f"Return {bus} LPF @ {lpf_hz:.0f} Hz",
            ),
        ],
        spectral_tilt="neutral",
        mud_detected=False,
    )
    normalized = _apply_proq3_eq(eq_intent)
    for pname, pval in normalized.items():
        fx.set_param(aux_track, eq_fx_idx, pname, pval)

    log.debug(
        "Return EQ: %s bus on aux %d — HPF=%.0f Hz, LPF=%.0f Hz (genre=%s)",
        bus, aux_track, hpf_hz, lpf_hz, genre,
    )
