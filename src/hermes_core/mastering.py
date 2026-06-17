"""
母带处理引擎 — 二分查找 LUFS + 限制器校准。

提供 MasteringEngine 类（封装 Pro-L 2 限制器 + LUFS 目标搜索）
和流派目标 LUFS 查询、错误/提示等模块级函数。
"""

import logging
import os
import tempfile
from typing import Callable

from hermes_core.genre_tables import (
    _GENRE_TARGET_LUFS,
    _DEFAULT_TARGET_LUFS,
    _PRO_L2_RANGE_DB,
)
from hermes_core.loudness_optimizer import (
    find_optimal_gain,
    verify_output,
    load_calibration,
    generate_report,
)
from hermes_core.signal import SignalAnalyzer

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# 模块级函数
# ════════════════════════════════════════════════════════════════


def _get_genre_target_lufs(genre: str) -> float:
    """返回 *genre* 的推荐目标 LUFS。"""
    return _GENRE_TARGET_LUFS.get(genre, _DEFAULT_TARGET_LUFS)


def _master_error(target_lufs: float, ceiling_db: float, error: str) -> dict:
    """构建 finalize_master 错误结果字典。"""
    return {
        "target_lufs": target_lufs,
        "achieved_lufs": None,
        "probe_lufs": None,
        "gain_db": 0.0,
        "ceiling_db": ceiling_db,
        "passed": False,
        "converged": False,
        "error": error,
        "hint": _friendly_hint(error),
        "output_path": None,
        "pre_limiter_peak_db": None,
    }


def _friendly_hint(error: str) -> str:
    """返回常见错误的用户友好提示。"""
    hints = {
        "Probe render failed":
            "REAPER may be blocked by a modal dialog. Try watchdog=True "
            "to auto-dismiss dialogs, or check that tracks have media items.",
        "Probe is near-silent":
            "The probe render produced near-silent audio. Check that "
            "the source files are not empty and have audible content.",
        "Pro-L 2 Output Level param not found":
            "Pro-L 2 parameter name doesn't match. Verify the plugin is "
            "installed and named exactly 'VST: FabFilter Pro-L 2 (FabFilter)'. "
            "Try running preflight_plugins() first.",
        "Pro-L 2 Gain param not found":
            "Pro-L 2 Gain parameter not found. Same as above — check "
            "plugin installation and name.",
        "Failed to add":
            "Plugin not found in REAPER. Check the FX name matches "
            "the REAPER FX browser exactly, including vendor suffix.",
        "Not a WAV file":
            "Input file is not a valid WAV. Supported formats: WAV "
            "(16/24-bit PCM, 32-bit float), FLAC, MP3 via soundfile.",
        "WAV data chunk not found":
            "WAV file appears corrupted — data chunk is missing. "
            "Try re-exporting the file from your DAW.",
    }
    for key, hint in hints.items():
        if key.lower() in error.lower():
            return hint
    return (
        "Check the log for details. Common issues: missing plugins, "
        "unwritable output directory, insufficient disk space, or REAPER "
        "modal dialogs blocking automation."
    )


# ════════════════════════════════════════════════════════════════
# MasteringEngine
# ════════════════════════════════════════════════════════════════


