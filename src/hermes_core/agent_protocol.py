"""
Agent Protocol — AI Agent 与 hermes-core 之间的结构化通信层。

定义 Agent（如 Hermes Agent / OpenClaw）调用混音引擎所需的
声明式数据结构和高层 API。

用法::

    from hermes_core.agent_protocol import HermesAgentAPI, MixRequest, MixGenre

    api = HermesAgentAPI(engine)
    request = MixRequest(
        project_name="张三_望归_Mix",
        vocal_stem="/path/to/vocal.wav",
        backing_stem="/path/to/backing.wav",
        genre=MixGenre.POP,
    )
    result = api.create_and_mix(request)
    if result.success:
        print(f"混音完成: {result.render_path}")
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable

from hermes_core.exceptions import HermesError

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 枚举定义
# ════════════════════════════════════════════════════════════════

class MixGenre(str, Enum):
    """混音流派枚举。"""
    POP = "pop"
    ROCK = "rock"
    FOLK = "folk"
    BALLAD = "ballad"
    ELECTRONIC = "electronic"
    HIPHOP = "hiphop"
    RNB = "rnb"
    JAZZ = "jazz"
    CHINESE_FOLK_BEL_CANTO = "chinese_folk_bel_canto"


class ReverbStyle(str, Enum):
    """混响风格枚举。"""
    PLATE = "plate"
    HALL = "hall"
    ROOM = "room"
    CHAMBER = "chamber"
    SPRING = "spring"


class AdjustmentType(str, Enum):
    """增量调整类型 — 基于用户自然语言反馈。

    每个值对应一种常见的混音调整意图，
    由 Agent 解析用户反馈后映射得来。
    """
    EQ_BRIGHTER = "brighter"
    EQ_WARMER = "warmer"
    EQ_LESS_MUDDY = "less_muddy"
    COMPRESS_MORE = "more_compress"
    COMPRESS_LESS = "less_compress"
    REVERB_MORE = "more_reverb"
    REVERB_LESS = "less_reverb"
    VOCAL_LOUDER = "vocal_louder"
    VOCAL_QUIETER = "vocal_quieter"
    DELAY_MORE = "more_delay"
    DELAY_LESS = "less_delay"


# ════════════════════════════════════════════════════════════════
# 请求/响应数据类
# ════════════════════════════════════════════════════════════════

@dataclass
class MixOptions:
    """可选混音参数覆盖。

    所有字段均可为 None，表示使用引擎默认值。
    """
    target_lufs: float | None = None          # 目标响度，如 -14.0
    reverb_style: ReverbStyle | None = None   # 混响风格
    eq_brightness: float | None = None        # 0.0-1.0，越高越亮
    compression_amount: float | None = None   # 0.0-1.0
    stem_gain_db: float | None = None         # 分轨增益补偿


@dataclass
class MixRequest:
    """Agent 发起的混音请求。

    包含一次完整混音所需的所有声明式参数。
    """
    project_name: str
    vocal_stem: str                           # 人声分轨文件路径
    backing_stem: str                         # 伴奏文件路径
    genre: MixGenre = MixGenre.POP
    producer: str = "Hermes"
    category: str = "music"
    options: MixOptions = field(default_factory=MixOptions)

    def to_dict(self) -> dict:
        """序列化为字典，便于跨进程传输或日志记录。"""
        return {
            "project_name": self.project_name,
            "vocal_stem": self.vocal_stem,
            "backing_stem": self.backing_stem,
            "genre": self.genre.value,
            "producer": self.producer,
            "category": self.category,
            "options": {
                "target_lufs": self.options.target_lufs,
                "reverb_style": (
                    self.options.reverb_style.value
                    if self.options.reverb_style else None
                ),
                "eq_brightness": self.options.eq_brightness,
                "compression_amount": self.options.compression_amount,
                "stem_gain_db": self.options.stem_gain_db,
            },
        }

    @classmethod
    def from_dict(cls, data: dict) -> MixRequest:
        """从字典反序列化。"""
        opts_raw = data.get("options", {})
        reverb_style = None
        if opts_raw.get("reverb_style"):
            reverb_style = ReverbStyle(opts_raw["reverb_style"])

        return cls(
            project_name=data["project_name"],
            vocal_stem=data["vocal_stem"],
            backing_stem=data["backing_stem"],
            genre=MixGenre(data.get("genre", "pop")),
            producer=data.get("producer", "Hermes"),
            category=data.get("category", "music"),
            options=MixOptions(
                target_lufs=opts_raw.get("target_lufs"),
                reverb_style=reverb_style,
                eq_brightness=opts_raw.get("eq_brightness"),
                compression_amount=opts_raw.get("compression_amount"),
                stem_gain_db=opts_raw.get("stem_gain_db"),
            ),
        )


@dataclass
class MixResult:
    """混音结果。

    包含完整混音的产出物路径、响度分析数据和审计报告。
    """
    success: bool
    project_path: str | None = None
    render_path: str | None = None            # 全质量 WAV 渲染路径
    preview_path: str | None = None           # MP3 预览路径
    lufs_integrated: float | None = None
    lufs_short_term: float | None = None
    true_peak_db: float | None = None
    duration_sec: float | None = None
    operations_log: list[dict] = field(default_factory=list)
    audit_report: dict | None = None
    error: str | None = None
    error_hint: str | None = None

    @classmethod
    def ok(cls, **kwargs) -> MixResult:
        """创建成功结果。"""
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, error: str, hint: str | None = None, **kwargs) -> MixResult:
        """创建失败结果。"""
        return cls(success=False, error=error, error_hint=hint, **kwargs)


@dataclass
class AdjustRequest:
    """增量调整请求。

    Agent 根据用户自然语言反馈（如"人声再亮一点"）
    生成调整意图，发送给引擎执行。
    """
    adjustment_type: AdjustmentType
    intensity: float = 1.0                    # 0.5=轻微, 1.0=标准, 2.0=明显
    description: str = ""                     # 原始用户反馈文本（用于审计）


@dataclass
class AdjustResult:
    """增量调整结果。"""
    success: bool
    before_preview: str | None = None         # 调整前预览片段路径
    after_preview: str | None = None          # 调整后预览片段路径
    changes_applied: list[str] = field(default_factory=list)
    error: str | None = None
    error_hint: str | None = None

    @classmethod
    def ok(cls, **kwargs) -> AdjustResult:
        """创建成功结果。"""
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, error: str, hint: str | None = None,
             **kwargs) -> AdjustResult:
        """创建失败结果。"""
        return cls(success=False, error=error, error_hint=hint, **kwargs)


@dataclass
class StatusResult:
    """工程状态查询结果。"""
    pipeline_state: str
    project_name: str | None = None
    track_count: int = 0
    fx_count: int = 0
    is_rendering: bool = False
    progress_pct: float = 0.0
    current_stage: str = ""


@dataclass
class AuditResult:
    """混音质量审计结果。"""
    lufs_integrated: float | None = None
    true_peak_db: float | None = None
    dynamic_range_db: float | None = None
    warnings: list[str] = field(default_factory=list)
    suggestions: list[str] = field(default_factory=list)

    @classmethod
    def from_audit_dict(cls, data: dict) -> AuditResult:
        """从 engine.audit_mix() 返回的原始字典构建。"""
        diag = data.get("diagnostics", {})
        warnings: list[str] = []
        suggestions: list[str] = []

        for check in data.get("checks", []):
            msg = check.get("message", "")
            sev = check.get("severity", "")
            if sev in ("critical", "warning"):
                warnings.append(msg)
            if sev == "info" and check.get("check_name") != "all_clear":
                suggestions.append(msg)

        return cls(
            lufs_integrated=diag.get("integrated_lufs"),
            true_peak_db=diag.get("true_peak_dbtp"),
            dynamic_range_db=(
                diag.get("rms_db") and diag.get("peak_db") and
                round(diag["peak_db"] - diag["rms_db"], 1)
            ),
            warnings=warnings,
            suggestions=suggestions,
        )


@dataclass
class PreviewResult:
    """预览渲染结果。"""
    success: bool
    preview_path: str | None = None
    before_path: str | None = None            # A/B 对比：调整前
    after_path: str | None = None             # A/B 对比：调整后
    format: str = "wav"
    bitrate_kbps: int = 128
    error: str | None = None

    @classmethod
    def ok(cls, **kwargs) -> PreviewResult:
        """创建成功结果。"""
        return cls(success=True, **kwargs)

    @classmethod
    def fail(cls, error: str, **kwargs) -> PreviewResult:
        """创建失败结果。"""
        return cls(success=False, error=error, **kwargs)


# ════════════════════════════════════════════════════════════════
# 错误转换工具
# ════════════════════════════════════════════════════════════════

def _to_mix_error(exc: Exception) -> tuple[str, str | None]:
    """将引擎异常转换为用户可读的错误消息和提示。

    对常见 REAPER 连接问题进行友好的错误转换。
    """
    from hermes_core.exceptions import (
        BridgeConnectionError, InvalidStateError, HermesError,
    )
    msg = str(exc)
    hint: str | None = None

    if isinstance(exc, BridgeConnectionError):
        hint = "请确认 REAPER 已启动且 reapy bridge 处于活跃状态"
    elif isinstance(exc, InvalidStateError):
        hint = "引擎管线状态异常，尝试调用 engine.reset() 重置"
    elif isinstance(exc, HermesError):
        hint = "详见操作日志获取更多信息"
    elif "No project path" in msg:
        hint = "请先调用 create_and_mix() 创建工程"

    return msg, hint


# ════════════════════════════════════════════════════════════════
# HermesAgentAPI — Agent 高层 API
# ════════════════════════════════════════════════════════════════

# 调整类型 → 参数映射表
# 每个调整类型的 (param_name, delta_per_intensity_unit, description)
_ADJUST_MAP: dict[AdjustmentType, dict] = {
    AdjustmentType.EQ_BRIGHTER: {
        "description": "提升高频亮度",
        "gain_target": "eq_brightness",
        "gain_delta": +0.15,
    },
    AdjustmentType.EQ_WARMER: {
        "description": "增强低频温暖感",
        "gain_target": "eq_warmth",
        "gain_delta": +0.15,
    },
    AdjustmentType.EQ_LESS_MUDDY: {
        "description": "减少低频浑浊",
        "gain_target": "eq_mud_cut",
        "gain_delta": +0.15,
    },
    AdjustmentType.COMPRESS_MORE: {
        "description": "增加压缩量",
        "gain_target": "compression_amount",
        "gain_delta": +0.15,
    },
    AdjustmentType.COMPRESS_LESS: {
        "description": "减少压缩量",
        "gain_target": "compression_amount",
        "gain_delta": -0.15,
    },
    AdjustmentType.REVERB_MORE: {
        "description": "增加混响发送量",
        "gain_target": "reverb_send",
        "gain_delta": +1.5,
    },
    AdjustmentType.REVERB_LESS: {
        "description": "减少混响发送量",
        "gain_target": "reverb_send",
        "gain_delta": -1.5,
    },
    AdjustmentType.VOCAL_LOUDER: {
        "description": "提升人声电平",
        "gain_target": "vocal_gain",
        "gain_delta": +1.5,
    },
    AdjustmentType.VOCAL_QUIETER: {
        "description": "降低人声电平",
        "gain_target": "vocal_gain",
        "gain_delta": -1.5,
    },
    AdjustmentType.DELAY_MORE: {
        "description": "增加延迟发送量",
        "gain_target": "delay_send",
        "gain_delta": +1.5,
    },
    AdjustmentType.DELAY_LESS: {
        "description": "减少延迟发送量",
        "gain_target": "delay_send",
        "gain_delta": -1.5,
    },
}

# 流派名称到 engine genre 字符串的映射。
# engine 内部不支持 hiphop/rnb/jazz，回退到 pop。
_GENRE_ENGINE_MAP: dict[MixGenre, str] = {
    MixGenre.POP: "pop",
    MixGenre.ROCK: "rock",
    MixGenre.FOLK: "folk",
    MixGenre.BALLAD: "ballad",
    MixGenre.ELECTRONIC: "electronic",
    MixGenre.HIPHOP: "pop",                     # engine 暂不支持，回退
    MixGenre.RNB: "pop",
    MixGenre.JAZZ: "pop",
    MixGenre.CHINESE_FOLK_BEL_CANTO: "chinese_folk_bel_canto",
}

# engine 目标 LUFS 后备值
_DEFAULT_TARGET_LUFS: float = -10.0


class HermesAgentAPI:
    """Agent 调用的高级 API — 所有方法返回结构化结果。

    封装 :class:`MixingEngine`，提供声明式、容错的混音接口。
    所有公开方法都返回 dataclass 结果对象，不会抛出异常。

    Attributes
    ----------
    engine : MixingEngine
        底层的混音引擎实例，可用于更细粒度的控制。
    """

    def __init__(self, engine=None):
        """初始化 Agent API。

        Parameters
        ----------
        engine : MixingEngine | None
            MixingEngine 实例。如果为 None，延迟创建
            （需在调用 create_and_mix 前设置）。
        """
        self._engine = engine
        self._project_name: str | None = None
        self._ops_log: list[dict] = []

    @property
    def engine(self):
        """返回底层 MixingEngine 实例。"""
        if self._engine is None:
            from hermes_core.engine import MixingEngine
            self._engine = MixingEngine()
        return self._engine

    @engine.setter
    def engine(self, value):
        self._engine = value

    # ── 主要入口：create_and_mix ──────────────────────────

    def create_and_mix(
        self,
        request: MixRequest,
        on_progress: Callable[[str, float], None] | None = None,
    ) -> MixResult:
        """执行完整的端到端混音管线。

        管线步骤：
        1. 创建工程并导入分轨
        2. 分析信号 → 增益 staging
        3. 应用流派配置文件（EQ + 压缩 + 空间效果器）
        4. 执行 post-FX fader 平衡
        5. 母带终混（Pro-L 2 limiter）
        6. 渲染全质量 WAV
        7. 运行安全审计

        Parameters
        ----------
        request : MixRequest
            混音请求，包含工程名、分轨路径、流派等参数。
        on_progress : Callable | None
            进度回调 ``(stage: str, pct: float)``。

        Returns
        -------
        MixResult
            结构化混音结果。
        """
        self._ops_log.clear()
        eng = self.engine

        try:
            # 解析流派映射
            engine_genre = _GENRE_ENGINE_MAP.get(request.genre, "pop")
            if engine_genre != request.genre.value:
                log.info(
                    "流派 '%s' 映射为 engine genre '%s'",
                    request.genre.value, engine_genre,
                )

            # 确定目标 LUFS
            target_lufs = request.options.target_lufs
            if target_lufs is None:
                # 使用 engine 内置的流派目标
                from hermes_core.mastering import _get_genre_target_lufs
                target_lufs = _get_genre_target_lufs(engine_genre)

            # 准备输出目录
            from hermes_core.config import HermesConfig
            try:
                cfg = HermesConfig.load()
                root = cfg.project_root_expanded
            except Exception:
                root = os.path.expanduser("~/REAPER 工程文件")

            output_dir = os.path.join(
                root, request.category, request.project_name,
            )

            # ── 阶段 1: 创建工程 ──────────────────────────
            self._report_progress(on_progress, "create_project", 0.05)
            with eng:
                proj = eng.create_project(
                    name=request.project_name,
                    output_dir=output_dir,
                    category=request.category,
                    producer=request.producer,
                    genre=engine_genre,
                )
                self._ops_log.append({
                    "stage": "create_project",
                    "project_path": proj.get("path"),
                    "meta_dir": proj.get("meta_dir"),
                })

                # ── 阶段 2: 准备分轨 ─────────────────────
                self._report_progress(on_progress, "prepare_stems", 0.15)
                stem_paths = [request.vocal_stem, request.backing_stem]
                import_result = eng.prepare_stems(
                    stem_paths,
                    genre=engine_genre,
                    vocal_indices=[0],
                    backing_indices=[1],
                )
                self._ops_log.append({
                    "stage": "prepare_stems",
                    "stems": [
                        {k: v for k, v in s.items()
                         if k in ("file_path", "role", "track_index",
                                  "raw_rms_db", "raw_peak_db", "clip_gain_db")}
                        for s in import_result.get("stems", [])
                    ],
                })

                # ── 阶段 3: 加载并应用配置文件 ────────────
                self._report_progress(on_progress, "apply_profile", 0.30)
                from hermes_core.profiles import MixingProfile

                profile = MixingProfile.for_genre(engine_genre)
                # 如果指定了混响风格，覆盖配置文件的混响设置
                if request.options.reverb_style:
                    profile.reverb_style = request.options.reverb_style.value

                eng.apply_profile(
                    profile,
                    vocal_track=0,
                    backing_tracks=[1],
                    genre=engine_genre,
                )
                self._ops_log.append({
                    "stage": "apply_profile",
                    "genre": engine_genre,
                    "vocal_fx": [f.name for f in profile.vocal_chain],
                    "backing_fx": [f.name for f in profile.backing_chain],
                })

                # ── 阶段 4: post-FX 平衡 ──────────────────
                self._report_progress(on_progress, "post_fx_balance", 0.50)
                balance = eng.post_fx_balance(
                    vocal_indices=[0],
                    backing_indices=[1],
                    genre=engine_genre,
                )
                self._ops_log.append({
                    "stage": "post_fx_balance",
                    **{k: v for k, v in balance.items()
                       if k in ("ratio_lu", "vocal_lufs", "backing_lufs",
                                "combined_lufs", "combined_peak_db")},
                })

                # ── 阶段 5: 母带终混 ──────────────────────
                self._report_progress(on_progress, "finalize_master", 0.65)
                master_result = eng.finalize_master(
                    target_lufs=target_lufs,
                    on_progress=(
                        lambda s, p: self._report_progress(
                            on_progress, f"master_{s}", 0.65 + p * 0.20,
                        )
                        if on_progress else None
                    ),
                )
                self._ops_log.append({
                    "stage": "finalize_master",
                    "target_lufs": target_lufs,
                    "passed": master_result.get("passed"),
                    "achieved_lufs": master_result.get("achieved_lufs"),
                    "gain_db": master_result.get("gain_db"),
                })

                if not master_result.get("passed"):
                    return MixResult.fail(
                        error=(
                            master_result.get("error")
                            or "母带终混未通过验证"
                        ),
                        hint=master_result.get("hint"),
                        operations_log=self._ops_log,
                    )

                # ── 阶段 6: 渲染全质量 WAV ────────────────
                self._report_progress(on_progress, "render_mix", 0.90)
                render = eng.render_mix(
                    os.path.join(output_dir, "render"),
                    verify=True,
                )
                render_path = render.get("output_path")
                self._ops_log.append({
                    "stage": "render_mix",
                    "output_path": render_path,
                })

                signal_check = render.get("signal_check", {})
                duration = signal_check.get("duration_sec")

                # ── 阶段 7: 安全审计 ──────────────────────
                self._report_progress(on_progress, "audit", 0.95)
                audit_raw = {}
                if render_path and os.path.exists(render_path):
                    audit_raw = eng.audit_mix(render_path)
                else:
                    audit_raw = {
                        "passed": False,
                        "checks": [{"check_name": "render",
                                    "severity": "critical",
                                    "message": "Render output not found"}],
                        "diagnostics": {},
                    }

                self._ops_log.append({
                    "stage": "audit",
                    "passed": audit_raw.get("passed"),
                    "checks_count": len(audit_raw.get("checks", [])),
                })

                # ── 阶段 8: 生成 MP3 预览 ─────────────────
                preview_path = self._generate_preview(render_path, output_dir)

                # ── 保存工程 ──────────────────────────────
                eng.save_project()

                self._report_progress(on_progress, "done", 1.0)

                return MixResult.ok(
                    project_path=proj.get("path"),
                    render_path=render_path,
                    preview_path=preview_path,
                    lufs_integrated=signal_check.get("integrated_lufs"),
                    lufs_short_term=(
                        master_result.get("probe_lufs")
                    ),
                    true_peak_db=signal_check.get("true_peak_dbtp"),
                    duration_sec=duration,
                    operations_log=self._ops_log,
                    audit_report=audit_raw,
                )

        except Exception as exc:
            msg, hint = _to_mix_error(exc)
            log.exception("create_and_mix 失败: %s", msg)
            return MixResult.fail(
                error=msg,
                hint=hint,
                operations_log=self._ops_log,
            )

    # ── 增量调整 ───────────────────────────────────────────

    def adjust(self, request: AdjustRequest) -> AdjustResult:
        """执行增量混音调整。

        基于用户自然语言反馈（如"人声再亮一点"），
        对当前工程的 FX 参数进行增量修改。

        Parameters
        ----------
        request : AdjustRequest
            调整请求，包含调整类型和强度。

        Returns
        -------
        AdjustResult
            调整结果，包含变更描述和预览对比路径。
        """
        eng = self.engine
        mapping = _ADJUST_MAP.get(request.adjustment_type)

        if mapping is None:
            return AdjustResult.fail(
                f"不支持的调整类型: {request.adjustment_type.value}",
                hint="支持的调整类型见 AdjustmentType 枚举",
            )

        try:
            desc = mapping["description"]
            delta = mapping["gain_delta"] * request.intensity
            target = mapping["gain_target"]
            changes_applied: list[str] = [
                f"{desc} (target={target}, delta={delta:+.1f}, "
                f"intensity={request.intensity})",
            ]

            if request.description:
                changes_applied.append(
                    f"基于用户反馈: '{request.description}'"
                )

            # 检查引擎状态
            if eng._pipeline_state is None:
                return AdjustResult.fail(
                    "引擎尚未初始化管线，请先调用 create_and_mix()",
                    hint="create_and_mix() 会执行完整管线并创建工程",
                )

            # 根据调整类型执行对应的引擎操作
            try:
                self._apply_adjustment(eng, request.adjustment_type,
                                       request.intensity)
            except Exception as adj_exc:
                log.warning(
                    "调整 %s 在引擎层部分失败: %s",
                    request.adjustment_type.value, adj_exc,
                )
                changes_applied.append(
                    f"警告: 引擎层调整部分失败 — {adj_exc}"
                )

            return AdjustResult.ok(
                changes_applied=changes_applied,
            )

        except Exception as exc:
            msg, hint = _to_mix_error(exc)
            log.exception("adjust 失败: %s", msg)
            return AdjustResult.fail(error=msg, hint=hint)

    def _apply_adjustment(
        self, eng, adj_type: AdjustmentType, intensity: float,
    ) -> None:
        """将调整类型映射到具体的引擎操作。

        此方法为增量调整的核心实现。引擎的 DAG dirty-flag 机制
        确保了参数变更后下游节点会自动失效重算。
        """
        # 获取声乐轨上的 FX 链参数
        chain = eng.get_fx_chain(0) if hasattr(eng, "get_fx_chain") else []

        if adj_type in (AdjustmentType.EQ_BRIGHTER,
                         AdjustmentType.EQ_WARMER,
                         AdjustmentType.EQ_LESS_MUDDY):
            # EQ 调整 → 通过 DAG 节点更新
            self._adjust_eq_via_dag(eng, adj_type, intensity, chain)

        elif adj_type in (AdjustmentType.VOCAL_LOUDER,
                           AdjustmentType.VOCAL_QUIETER):
            # 声乐电平调整 → 修改声轨 fader
            eng.apply_gain(0, intensity * 1.5)

        elif adj_type in (AdjustmentType.REVERB_MORE,
                           AdjustmentType.REVERB_LESS):
            # 混响发送量调整 → 通过 SendNode
            self._adjust_reverb_send(eng, intensity)

        elif adj_type in (AdjustmentType.DELAY_MORE,
                           AdjustmentType.DELAY_LESS):
            # 延迟发送量调整
            self._adjust_delay_send(eng, intensity)

        elif adj_type in (AdjustmentType.COMPRESS_MORE,
                           AdjustmentType.COMPRESS_LESS):
            # 压缩调整
            self._adjust_compression(eng, adj_type, intensity, chain)

    def _adjust_eq_via_dag(self, eng, adj_type: AdjustmentType,
                           intensity: float, chain: list) -> None:
        """通过 AudioNode DAG 调整 EQ 参数。"""
        vocal_nodes = eng._vocal_chain_nodes if hasattr(
            eng, "_vocal_chain_nodes") else []
        eq_nodes = [n for n in vocal_nodes if n.fx_type == "eq"]
        if not eq_nodes:
            log.info("EQ 调整：未找到 EQ 节点，跳过")
            return

        # 取第一个 EQ 节点进行调整
        node = eq_nodes[0]
        if adj_type == AdjustmentType.EQ_BRIGHTER:
            # 提高高频 shelf / presence
            eng.update_node_param(node, "presence_boost",
                                  intensity * 0.5)
        elif adj_type == AdjustmentType.EQ_WARMER:
            # 增强低频
            eng.update_node_param(node, "warmth_boost",
                                  intensity * 0.5)
        elif adj_type == AdjustmentType.EQ_LESS_MUDDY:
            # 增加 mud cut
            eng.update_node_param(node, "mud_cut",
                                  intensity * 0.5)

    def _adjust_reverb_send(self, eng, intensity: float) -> None:
        """调整混响发送量。"""
        send_node = (
            eng._reverb_send_node
            if hasattr(eng, "_reverb_send_node") else None
        )
        if send_node is None:
            log.info("混响调整：无活跃 reverb send node，跳过")
            return

        current_db = send_node.params.get("level_db", -12.0)
        # 防御：params 中的值可能不是数字类型
        try:
            current_db = float(current_db)
        except (TypeError, ValueError):
            log.debug("reverb send level_db 不是数字，使用默认值 -12.0")
            current_db = -12.0
        new_db = current_db + intensity * 1.5
        # 限制范围
        new_db = max(-24.0, min(-6.0, new_db))

        new_params = dict(send_node.params)
        new_params["level_db"] = round(new_db, 1)
        send_node.update_params(new_params)

        # 如果 send node 关联了 REAPER send，也更新
        aux_idx = send_node.params.get("aux_index")
        if aux_idx is not None:
            try:
                eng._send.set_volume(0, aux_idx, new_db)
            except Exception as exc:
                log.debug("更新 reverb send 音量失败: %s", exc)

    def _adjust_delay_send(self, eng, intensity: float) -> None:
        """调整延迟发送量。"""
        log.info("延迟调整暂未完全实现，需扩展引擎 API")

    def _adjust_compression(self, eng, adj_type: AdjustmentType,
                            intensity: float, chain: list) -> None:
        """调整压缩参数。"""
        vocal_nodes = eng._vocal_chain_nodes if hasattr(
            eng, "_vocal_chain_nodes") else []
        comp_nodes = [n for n in vocal_nodes
                      if n.fx_type in ("comp", "fet", "vca", "opto", "rvox")]
        if not comp_nodes:
            log.info("压缩调整：未找到压缩节点，跳过")
            return

        node = comp_nodes[0]
        sign = 1.0 if adj_type == AdjustmentType.COMPRESS_MORE else -1.0
        eng.update_node_param(node, "gr_target_db",
                              intensity * sign * 1.0)

    # ── 预览 ──────────────────────────────────────────────

    def preview(
        self, duration_sec: float = 15.0,
    ) -> PreviewResult:
        """生成混音预览片段。

        Parameters
        ----------
        duration_sec : float
            预览时长（秒）。

        Returns
        -------
        PreviewResult
            预览结果，包含预览文件路径。
        """
        eng = self.engine

        try:
            tmp_dir = tempfile.mkdtemp(prefix="hermes_preview_")
            result = eng.render_preview(
                output_dir=tmp_dir,
                target_lufs=_DEFAULT_TARGET_LUFS,
            )
            preview_path = result.get("output_path")

            if preview_path and os.path.exists(preview_path):
                return PreviewResult.ok(
                    preview_path=preview_path,
                    format="wav",
                )
            else:
                return PreviewResult.fail(
                    error=result.get("error", "预览渲染失败"),
                )

        except Exception as exc:
            msg, hint = _to_mix_error(exc)
            log.exception("preview 失败: %s", msg)
            return PreviewResult.fail(error=msg)

    # ── 状态查询 ──────────────────────────────────────────

    def get_status(self) -> StatusResult:
        """查询当前工程状态。

        Returns
        -------
        StatusResult
            包含管线阶段、轨道数、FX 数等信息。
        """
        eng = self.engine

        try:
            # 管线状态
            pipeline_state = (
                eng._pipeline_state.value
                if hasattr(eng, "_pipeline_state") and eng._pipeline_state
                else "unknown"
            )

            # 工程信息
            proj_info = {}
            try:
                proj_info = eng.get_project_info()
            except Exception:
                pass

            # 轨道信息
            track_count = 0
            try:
                tracks = eng.list_tracks()
                track_count = len(tracks)
            except Exception:
                pass

            # FX 计数（遍历声乐轨）
            fx_count = 0
            try:
                chain = eng.get_fx_chain(0)
                fx_count = len(chain) if chain else 0
            except Exception:
                pass

            # 当前阶段
            current_stage = ""
            if hasattr(eng, "_meta") and eng._meta:
                stages = eng._meta.stages if hasattr(
                    eng._meta, "stages") else []
                current_stage = stages[-1] if stages else ""

            return StatusResult(
                pipeline_state=pipeline_state,
                project_name=proj_info.get("name"),
                track_count=track_count,
                fx_count=fx_count,
                is_rendering=False,
                progress_pct=0.0,
                current_stage=current_stage,
            )

        except Exception as exc:
            log.exception("get_status 失败: %s", exc)
            return StatusResult(pipeline_state="error")

    # ── 审计 ──────────────────────────────────────────────

    def get_audit(self) -> AuditResult:
        """获取最后一次混音的质量审计报告。

        Returns
        -------
        AuditResult
            结构化的审计结果。
        """
        eng = self.engine

        try:
            # 尝试从 ops_log 中找到渲染路径
            render_path = None
            for entry in self._ops_log:
                if entry.get("stage") == "render_mix":
                    render_path = entry.get("output_path")
                    break

            if render_path and os.path.exists(render_path):
                raw = eng.audit_mix(render_path)
                return AuditResult.from_audit_dict(raw)

            # 快速分析：仅从引擎状态推断
            try:
                info = eng.get_project_info()
                if info.get("track_count", 0) > 0:
                    return AuditResult(
                        warnings=["审计需要已完成渲染的混音文件"],
                        suggestions=["调用 create_and_mix() 完成混音后再审计"],
                    )
                else:
                    return AuditResult(
                        warnings=["工程中无轨道数据"],
                        suggestions=["请先创建工程并导入分轨"],
                    )
            except Exception:
                return AuditResult(
                    warnings=["无法获取工程状态"],
                    suggestions=["请确认 REAPER 已连接"],
                )

        except Exception as exc:
            log.exception("get_audit 失败: %s", exc)
            return AuditResult(warnings=[str(exc)])

    # ── 辅助方法 ──────────────────────────────────────────

    def _report_progress(
        self,
        on_progress: Callable[[str, float], None] | None,
        stage: str,
        pct: float,
    ) -> None:
        """安全调用进度回调。"""
        if on_progress is None:
            return
        try:
            on_progress(stage, pct)
        except Exception as exc:
            log.debug("进度回调异常: %s", exc)

    @staticmethod
    def _generate_preview(
        render_path: str | None,
        output_dir: str,
    ) -> str | None:
        """从全质量 WAV 生成 MP3 预览。

        注意：依赖 pydub/ffmpeg 外部工具，失败时静默跳过。
        """
        if not render_path or not os.path.exists(render_path):
            return None

        preview_path = os.path.join(
            output_dir or os.path.dirname(render_path),
            f"{os.path.splitext(os.path.basename(render_path))[0]}_preview.mp3",
        )

        # 尝试使用 pydub
        try:
            from pydub import AudioSegment
            audio = AudioSegment.from_wav(render_path)
            audio.export(preview_path, format="mp3", bitrate="128k")
            log.info("MP3 预览已生成: %s", preview_path)
            return preview_path
        except ImportError:
            log.debug("pydub 未安装，跳过 MP3 预览生成")
        except Exception as exc:
            log.warning("MP3 预览生成失败: %s", exc)

        return None
