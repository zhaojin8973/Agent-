"""
段落差异化参数自动化 — 按歌曲段落应用不同的混音参数。

支持定义歌曲结构（主歌/副歌/桥段等），为每个段落设置
差异化的 FX 参数值，并通过 REAPER 自动化包络写入。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from hermes_core.engine import MixingEngine

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 数据结构
# ════════════════════════════════════════════════════════════════


@dataclass
class SectionDef:
    """歌曲段落定义。

    Parameters
    ----------
    name : str
        段落类型名称，如 ``"verse"``、``"chorus"``、``"bridge"``。
    start_sec : float
        段落起始时间（秒）。
    end_sec : float
        段落结束时间（秒）。
    """

    name: str
    start_sec: float
    end_sec: float

    @property
    def duration_sec(self) -> float:
        """段落时长（秒）。"""
        return self.end_sec - self.start_sec


@dataclass
class AutomationIntent:
    """参数自动化意图 — 定义某个参数在各段落的差异化值。

    Parameters
    ----------
    track_idx : int
        REAPER 轨道索引。
    param_name : str
        参数名（如 ``"Ratio"``、``"Band 2 Gain"``、``"Threshold"``）。
    section_values : dict[str, float]
        段落类型 → 目标值的映射。未列出的段落使用 *default_value*。
    default_value : float
        未在 *section_values* 中显式指定的段落默认值。
    fx_idx : int | None
        FX 槽位索引。None 表示轨道级参数（如音量、声像）。
    ramp_ms : float
        段落边界过渡斜率（毫秒）。用于避免参数突变产生的咔嗒声。
    """

    track_idx: int
    param_name: str
    section_values: dict[str, float] = field(default_factory=dict)
    default_value: float = 0.0
    fx_idx: int | None = None
    ramp_ms: float = 10.0


@dataclass
class TrackAutomation:
    """轨道级自动化配置 — 将多个 AutomationIntent 绑定到同一轨道。

    便捷构造器，用于同一轨道的多个参数自动化。
    """

    track_idx: int
    intents: list[AutomationIntent] = field(default_factory=list)

    def add_intent(
        self, param_name: str, section_values: dict[str, float],
        default_value: float = 0.0, fx_idx: int | None = None,
        ramp_ms: float = 10.0,
    ) -> "TrackAutomation":
        """添加一个参数自动化意图。"""
        self.intents.append(AutomationIntent(
            track_idx=self.track_idx,
            param_name=param_name,
            section_values=section_values,
            default_value=default_value,
            fx_idx=fx_idx,
            ramp_ms=ramp_ms,
        ))
        return self


# ════════════════════════════════════════════════════════════════
# 预定义段落模板
# ════════════════════════════════════════════════════════════════

# 常见流行歌曲结构模板（时间以秒为单位，需外部填入实际时间）
_POP_SONG_STRUCTURE: list[SectionDef] = [
    SectionDef("intro",  0.0,    15.0),
    SectionDef("verse",  15.0,   45.0),
    SectionDef("chorus", 45.0,   75.0),
    SectionDef("verse",  75.0,   105.0),
    SectionDef("chorus", 105.0,  135.0),
    SectionDef("bridge", 135.0,  165.0),
    SectionDef("chorus", 165.0,  210.0),
    SectionDef("outro",  210.0,  240.0),
]

# 段落参数预设差异（相对于 default_value 的偏移）
_SECTION_PARAM_PRESETS: dict[str, dict[str, dict[str, float]]] = {
    "verse": {
        "comp_ratio": {"default": 3.0},
        "eq_presence": {"default": 0.0},
        "reverb_level": {"default": -8.0},
        "threshold": {"default": -24.0},
    },
    "chorus": {
        "comp_ratio": {"default": 4.0},
        "eq_presence": {"default": 2.0},
        "reverb_level": {"default": -5.0},
        "threshold": {"default": -26.0},
    },
    "bridge": {
        "comp_ratio": {"default": 2.5},
        "eq_presence": {"default": -1.0},
        "reverb_level": {"default": -10.0},
        "threshold": {"default": -20.0},
    },
    "intro": {
        "comp_ratio": {"default": 2.0},
        "eq_presence": {"default": -2.0},
        "reverb_level": {"default": -12.0},
        "threshold": {"default": -18.0},
    },
    "outro": {
        "comp_ratio": {"default": 2.0},
        "eq_presence": {"default": -2.0},
        "reverb_level": {"default": -14.0},
        "threshold": {"default": -16.0},
    },
}


# ════════════════════════════════════════════════════════════════
# AutomationManager
# ════════════════════════════════════════════════════════════════


class AutomationManager:
    """段落差异化自动化管理器。

    接收歌曲段落结构，将各段落的参数变化写入 REAPER 自动化包络。

    用法::

        sections = [
            SectionDef("verse", 0, 30),
            SectionDef("chorus", 30, 60),
        ]
        intent = AutomationIntent(
            track_idx=0, param_name="Ratio",
            section_values={"verse": 3.0, "chorus": 4.0},
            default_value=3.0,
        )
        mgr = AutomationManager(engine)
        mgr.apply(sections, [intent])
    """

    def __init__(self, engine: "MixingEngine") -> None:
        """初始化自动化管理器。

        Parameters
        ----------
        engine : MixingEngine
            已连接的 MixingEngine 实例。
        """
        self._engine = engine

    # ── 公共 API ────────────────────────────────────────────

    def apply(
        self,
        sections: list[SectionDef],
        intents: list[AutomationIntent],
    ) -> dict:
        """将段落差异化参数写入 REAPER 自动化包络。

        Parameters
        ----------
        sections : list[SectionDef]
            歌曲段落结构（按时间顺序）。
        intents : list[AutomationIntent]
            参数自动化意图列表。

        Returns
        -------
        dict
            ``{written: int, skipped: int, errors: list[str]}``
        """
        written = 0
        skipped = 0
        errors: list[str] = []

        for intent in intents:
            points = self._build_envelope_points(sections, intent)
            if len(points) < 2:
                skipped += 1
                continue

            result = self._engine.write_automation(
                track_idx=intent.track_idx,
                param_name=intent.param_name,
                points=points,
            )
            if result.get("error"):
                errors.append(
                    f"track {intent.track_idx}/{intent.param_name}: "
                    f"{result['error']}"
                )
            else:
                written += 1
                log.info(
                    "自动化: track %d, %s — %d 段落, %d 点",
                    intent.track_idx, intent.param_name,
                    len(intent.section_values), result.get("point_count", 0),
                )

        return {"written": written, "skipped": skipped, "errors": errors}

    def apply_preset(
        self,
        sections: list[SectionDef],
        track_idx: int,
        param_kinds: list[str] | None = None,
        fx_idx: int | None = None,
    ) -> dict:
        """使用内置段落参数预设快速创建自动化。

        根据 *param_kinds* 从 ``_SECTION_PARAM_PRESETS`` 加载
        每个段落的参数值，写入自动化包络。

        Parameters
        ----------
        sections : list[SectionDef]
            歌曲段落结构。
        track_idx : int
            目标轨道索引。
        param_kinds : list[str] | None
            要自动化的参数类型列表。None 表示全部：
            ``["comp_ratio", "eq_presence", "reverb_level", "threshold"]``
        fx_idx : int | None
            FX 槽位索引。

        Returns
        -------
        dict
            与 :meth:`apply` 相同格式的结果字典。
        """
        if param_kinds is None:
            param_kinds = ["comp_ratio", "eq_presence", "reverb_level", "threshold"]

        intents: list[AutomationIntent] = []
        for kind in param_kinds:
            section_values: dict[str, float] = {}
            default_val = 0.0
            for section_type, params in _SECTION_PARAM_PRESETS.items():
                if kind in params:
                    val = params[kind].get("default", 0.0)
                    section_values[section_type] = val
                    # 使用第一个段落类型的值作为默认值
                    if default_val == 0.0 and val != 0.0:
                        default_val = val

            # 为当前歌曲中实际出现的段落筛选值
            active_sections = {s.name for s in sections}
            filtered_values = {
                k: v for k, v in section_values.items()
                if k in active_sections
            }

            if filtered_values:
                intents.append(AutomationIntent(
                    track_idx=track_idx,
                    param_name=kind,
                    section_values=filtered_values,
                    default_value=default_val,
                    fx_idx=fx_idx,
                ))

        return self.apply(sections, intents)

    # ── 内部辅助 ────────────────────────────────────────────

    @staticmethod
    def _build_envelope_points(
        sections: list[SectionDef],
        intent: AutomationIntent,
    ) -> list[tuple[float, float]]:
        """根据段落结构和参数意图构建自动化时间值对。

        每个段落生成 4 个点（开始/斜坡结束/结束前/结束），
        实现平滑的段落间过渡。

        ::

            [section N-1 end]  →  [ramp up]  →  [hold]  →  [ramp down]
             previous_value        target_value   target    next_value
        """
        if not sections:
            return []

        sorted_secs = sorted(sections, key=lambda s: s.start_sec)
        ramp_sec = intent.ramp_ms / 1000.0
        points: list[tuple[float, float]] = []

        for i, sec in enumerate(sorted_secs):
            # 当前段落的参数值
            cur_val = intent.section_values.get(sec.name, intent.default_value)

            # 前一段落的参数值
            if i > 0:
                prev_sec = sorted_secs[i - 1]
                prev_val = intent.section_values.get(
                    prev_sec.name, intent.default_value,
                )
            else:
                prev_val = cur_val

            # 后一段落的参数值
            if i < len(sorted_secs) - 1:
                next_sec = sorted_secs[i + 1]
                next_val = intent.section_values.get(
                    next_sec.name, intent.default_value,
                )
            else:
                next_val = cur_val

            # ── 构建 4 个包络点 ──
            # 1. 段落起点：从上一段落的结束值开始
            if i == 0 or prev_val != cur_val:
                points.append((sec.start_sec, prev_val))

            # 2. 斜坡上升结束：当前段落的参数值
            ramp_end = min(sec.start_sec + ramp_sec, sec.start_sec + sec.duration_sec * 0.5)
            if ramp_end > sec.start_sec and cur_val != prev_val:
                points.append((ramp_end, cur_val))

            # 3. 段落结束前：保持当前值
            points.append((sec.end_sec - ramp_sec, cur_val))

            # 4. 段落终点（如果下一段落值不同）
            if i < len(sorted_secs) - 1 and cur_val != next_val:
                points.append((sec.end_sec, next_val))

        # 去重（按时间合并相同值的连续点）
        return _deduplicate_points(points)


def _deduplicate_points(
    points: list[tuple[float, float]],
) -> list[tuple[float, float]]:
    """去除时间值相同的重复点，按时间排序。"""
    if not points:
        return []
    sorted_pts = sorted(points, key=lambda p: p[0])
    result = [sorted_pts[0]]
    for pt in sorted_pts[1:]:
        # 跳过与上一点时间相同或值相同的连续点
        if pt[0] > result[-1][0]:
            result.append(pt)
    return result


# ════════════════════════════════════════════════════════════════
# 便捷函数
# ════════════════════════════════════════════════════════════════


def make_pop_song_structure(
    durations: dict[str, float] | None = None,
) -> list[SectionDef]:
    """使用自定义时长创建标准流行歌曲结构模板。

    Parameters
    ----------
    durations : dict[str, float] | None
        段落名称 → 时长（秒）的覆盖字典。
        None 表示使用默认时长（intro 15s, verse 30s, chorus 30s,
        bridge 30s, outro 30s）。

    Returns
    -------
    list[SectionDef]
        按时间排列的段落定义列表。
    """
    defaults = {
        "intro": 15.0, "verse": 30.0, "chorus": 30.0,
        "bridge": 30.0, "outro": 30.0,
    }
    dur = {**defaults, **(durations or {})}

    current = 0.0
    structure = _POP_SONG_STRUCTURE[:]
    result: list[SectionDef] = []

    for template in structure:
        sec_dur = dur.get(template.name, 30.0)
        result.append(SectionDef(
            name=template.name,
            start_sec=current,
            end_sec=current + sec_dur,
        ))
        current += sec_dur

    return result