class MasteringEngine:
    """母带处理引擎 — 二分查找 LUFS + 限制器校准。

    对 master 轨道应用 brickwall 限制器，通过探测渲染 + 二分查找
    确定最佳 Gain 以达到目标 integrated LUFS。

    Parameters
    ----------
    bridge : ReaperBridge
        REAPER 桥接实例。
    fx_manager : FxManager
        FX 管理器（用于添加/设置 master 轨道插件参数）。
    render_manager : RenderManager
        渲染管理器（用于探测和最终渲染）。
    on_progress : Callable or None
        可选的进度回调 ``(stage: str, pct: float)``。
    """

    def __init__(self, bridge, fx_manager, render_manager, on_progress=None):
        self._bridge = bridge
        self._fx = fx_manager
        self._render = render_manager
        self._on_progress = on_progress

    def finalize(
        self,
        target_lufs: float = _DEFAULT_TARGET_LUFS,
        *,
        limiter_fx: str = "FabFilter Pro-L 2 (FabFilter)",
        ceiling_db: float = -0.5,
        tolerance: float = 0.3,
        tmp_dir: str | None = None,
    ) -> dict:
        """两阶段母带最终处理：限制器模拟 + 二分查找 LUFS 目标。

        1. 将 *limiter_fx* 添加到 master 轨道，Gain=0，Output Level=*ceiling_db*。
        2. 探测渲染 → brickwall 模拟 + 二分查找 → 最佳 Gain。
        3. 应用 Gain，渲染最终文件。
        4. 验证最终 LUFS 是否达标。

        二分查找直接考虑限制器非线性，因此无需开环公式。

        Returns
        -------
        dict
            包含 target_lufs, achieved_lufs, probe_lufs, gain_db,
            ceiling_db, passed, converged, output_path, pre_limiter_peak_db
            等键的字典。出错时还包含 error 和 hint。
        """
        def _progress(stage: str, pct: float):
            if self._on_progress:
                self._on_progress(stage, pct)

        _progress("setup", 0.0)
        tmp = tmp_dir or tempfile.mkdtemp(prefix="hermes_master_")
        probe_dir = os.path.join(tmp, "probe")
        final_dir = os.path.join(tmp, "final")

        # 1. 添加限制器
        fx_idx = self._fx.add_master(limiter_fx)
        if fx_idx < 0:
            return _master_error(
                target_lufs, ceiling_db,
                f"Failed to add {limiter_fx} to master",
            )

        # Pro-L 2 参数公式（2026-05-28 通过 REAPER 校准验证）：
        #   Gain: 0..+30 dB → normalized = gain_db / 30
        #   Output Level: -30..0 dB → normalized = (ceiling_db + 30) / 30
        ceiling_norm = max(0.0, min(1.0, (ceiling_db + _PRO_L2_RANGE_DB) / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Output Level", ceiling_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Output Level param not found — may need calibration",
            )
        if not self._fx.set_param(-1, fx_idx, "Gain", 0.0):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found — may need calibration",
            )

        # Pro-L 2 Style — 流派差异化（设计文档 §五）
        # Transparent=0.0 / Allround=0.35 / Aggressive=0.70
        try:
            from hermes_core.genre_tables import _GENRE_PROL2_STYLE
            genre = getattr(self, "_finalize_genre", "pop")
            style_name = _GENRE_PROL2_STYLE.get(genre, "Allround")
            _STYLE_NORM: dict[str, float] = {
                "Transparent": 0.0, "Allround": 0.35, "Aggressive": 0.70,
            }
            style_val = _STYLE_NORM.get(style_name, 0.35)
            self._fx.set_param(-1, fx_idx, "Style", style_val)
            log.info("Pro-L 2: ceiling=%.1fdBTP style=%s(%.2f) genre=%s",
                     ceiling_db, style_name, style_val, genre)
        except Exception:
            log.debug("Pro-L 2 Style not applied — param may have moved")

        # 2. 探测渲染
        _progress("probe_render", 0.15)
        probe_result = self._render.render_mix(probe_dir)
        if probe_result.get("output_path") is None:
            return _master_error(
                target_lufs, ceiling_db, "Probe render failed",
            )

        # 探测后分析 — 获取 pre_peak 用于诊断
        pre_peak = 0.0
        try:
            report = SignalAnalyzer.analyze(probe_result["output_path"])
            pre_peak = report.peak_db
        except (OSError, ValueError, RuntimeError):
            pass

        # 3. Hard-clip 模型 + 二分查找 → 最佳 Gain
        _progress("search", 0.35)
        probe_path = probe_result.get("output_path")
        cal = load_calibration()
        search = find_optimal_gain(
            probe_path,
            target_lufs=target_lufs,
            ceiling_dbtp=ceiling_db,
            tolerance=tolerance,
            calibration_offset=cal,
        )
        if not search.converged and search.probe_lufs <= -70:
            return _master_error(
                target_lufs, ceiling_db, "Probe is near-silent",
            )

        gain_db = search.gain_db

        # 4. 应用 Gain 并渲染最终文件
        _progress("final_render", 0.65)
        gain_norm = max(0.0, min(1.0, gain_db / _PRO_L2_RANGE_DB))
        if not self._fx.set_param(-1, fx_idx, "Gain", gain_norm):
            return _master_error(
                target_lufs, ceiling_db,
                "Pro-L 2 Gain param not found during final render",
            )
        final_result = self._render.render_mix(final_dir)
        output_path = final_result.get("output_path")

        # 5. 验证
        _progress("verify", 0.90)
        achieved_lufs = None
        passed = output_path is not None
        if output_path:
            verify = verify_output(output_path, target_lufs=target_lufs)
            achieved_lufs = verify.actual_lufs
            passed = verify.passed

        log.info(
            "Master report:\n%s",
            generate_report(search, verify if output_path else None),
        )

        return {
            "target_lufs": target_lufs,
            "achieved_lufs": achieved_lufs,
            "probe_lufs": search.probe_lufs,
            "gain_db": gain_db,
            "ceiling_db": ceiling_db,
            "passed": passed,
            "converged": search.converged,
            "pre_limiter_peak_db": pre_peak,
            "output_path": output_path,
        }
