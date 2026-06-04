"""
预览渲染 — 快速低质量渲染用于用户试听和 A/B 对比。

支持 MP3 128kbps 快速预览和调整前后对比片段生成。
"""

from __future__ import annotations

import base64
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass
class PreviewResult:
    """预览渲染结果。"""
    success: bool
    preview_path: str | None = None
    before_path: str | None = None      # A/B 对比：调整前预览
    after_path: str | None = None       # A/B 对比：调整后预览
    format: str = "mp3"
    bitrate_kbps: int = 128
    duration_sec: float = 0.0
    error: str | None = None


# REAPER 非交互渲染 action ID
_RENDER_ACTION = 42230

# MP3 sink code: "3pm " (3pm + space = mp3 reversed)
_MP3_SINK = "3pm "

# REAPER render bounds flags
_BOUNDS_ENTIRE_PROJECT = 1
_BOUNDS_TIME_SELECTION = 2


class PreviewRenderer:
    """预览渲染器 — 快速生成低质量 MP3 预览。

    用法::

        renderer = PreviewRenderer(bridge)
        result = renderer.render_preview(
            output_dir="/tmp/previews",
            duration_sec=15.0,
            label="after_eq_boost"
        )
        if result.success:
            print(f"预览: {result.preview_path}")
    """

    def __init__(self, bridge):
        """
        Args:
            bridge: ReaperBridge 实例，用于调用 REAPER API。
        """
        self._bridge = bridge

    @property
    def api(self):
        return self._bridge.api

    # ── 渲染格式配置 ────────────────────────────────────────

    def _configure_mp3_render(self, output_dir: str, bitrate_kbps: int = 128) -> None:
        """配置 REAPER 渲染为 MP3 格式。

        通过 GetSetProjectInfo_String 设置 RENDER_FORMAT 为 MP3。
        MP3 sink code: "3pm " (mp3 反转)。
        """
        api = self.api

        # RENDER_FORMAT: base64(sink_code + b'\x18\x00\x01')
        # \x18 = 24 = MP3 bitrate index, \x00\x01 = flags
        fmt_encoded = base64.b64encode(
            _MP3_SINK.encode() + b"\x18\x00\x01"
        ).decode()
        api.GetSetProjectInfo_String(0, "RENDER_FORMAT", fmt_encoded, True)
        api.GetSetProjectInfo_String(0, "RENDER_FILE", output_dir, True)
        api.GetSetProjectInfo_String(0, "RENDER_PATTERN", "preview", True)
        api.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)
        api.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, True)

    def _configure_wav_render(self, output_dir: str) -> None:
        """配置 REAPER 渲染为 WAV 格式。"""
        api = self.api
        fmt_encoded = base64.b64encode(b"evaw\x18\x00\x01").decode()
        api.GetSetProjectInfo_String(0, "RENDER_FORMAT", fmt_encoded, True)
        api.GetSetProjectInfo_String(0, "RENDER_FILE", output_dir, True)
        api.GetSetProjectInfo_String(0, "RENDER_PATTERN", "preview", True)
        api.GetSetProjectInfo(0, "RENDER_CHANNELS", 2, True)
        api.GetSetProjectInfo(0, "RENDER_SETTINGS", 0, True)

    def _set_time_selection(self, start_sec: float, end_sec: float) -> None:
        """设置 REAPER 时间选区。

        使用 GetSet_LoopTimeRange API：
        GetSet_LoopTimeRange(True, False, start, end, False)
        """
        api = self.api
        api.GetSet_LoopTimeRange(True, False, start_sec, end_sec, False)

    def _get_time_selection(self) -> tuple[float, float]:
        """获取当前时间选区 (start_sec, end_sec)。"""
        api = self.api
        _, _, start, end, _ = api.GetSet_LoopTimeRange(
            False, False, 0, 0, False,
        )
        return (start, end)

    def _set_render_bounds(self, bounds: str) -> None:
        """设置渲染范围：entire_project 或 time_selection。"""
        api = self.api
        flag = (
            _BOUNDS_TIME_SELECTION if bounds == "time_selection"
            else _BOUNDS_ENTIRE_PROJECT
        )
        api.GetSetProjectInfo(0, "RENDER_BOUNDSFLAG", flag, True)

    def _trigger_render(self, output_path: str, timeout: float = 120.0) -> bool:
        """触发非交互渲染并等待输出文件出现。

        Returns True on success, False on timeout or error.
        """
        api = self.api

        # 聚焦 REAPER 窗口（macOS 专有，防止渲染输出静音）
        self._bridge.focus_reaper()
        time.sleep(0.3)

        # 触发渲染
        api.Main_OnCommand(_RENDER_ACTION, 0)

        # 等待输出文件
        start = time.time()
        while not os.path.exists(output_path):
            if time.time() - start > timeout:
                log.warning("渲染超时: %s", output_path)
                return False
            time.sleep(0.1)

        # 验证文件非零大小
        if os.path.getsize(output_path) == 0:
            log.warning("渲染输出为空: %s", output_path)
            return False

        return True

    # ── 公开 API ─────────────────────────────────────────

    def render_preview(
        self,
        output_dir: str | Path,
        duration_sec: float = 15.0,
        label: str = "preview",
        start_position_sec: float = 0.0,
    ) -> PreviewResult:
        """渲染快速预览（MP3 128kbps）。

        使用 REAPER 的非交互渲染命令（Action 42230）。

        Args:
            output_dir: 输出目录
            duration_sec: 预览时长（秒）
            label: 文件名标签
            start_position_sec: 渲染起始位置（秒）

        Returns:
            PreviewResult with preview_path on success
        """
        output_dir = str(output_dir)
        os.makedirs(output_dir, exist_ok=True)
        output_path = os.path.join(output_dir, f"{label}.mp3")

        try:
            # 保存原始时间选区
            orig_start, orig_end = self._get_time_selection()

            # 设置渲染区间
            end_sec = start_position_sec + duration_sec
            self._set_time_selection(start_position_sec, end_sec)

            # 配置 MP3 渲染
            self._configure_mp3_render(output_dir)
            self._set_render_bounds("time_selection")

            # 触发渲染
            ok = self._trigger_render(output_path)

            # 恢复原始时间选区
            self._set_time_selection(orig_start, orig_end)

            if ok:
                log.info("预览渲染完成: %s (%.1fs)", output_path, duration_sec)
                return PreviewResult(
                    success=True,
                    preview_path=output_path,
                    format="mp3",
                    bitrate_kbps=128,
                    duration_sec=duration_sec,
                )
            else:
                return PreviewResult(
                    success=False,
                    error="渲染失败：输出文件未生成或为空",
                )

        except Exception as exc:
            log.exception("预览渲染失败: %s", exc)
            return PreviewResult(
                success=False,
                error=str(exc),
            )

    def render_ab_comparison(
        self,
        output_dir: str | Path,
        before_label: str = "before",
        after_label: str = "after",
        duration_sec: float = 15.0,
    ) -> PreviewResult:
        """生成 A/B 对比预览（调整前 vs 调整后）。

        需要引擎在调整前后各调用一次。实际策略：
        - before 预览：当前混音状态
        - after 预览：需要先将当前混音状态暂存，应用调整后渲染，
          然后通过 REAPER undo 恢复暂存状态

        Returns:
            PreviewResult with before_path and after_path on success
        """
        output_dir = str(output_dir)
        os.makedirs(output_dir, exist_ok=True)

        before_path = os.path.join(output_dir, f"{before_label}.mp3")
        after_path = os.path.join(output_dir, f"{after_label}.mp3")

        try:
            api = self.api

            # 保存原始时间选区
            orig_start, orig_end = self._get_time_selection()

            # 配置渲染区间
            end_sec = duration_sec
            self._set_time_selection(0.0, end_sec)
            self._set_render_bounds("time_selection")

            # ── Before 渲染 ──
            self._configure_mp3_render(output_dir)
            # 先改 RENDER_PATTERN 为 before_label
            api.GetSetProjectInfo_String(
                0, "RENDER_PATTERN", before_label, True,
            )
            ok_before = self._trigger_render(before_path)
            if not ok_before:
                # 恢复
                self._set_time_selection(orig_start, orig_end)
                return PreviewResult(
                    success=False,
                    error="A/B 对比失败：before 渲染未生成",
                )

            # ── After 渲染 ──
            # 注意：after 渲染在调用方应用调整后执行
            # 此方法仅渲染当前状态为 after
            api.GetSetProjectInfo_String(
                0, "RENDER_PATTERN", after_label, True,
            )
            ok_after = self._trigger_render(after_path)

            # 恢复原始时间选区
            self._set_time_selection(orig_start, orig_end)

            if ok_after:
                log.info(
                    "A/B 对比完成: before=%s, after=%s",
                    before_path, after_path,
                )
                return PreviewResult(
                    success=True,
                    before_path=before_path,
                    after_path=after_path,
                    format="mp3",
                    bitrate_kbps=128,
                    duration_sec=duration_sec,
                )
            else:
                return PreviewResult(
                    success=True,
                    before_path=before_path,
                    after_path=None,
                    format="mp3",
                    bitrate_kbps=128,
                    duration_sec=duration_sec,
                    error="After 渲染失败",
                )

        except Exception as exc:
            log.exception("A/B 对比渲染失败: %s", exc)
            return PreviewResult(
                success=False,
                error=str(exc),
            )
