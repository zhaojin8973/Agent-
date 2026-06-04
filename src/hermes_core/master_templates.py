"""
大师空间模板 — 四位知名混音师的空间效果链模板。

每个模板构建一组辅助轨道（延迟/混响返回），配置 EQ、空间插件和发送。
所有函数接受 REAPER 子管理器作为显式参数，不依赖 MixingEngine。
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from hermes_core.eq_engine import _apply_proq3_eq
from hermes_core.spatial_engine import _apply_return_eq

if TYPE_CHECKING:
    from hermes_core.bridge import ReaperBridge
    from hermes_core.track import TrackManager
    from hermes_core.fx import FxManager
    from hermes_core.send import SendManager

log = logging.getLogger(__name__)

# ── 模板调度键 → 函数映射 ──────────────────────────────────

_TEMPLATE_DISPATCH: dict[str, str] = {
    "cla":             "_master_cla",
    "chris_lord-alge": "_master_cla",
    "hewitt":          "_master_hewitt",
    "ryan_hewitt":     "_master_hewitt",
    "serban":          "_master_serban",
    "serban_ghenea":   "_master_serban",
    "townsend":        "_master_townsend",
    "devin_townsend":  "_master_townsend",
}

AVAILABLE_TEMPLATES = ["cla", "hewitt", "serban", "townsend"]


# ════════════════════════════════════════════════════════════════
# Townsend 辅助 — HPF + LPF EQ 块
# ════════════════════════════════════════════════════════════════


def _townsend_hp_lp_eq(fx: "FxManager", aux: int, eq_idx: int) -> None:
    """Townsend 延迟/混响轨道的标准 Pro-Q 3 滤波器块。

    HPF @ 400 Hz + LPF @ 3 kHz — 激进过滤以避免低频堆积和齿音刺耳。
    """
    if eq_idx < 0:
        return
    try:
        intent = {
            "bands": [
                {"band_type": "hp", "freq_hz": 400,
                 "gain_db": 0.0, "q": 0.71, "reason": "DT HPF 400Hz"},
                {"band_type": "lp", "freq_hz": 3000,
                 "gain_db": 0.0, "q": 0.71, "reason": "DT LPF 3kHz"},
            ],
            "spectral_tilt": "neutral", "mud_detected": False,
        }
        normed = _apply_proq3_eq(intent)
        for pn, pv in normed.items():
            fx.set_param(aux, eq_idx, pn, pv)
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
# 调度器
# ════════════════════════════════════════════════════════════════


def apply_master_template(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    send: "SendManager",
    master_name: str,
    vocal_track: int,
    genre: str = "pop",
    bpm: float | None = None,
) -> dict:
    """调度大师空间模板。

    Parameters
    ----------
    master_name : str
        模板名。大小写不敏感。支持:
        ``"cla"`` / ``"chris lord-alge"``,
        ``"hewitt"`` / ``"ryan hewitt"``,
        ``"serban"`` / ``"serban ghenea"``,
        ``"townsend"`` / ``"devin townsend"``.
    vocal_track : int
        人声轨索引。
    genre : str
        流派键，用于回退参数。
    bpm : float | None
        工程速度，延迟音符值需要。

    Returns
    -------
    dict
        模板结果，格式因模板而异。

    Raises
    ------
    ValueError
        未知模板名。
    """
    name_lower = master_name.lower().replace(" ", "_")
    func_name = _TEMPLATE_DISPATCH.get(name_lower)
    if func_name is None:
        raise ValueError(
            f"未知大师模板 '{master_name}'。可用: {AVAILABLE_TEMPLATES}"
        )
    log.info("应用大师模板: %s", master_name)
    func = globals()[func_name]
    return func(bridge, tracks, fx, send, vocal_track, genre, bpm)


# ════════════════════════════════════════════════════════════════
# Master A: Chris Lord-Alge
# ════════════════════════════════════════════════════════════════


def _master_cla(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    send: "SendManager",
    vocal_track: int,
    genre: str,
    bpm: float | None,
) -> dict:
    """Master A: Chris Lord-Alge — 延迟送入混响。

    3 条延迟 + 3 条混响，延迟输出送入混响产生光泽尾音。
    """
    result: dict = {"delays": {}, "reverbs": {}, "cross_sends": []}

    # ── 延迟总线 ──────────────────────────────────────
    delay_specs = [
        {
            "key": "slap", "name": "CLA Slap",
            "time_val": 0.05, "feedback": 0.10,
            "lowcut": 0.12, "mode": 0.0,
        },
        {
            "key": "throw", "name": "CLA Throw",
            "time_val": 0.08, "feedback": 0.15,
            "lowcut": 0.12, "mode": 0.0,
        },
        {
            "key": "tape", "name": "CLA Tape",
            "time_val": 0.04, "feedback": 0.20,
            "lowcut": 0.12, "highcut": 0.40,
            "mode": 0.3,
        },
    ]
    delay_tracks: list[int] = []
    for ds in delay_specs:
        aux = tracks.create(name=ds["name"])
        delay_tracks.append(aux)
        # Pro-Q 3 HPF
        eq_idx = fx.add(aux, "FabFilter Pro-Q 3")
        if eq_idx >= 0:
            hpf_intent = {
                "bands": [{"band_type": "hp", "freq_hz": 200,
                           "gain_db": 0.0, "q": 0.71, "reason": "CLA HPF 200Hz"}],
                "spectral_tilt": "neutral", "mud_detected": False,
            }
            try:
                normed = _apply_proq3_eq(hpf_intent)
                for pn, pv in normed.items():
                    fx.set_param(aux, eq_idx, pn, pv)
            except Exception:
                pass

        # EchoBoy
        eb_idx = fx.add(aux, "EchoBoy")
        if eb_idx >= 0:
            eb_params = {
                "Echo1Time": ds["time_val"], "Feedback": ds["feedback"],
                "Mix": 1.0, "LowCut": ds.get("lowcut", 0.12),
                "Saturation": 0.15,
            }
            if "highcut" in ds:
                eb_params["HighCut"] = ds["highcut"]
            for pn, pv in eb_params.items():
                fx.set_param(aux, eb_idx, pn, pv)

        # 发送
        send_info = send.create(
            src=vocal_track, dest=aux, level_db=-15.0,
        )
        result["delays"][ds["key"]] = {
            "aux_index": aux, "fx_index": eb_idx, "send": send_info,
        }

    # ── 混响总线 ──────────────────────────────────────
    reverb_specs = [
        {"key": "plate", "name": "CLA Plate", "plugin": "Little Plate",
         "params": {"Decay": 0.32, "Mix": 1.0, "Low Cut": 0.15}},
        {"key": "room", "name": "CLA Room", "plugin": "ValhallaRoom",
         "params": {"decay": 0.18, "mix": 1.0, "predelay": 0.05}},
        {"key": "hall", "name": "CLA Hall", "plugin": "LX480",
         "params": {
             "E1: Reverb Time Mid (RTM)": 0.32,
             "E1: Pre Delay (PDL)": 0.15,
             "E1: Mix (MIX)": 1.0,
         }},
    ]
    reverb_tracks: list[int] = []
    for rs in reverb_specs:
        aux = tracks.create(name=rs["name"])
        reverb_tracks.append(aux)
        # Pro-Q 3 HPF 250Hz
        eq_idx = fx.add(aux, "FabFilter Pro-Q 3")
        if eq_idx >= 0:
            try:
                hpf_intent = {
                    "bands": [{"band_type": "hp", "freq_hz": 250,
                               "gain_db": 0.0, "q": 0.71, "reason": "CLA HPF 250Hz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                normed = _apply_proq3_eq(hpf_intent)
                for pn, pv in normed.items():
                    fx.set_param(aux, eq_idx, pn, pv)
            except Exception:
                pass

        # 混响插件
        rv_idx = fx.add(aux, rs["plugin"])
        if rv_idx >= 0:
            for pn, pv in rs["params"].items():
                fx.set_param(aux, rv_idx, pn, pv)

        # 发送
        send_info = send.create(
            src=vocal_track, dest=aux, level_db=-14.0,
        )
        result["reverbs"][rs["key"]] = {
            "aux_index": aux, "fx_index": rv_idx, "send": send_info,
        }

    # ── 跨发送: 延迟 → 混响（CLA 秘方）────────────────
    for dt in delay_tracks:
        for rvt in reverb_tracks:
            try:
                send.create(src=dt, dest=rvt, level_db=-8.0)
                result["cross_sends"].append({
                    "src": dt, "dest": rvt, "level_db": -8.0,
                })
            except Exception as exc:
                log.debug("CLA cross-send failed: %s", exc)

    log.info(
        "CLA template: %d delays + %d reverbs + %d cross-sends",
        len(delay_tracks), len(reverb_tracks), len(result["cross_sends"]),
    )
    return result


# ════════════════════════════════════════════════════════════════
# Master B: Ryan Hewitt
# ════════════════════════════════════════════════════════════════


def _master_hewitt(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    send: "SendManager",
    vocal_track: int,
    genre: str,
    bpm: float | None,
) -> dict:
    """Master B: Ryan Hewitt — 三层 EMT 140 板混响。

    不同 Pre-Delay 创造「立体声→单声道崩塌」效果。
    优先使用 UAD EMT 140，回退到 ValhallaPlate。
    """
    result: dict = {"plates": {}}
    plate_specs = [
        {
            "key": "plate_1_mono", "name": "HP Plate 1 (Mono)",
            "PreDly": 0.50, "DampA": 0.60, "DampB": 0.55,
            "Width": 0.0, "LowCut": 0.12, "send_db": -14.0,
        },
        {
            "key": "plate_2_stereo", "name": "HP Plate 2 (Stereo)",
            "PreDly": 0.13, "DampA": 0.55, "DampB": 0.50,
            "Width": 0.50, "LowCut": 0.17, "send_db": -13.0,
        },
        {
            "key": "plate_3_wide", "name": "HP Plate 3 (Wide)",
            "PreDly": 0.13, "DampA": 0.50, "DampB": 0.45,
            "Width": 1.0, "LowCut": 0.12, "send_db": -12.0,
        },
    ]
    for ps in plate_specs:
        aux = tracks.create(name=ps["name"])
        # Pro-Q 3 HPF
        eq_idx = fx.add(aux, "FabFilter Pro-Q 3")
        hpf_hz = 180 if "plate_1" in ps["key"] or "plate_3" in ps["key"] else 250
        if eq_idx >= 0:
            try:
                hpf_intent = {
                    "bands": [{"band_type": "hp", "freq_hz": hpf_hz,
                               "gain_db": 0.0, "q": 0.71,
                               "reason": f"Hewitt HPF {hpf_hz}Hz"}],
                    "spectral_tilt": "neutral", "mud_detected": False,
                }
                normed = _apply_proq3_eq(hpf_intent)
                for pn, pv in normed.items():
                    fx.set_param(aux, eq_idx, pn, pv)
            except Exception:
                pass

        # 优先 UAD EMT 140，回退 ValhallaPlate
        plate_idx = fx.add(aux, "UAD EMT 140")
        if plate_idx < 0:
            plate_idx = fx.add(aux, "ValhallaPlate")
            if plate_idx >= 0:
                vp_params = {
                    "Decay": 0.40, "PreDelay": ps["PreDly"],
                    "Size": 0.40, "Width": ps["Width"],
                    "Type": 0.3, "Mix": 1.0,
                }
                for pn, pv in vp_params.items():
                    fx.set_param(aux, plate_idx, pn, pv)
        else:
            uad_params = {
                "PreDly": ps["PreDly"], "Width": ps["Width"],
                "Mix": 1.0, "LowCut": ps["LowCut"],
                "DampA": ps.get("DampA", 0.55),
                "DampB": ps.get("DampB", 0.50),
            }
            for pn, pv in uad_params.items():
                fx.set_param(aux, plate_idx, pn, pv)

        send_info = send.create(
            src=vocal_track, dest=aux, level_db=ps["send_db"],
        )
        result["plates"][ps["key"]] = {
            "aux_index": aux, "fx_index": plate_idx, "send": send_info,
        }

    log.info("Hewitt template: 3 plates (UAD EMT 140 preferred)")
    return result


# ════════════════════════════════════════════════════════════════
# Master C: Serban Ghenea
# ════════════════════════════════════════════════════════════════


def _master_serban(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    send: "SendManager",
    vocal_track: int,
    genre: str,
    bpm: float | None,
) -> dict:
    """Master C: Serban Ghenea — 干净透明的 Sidechain Ducking 空间。

    5 条标准返回轨，每条挂 Pro-C 2 侧链压缩（人声触发）。
    注意：Sidechain 路由需要 REAPER 通道 3/4 接线，当前版本
    仅添加 Pro-C 2 并设置参数，sidechain 接线需手动完成。
    """
    result: dict = {"buses": {}}
    bus_specs = [
        {"key": "plate", "name": "SG Plate", "plugin": "FabFilter Pro-R",
         "params": {"Decay Rate": 0.35, "Mix": 1.0, "Predelay": 0.12,
                    "Brightness": 0.55, "Character": 0.40},
         "send_db": -12.0},
        {"key": "hall", "name": "SG Hall", "plugin": "LX480",
         "params": {
             "E1: Reverb Time Mid (RTM)": 0.32,
             "E1: Pre Delay (PDL)": 0.22,
             "E1: Mix (MIX)": 1.0,
         }, "send_db": -14.0},
        {"key": "room", "name": "SG Room", "plugin": "ValhallaRoom",
         "params": {"decay": 0.10, "mix": 1.0, "predelay": 0.05},
         "send_db": -16.0},
        {"key": "slap", "name": "SG Slap", "plugin": "EchoBoy",
         "params": {"Echo1Time": 0.05, "Feedback": 0.10,
                    "Mix": 1.0, "Saturation": 0.10, "LowCut": 0.12},
         "send_db": -14.0},
        {"key": "rhythm", "name": "SG Rhythm", "plugin": "EchoBoy",
         "params": {"RhythmNote": 0.30, "Feedback": 0.20,
                    "Mix": 1.0, "Saturation": 0.10, "LowCut": 0.12},
         "send_db": -16.0},
    ]
    for bs in bus_specs:
        aux = tracks.create(name=bs["name"])
        # Pro-Q 3
        eq_idx = fx.add(aux, "FabFilter Pro-Q 3")
        if eq_idx >= 0:
            _apply_return_eq(fx, aux, eq_idx, bs["key"], genre)

        # 空间插件
        fx_idx = fx.add(aux, bs["plugin"])
        if fx_idx >= 0:
            for pn, pv in bs["params"].items():
                fx.set_param(aux, fx_idx, pn, pv)

        # Sidechain 压缩: Pro-C 2
        # 注意：通道 3/4 接线需要手动设置
        sc_idx = fx.add(aux, "FabFilter Pro-C 2")
        if sc_idx >= 0:
            sc_params = {
                "Threshold": 0.35, "Ratio": 0.15,
                "Attack": 0.05, "Release": 0.25,
                "Knee": 0.10, "Range": 0.10,
                "Makeup Gain": 0.0,
            }
            for pn, pv in sc_params.items():
                fx.set_param(aux, sc_idx, pn, pv)
            log.info(
                "Serban sidechain: Pro-C 2 on '%s' — "
                "手动设置通道 3/4 接线以完成 sidechain 路由", bs["name"],
            )

        send_info = send.create(
            src=vocal_track, dest=aux, level_db=bs["send_db"],
        )
        result["buses"][bs["key"]] = {
            "aux_index": aux, "fx_index": fx_idx, "send": send_info,
            "sidechain_fx": sc_idx,
        }

    log.info("Serban template: 5 buses + sidechain compression")
    return result


# ════════════════════════════════════════════════════════════════
# Master D: Devin Townsend
# ════════════════════════════════════════════════════════════════


def _master_townsend(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    send: "SendManager",
    vocal_track: int,
    genre: str,
    bpm: float | None,
) -> dict:
    """Master D: Devin Townsend — 不对称延迟 + 廉价混响粘合。

    左右延迟不同时间 + 高 Feedback 产生雾状空间，
    Little Plate 粘合整体，Pro-Q 3 激进 EQ 过滤。
    """
    result: dict = {}

    # ── L Delay (EchoBoy SpaceEcho, 300ms, FB 40%, 硬左) ──
    l_aux = tracks.create(name="DT L Delay")
    l_eq = fx.add(l_aux, "FabFilter Pro-Q 3")
    _townsend_hp_lp_eq(fx, l_aux, l_eq)
    l_eb = fx.add(l_aux, "EchoBoy")
    if l_eb >= 0:
        for pn, pv in {
            "Echo1Time": 0.12, "Feedback": 0.40, "Mix": 1.0,
            "Saturation": 0.25, "LowCut": 0.18,
        }.items():
            fx.set_param(l_aux, l_eb, pn, pv)
    l_send = send.create(src=vocal_track, dest=l_aux, level_db=-12.0)
    # 硬左声像
    send.set_pan(vocal_track, l_send.get("index", 0), -1.0)
    result["left_delay"] = {"aux_index": l_aux, "send": l_send, "pan": -1.0}

    # ── R Delay (EchoBoy SpaceEcho, 500ms, FB 40%, 硬右) ──
    r_aux = tracks.create(name="DT R Delay")
    r_eq = fx.add(r_aux, "FabFilter Pro-Q 3")
    _townsend_hp_lp_eq(fx, r_aux, r_eq)
    r_eb = fx.add(r_aux, "EchoBoy")
    if r_eb >= 0:
        for pn, pv in {
            "Echo1Time": 0.18, "Feedback": 0.40, "Mix": 1.0,
            "Saturation": 0.25, "LowCut": 0.18,
        }.items():
            fx.set_param(r_aux, r_eb, pn, pv)
    r_send = send.create(src=vocal_track, dest=r_aux, level_db=-12.0)
    send.set_pan(vocal_track, r_send.get("index", 0), 1.0)
    result["right_delay"] = {"aux_index": r_aux, "send": r_send, "pan": 1.0}

    # ── Glue Verb (Little Plate 1.5s + 激进 Post-EQ) ──
    g_aux = tracks.create(name="DT Glue Verb")
    g_eq = fx.add(g_aux, "FabFilter Pro-Q 3")
    _townsend_hp_lp_eq(fx, g_aux, g_eq)
    g_fx = fx.add(g_aux, "Little Plate")
    if g_fx >= 0:
        for pn, pv in {"Decay": 0.25, "Mix": 1.0, "Low Cut": 0.18}.items():
            fx.set_param(g_aux, g_fx, pn, pv)
    g_send = send.create(src=vocal_track, dest=g_aux, level_db=-10.0)
    result["glue_reverb"] = {
        "aux_index": g_aux, "fx_index": g_fx, "send": g_send,
        "post_eq": {"hpf": 400, "lpf": 3000},
    }

    log.info("Townsend template: L/R delays + glue verb")
    return result
