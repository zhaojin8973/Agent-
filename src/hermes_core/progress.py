"""
进度回调系统 — Agent 注册回调以接收混音管线的实时进度。

所有耗时操作（渲染、分析、导入）通过此系统向 Agent 报告进度，
避免 Agent 在长时间操作中失去对用户的状态反馈能力。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass, field
from typing import Callable

log = logging.getLogger(__name__)


@dataclass
class ProgressEvent:
    """单个进度事件。"""

    stage: str           # 当前阶段名称，如 "gain_staging"
    step: int            # 当前步骤（从 1 开始）
    total_steps: int     # 总步骤数
    message: str         # 人类可读的描述
    pct: float = 0.0     # 进度百分比 0.0-100.0
    is_error: bool = False
    is_warning: bool = False


class ProgressReporter:
    """进度报告器 — Agent 注册回调以接收实时进度。

    用法::

        reporter = ProgressReporter(total_steps=8)
        reporter.on_progress = lambda e: print(f"[{e.pct:.0f}%] {e.message}")

        reporter.stage_start("gain_staging", "增益分级中...")
        reporter.step(1, "分析人声电平...")
        reporter.step(2, "计算增益补偿...")
        reporter.stage_done("gain_staging")

    支持嵌套阶段（通过内部栈管理）。
    """

    def __init__(self, total_steps: int = 1):
        self._total_steps = max(1, total_steps)
        self._current_step = 0
        self._stage_stack: list[str] = []
        self._callbacks: list[Callable[[ProgressEvent], None]] = []
        self._lock = threading.Lock()

    # ── 属性 ──────────────────────────────────────────────────────

    @property
    def pct(self) -> float:
        """当前进度百分比（0.0-100.0）。"""
        if self._total_steps <= 0:
            return 0.0
        return min(100.0, (self._current_step / self._total_steps) * 100.0)

    @property
    def current_step(self) -> int:
        """当前步骤编号（从 0 开始）。"""
        return self._current_step

    @property
    def total_steps(self) -> int:
        """总步骤数。"""
        return self._total_steps

    # ── 回调注册 ──────────────────────────────────────────────────

    def on_progress(self, callback: Callable[[ProgressEvent], None]) -> None:
        """注册进度回调。

        可以多次调用以注册多个回调，每个回调都会在进度更新时被调用。
        回调中抛出的异常不会传播，只记录日志。

        Parameters
        ----------
        callback : Callable
            接收 ProgressEvent 的可调用对象。
        """
        with self._lock:
            self._callbacks.append(callback)

    # ── 阶段管理 ──────────────────────────────────────────────────

    def stage_start(self, stage: str, message: str = "") -> None:
        """开始一个新阶段（推入阶段栈）。

        Parameters
        ----------
        stage : str
            阶段名称，如 "gain_staging"。
        message : str
            人类可读的阶段描述。
        """
        with self._lock:
            self._stage_stack.append(stage)
        log.debug("阶段开始: %s", stage)
        if message:
            event = ProgressEvent(
                stage=stage,
                step=self._current_step,
                total_steps=self._total_steps,
                message=message,
                pct=self.pct,
            )
            self._emit(event)

    def stage_done(self, stage: str) -> None:
        """结束当前阶段（从阶段栈弹出）。

        Parameters
        ----------
        stage : str
            要结束的阶段名称，必须与栈顶一致。
        """
        with self._lock:
            if self._stage_stack and self._stage_stack[-1] == stage:
                self._stage_stack.pop()
                log.debug("阶段完成: %s", stage)
            else:
                log.warning(
                    "阶段栈不匹配: 期望 %s，当前栈顶 %s",
                    stage,
                    self._stage_stack[-1] if self._stage_stack else "(空)",
                )

    @property
    def current_stage(self) -> str | None:
        """当前阶段名称（栈顶），栈空时返回 None。"""
        with self._lock:
            return self._stage_stack[-1] if self._stage_stack else None

    # ── 步骤报告 ──────────────────────────────────────────────────

    def step(self, step_num: int, message: str) -> None:
        """报告指定步骤的进度。

        Parameters
        ----------
        step_num : int
            当前步骤编号（从 1 开始）。
        message : str
            人类可读的步骤描述。
        """
        with self._lock:
            self._current_step = max(0, step_num)
            stage = self._stage_stack[-1] if self._stage_stack else ""
            pct = self.pct
        event = ProgressEvent(
            stage=stage,
            step=step_num,
            total_steps=self._total_steps,
            message=message,
            pct=pct,
        )
        self._emit(event)

    def advance(self, message: str) -> None:
        """自动递增步骤并报告。

        Parameters
        ----------
        message : str
            人类可读的步骤描述。
        """
        with self._lock:
            self._current_step += 1
            step = self._current_step
            stage = self._stage_stack[-1] if self._stage_stack else ""
            pct = self.pct
        event = ProgressEvent(
            stage=stage,
            step=step,
            total_steps=self._total_steps,
            message=message,
            pct=pct,
        )
        self._emit(event)

    def warning(self, message: str) -> None:
        """报告警告（不中断流程）。

        Parameters
        ----------
        message : str
            警告描述。
        """
        with self._lock:
            stage = self._stage_stack[-1] if self._stage_stack else ""
            step = self._current_step
        event = ProgressEvent(
            stage=stage,
            step=step,
            total_steps=self._total_steps,
            message=message,
            pct=self.pct,
            is_warning=True,
        )
        self._emit(event)

    def error(self, message: str) -> None:
        """报告错误。

        Parameters
        ----------
        message : str
            错误描述。
        """
        with self._lock:
            stage = self._stage_stack[-1] if self._stage_stack else ""
            step = self._current_step
        event = ProgressEvent(
            stage=stage,
            step=step,
            total_steps=self._total_steps,
            message=message,
            pct=self.pct,
            is_error=True,
        )
        self._emit(event)

    # ── 内部方法 ──────────────────────────────────────────────────

    def _emit(self, event: ProgressEvent) -> None:
        """调用所有注册的回调。

        每个回调的异常都会被捕获并记录日志，不会传播到其他回调。
        """
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(event)
            except Exception:
                log.exception("进度回调异常（已忽略）: %s", callback)
