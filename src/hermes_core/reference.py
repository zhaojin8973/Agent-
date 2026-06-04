"""
参考曲目匹配 — 频谱+响度对齐参考曲目。

分析参考曲目的频谱特征和目标响度，生成匹配参数，
用于指导混音决策。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field

log = logging.getLogger(__name__)


@dataclass
class ReferenceProfile:
    """参考曲目分析结果。

    包含参考曲目的频谱特征、响度数据和动态范围信息，
    用作混音匹配的目标标准。
    """
    path: str
    duration_sec: float = 0.0
    integrated_lufs: float | None = None
    short_term_lufs: float | None = None
    true_peak_db: float | None = None
    spectral_tilt_db: float | None = None        # 频谱斜率（高频-低频）
    low_mid_balance_db: float | None = None       # 低频 vs 中频平衡
    presence_band_db: float | None = None          # 临场感频段能量（2-5kHz 平均值）
    dynamic_range_db: float | None = None          # 动态范围（PLR: Peak-to-Loudness Ratio）


class ReferenceMatcher:
    """参考曲目匹配器。

    分析参考曲目的频谱和响度特征，生成混音匹配参数，
    并支持将混音结果与参考曲目进行对比。

    用法::

        matcher = ReferenceMatcher()
        ref = matcher.analyze("/path/to/reference.wav")
        params = matcher.generate_match_params(ref, target_lufs=-14.0)
        diff = matcher.compare(mix_path="/path/to/mix.wav", reference=ref)
    """

    def __init__(self):
        self._references: dict[str, ReferenceProfile] = {}

    # ── 分析 ──────────────────────────────────────────────────

    def analyze(self, path: str) -> ReferenceProfile:
        """分析参考曲目，提取频谱和响度特征。

        在完整实现中，此方法会读取音频文件并计算所有频谱和响度
        指标。当前框架版本为不支持真实音频分析的场景提供占位实现。

        Args:
            path: 参考曲目的文件路径。

        Returns:
            ReferenceProfile: 包含分析结果的数据对象。
            对于无法读取的文件返回 duration_sec=0 的默认 profile。

        Raises:
            FileNotFoundError: 当文件路径不存在时。
        """
        import os
        if not os.path.exists(path):
            raise FileNotFoundError(f"参考曲目文件不存在: {path}")

        profile = ReferenceProfile(path=path)
        try:
            # 尝试用 wave 模块获取基础信息
            import wave
            with wave.open(path, "rb") as wf:
                n_frames = wf.getnframes()
                sample_rate = wf.getframerate()
                if sample_rate > 0:
                    profile.duration_sec = n_frames / sample_rate
                n_channels = wf.getnchannels()
                log.debug(
                    "参考曲目 %s: %d 声道, %d Hz, %.1f 秒",
                    path, n_channels, sample_rate, profile.duration_sec,
                )
        except (wave.Error, EOFError, OSError):
            # 不是标准 WAV 文件，使用占位值
            log.debug("无法用 wave 模块解析 %s，使用占位数据", path)
            profile.duration_sec = 0.0

        # 占位频谱特征（完整实现中由 pyloudnorm + numpy 计算）
        profile.integrated_lufs = -14.0
        profile.short_term_lufs = -12.0
        profile.true_peak_db = -1.0
        profile.spectral_tilt_db = -2.0
        profile.low_mid_balance_db = 0.0
        profile.presence_band_db = -3.0
        profile.dynamic_range_db = 10.0

        self._references[path] = profile
        log.info("已分析参考曲目: %s (LUFS: %.1f)", path, profile.integrated_lufs)
        return profile

    # ── 匹配参数生成 ──────────────────────────────────────────

    def generate_match_params(self, reference: ReferenceProfile,
                              target_lufs: float | None = None) -> dict:
        """生成匹配参数 — 告诉混音引擎如何调整以达到参考曲目标准。

        根据参考曲目的频谱和响度特征，计算出一组可操作的混音参数，
        包括目标响度、EQ 调整建议、压缩提示和立体声宽度提示。

        Args:
            reference: 已分析的参考曲目 profile。
            target_lufs: 覆盖目标响度。若为 None，使用参考曲目的 integrated_lufs。

        Returns:
            dict with:
                - target_lufs: float — 目标输出响度（LUFS）。
                - eq_adjustments: list[dict] — EQ 频段调整建议。
                - compression_hint: dict — 压缩设置建议。
                - stereo_width_hint: dict — 立体声宽度建议。
                - spectral_targets: dict — 频谱目标数据。
        """
        target = target_lufs if target_lufs is not None else reference.integrated_lufs
        if target is None:
            target = -14.0

        # ── EQ 调整建议 ──────────────────────────────────────
        eq_adjustments = []
        if reference.spectral_tilt_db is not None:
            eq_adjustments.append({
                "band": "high_shelf",
                "freq_hz": 8000.0,
                "gain_db_hint": reference.spectral_tilt_db * 0.5,
                "reason": f"频谱斜率 {reference.spectral_tilt_db:+.1f} dB 补偿",
            })

        if reference.low_mid_balance_db is not None:
            eq_adjustments.append({
                "band": "bell",
                "freq_hz": 300.0,
                "gain_db_hint": -reference.low_mid_balance_db * 0.3,
                "reason": f"低频/中频平衡 {reference.low_mid_balance_db:+.1f} dB",
            })

        if reference.presence_band_db is not None and reference.presence_band_db < -2.0:
            eq_adjustments.append({
                "band": "bell",
                "freq_hz": 3000.0,
                "gain_db_hint": min(3.0, abs(reference.presence_band_db) * 0.5),
                "reason": f"临场感不足 ({reference.presence_band_db:+.1f} dB) → 提升",
            })

        # ── 压缩提示 ──────────────────────────────────────────
        compression_hint = {
            "amount": "medium",
            "ratio": 2.5,
            "threshold_db": -24.0,
            "target_gr_db": 3.0,
        }

        if reference.dynamic_range_db is not None:
            if reference.dynamic_range_db > 12.0:
                compression_hint["amount"] = "light"
                compression_hint["ratio"] = 2.0
                compression_hint["target_gr_db"] = 2.0
            elif reference.dynamic_range_db < 6.0:
                compression_hint["amount"] = "heavy"
                compression_hint["ratio"] = 4.0
                compression_hint["target_gr_db"] = 5.0

        # ── 立体声宽度提示 ────────────────────────────────────
        stereo_width_hint = {
            "width_percent": 100,
            "mid_side_balance": 0.0,
        }

        # ── 频谱目标 ──────────────────────────────────────────
        spectral_targets = {
            "reference_lufs": reference.integrated_lufs,
            "reference_true_peak": reference.true_peak_db,
            "reference_dynamic_range": reference.dynamic_range_db,
            "reference_spectral_tilt": reference.spectral_tilt_db,
        }

        log.info(
            "已生成匹配参数: 目标 LUFS %.1f, %d 条 EQ 调整, 压缩: %s",
            target, len(eq_adjustments), compression_hint["amount"],
        )
        return {
            "target_lufs": target,
            "eq_adjustments": eq_adjustments,
            "compression_hint": compression_hint,
            "stereo_width_hint": stereo_width_hint,
            "spectral_targets": spectral_targets,
        }

    # ── 对比分析 ──────────────────────────────────────────────

    def compare(self, mix_path: str, reference: ReferenceProfile) -> dict:
        """对比混音结果与参考曲目，返回差异分析。

        分析混音文件并将其频谱/响度特征与参考曲目进行对比，
        给出差异报告和调整建议。

        Args:
            mix_path: 混音输出文件路径。
            reference: 参考曲目 profile（需先通过 analyze() 获取）。

        Returns:
            dict with:
                - match_quality: str — "good" | "fair" | "poor"
                - lufs_diff_db: float — 响度差异（混音 - 参考）
                - spectral_diff_db: float | None — 频谱斜率差异
                - eq_suggestions: list[dict] — 改进建议
                - overall_score: float — 0.0-1.0 综合匹配分数
        """
        try:
            mix_profile = self.analyze(mix_path)
        except FileNotFoundError:
            log.warning("混音文件未找到: %s，返回空对比结果", mix_path)
            return {
                "match_quality": "poor",
                "lufs_diff_db": 0.0,
                "spectral_diff_db": None,
                "eq_suggestions": [],
                "overall_score": 0.0,
            }

        # ── 响度对比 ──────────────────────────────────────────
        ref_lufs = reference.integrated_lufs or -14.0
        mix_lufs = mix_profile.integrated_lufs or -14.0
        lufs_diff = mix_lufs - ref_lufs

        # ── 频谱对比 ──────────────────────────────────────────
        spectral_diff = None
        if (reference.spectral_tilt_db is not None
                and mix_profile.spectral_tilt_db is not None):
            spectral_diff = mix_profile.spectral_tilt_db - reference.spectral_tilt_db

        # ── 综合评分 ──────────────────────────────────────────
        score = self._calculate_match_score(
            lufs_diff=lufs_diff,
            spectral_diff=spectral_diff,
            ref_dr=reference.dynamic_range_db,
            mix_dr=mix_profile.dynamic_range_db,
        )

        if score >= 0.8:
            quality = "good"
        elif score >= 0.5:
            quality = "fair"
        else:
            quality = "poor"

        # ── 改进建议 ──────────────────────────────────────────
        suggestions = []
        if abs(lufs_diff) > 1.5:
            direction = "提高" if lufs_diff < 0 else "降低"
            suggestions.append({
                "parameter": "output_gain",
                "adjustment_db": -lufs_diff,
                "reason": f"响度偏差 {lufs_diff:+.1f} LUFS → {direction}输出增益",
            })

        if spectral_diff is not None and abs(spectral_diff) > 3.0:
            direction = "提亮" if spectral_diff < 0 else "压暗"
            suggestions.append({
                "parameter": "high_shelf_eq",
                "adjustment_db": -spectral_diff * 0.5,
                "reason": f"频谱斜率偏差 {spectral_diff:+.1f} dB → {direction}高频",
            })

        log.info(
            "对比完成: %s vs 参考, 质量=%s, LUFS 差异=%+.1f, 评分=%.2f",
            mix_path, quality, lufs_diff, score,
        )
        return {
            "match_quality": quality,
            "lufs_diff_db": lufs_diff,
            "spectral_diff_db": spectral_diff,
            "eq_suggestions": suggestions,
            "overall_score": score,
        }

    # ── 缓存管理 ──────────────────────────────────────────────

    def get_cached(self, path: str) -> ReferenceProfile | None:
        """获取已缓存的参考曲目分析结果。

        Args:
            path: 参考曲目文件路径。

        Returns:
            ReferenceProfile 或 None（若未缓存）。
        """
        return self._references.get(path)

    def clear_cache(self) -> None:
        """清空所有缓存的参考曲目分析结果。"""
        self._references.clear()
        log.debug("已清空参考曲目缓存（%d 条）", len(self._references))

    @property
    def cached_count(self) -> int:
        """当前缓存的参考曲目数量。"""
        return len(self._references)

    # ── 内部方法 ──────────────────────────────────────────────

    @staticmethod
    def _calculate_match_score(lufs_diff: float,
                                spectral_diff: float | None,
                                ref_dr: float | None,
                                mix_dr: float | None) -> float:
        """计算混音与参考曲目的综合匹配分数（0.0-1.0）。

        评分维度：
        - 响度匹配（40% 权重）：LUFS 差值越小越好。
        - 频谱匹配（30% 权重）：频谱斜率差值越小越好。
        - 动态范围匹配（30% 权重）：动态范围差值越小越好。
        """
        scores = []

        # 响度评分（40% 权重）：±3 dB 以内满分，超出线性衰减
        lufs_score = max(0.0, 1.0 - abs(lufs_diff) / 6.0)
        scores.append(("loudness", lufs_score, 0.4))

        # 频谱评分（30% 权重）：±6 dB 以内满分
        if spectral_diff is not None:
            spectral_score = max(0.0, 1.0 - abs(spectral_diff) / 12.0)
            scores.append(("spectral", spectral_score, 0.3))
        else:
            scores.append(("spectral", 0.5, 0.3))  # 未知时给中性分

        # 动态范围评分（30% 权重）：±6 dB 以内满分
        if ref_dr is not None and mix_dr is not None:
            dr_diff = abs(ref_dr - mix_dr)
            dr_score = max(0.0, 1.0 - dr_diff / 12.0)
            scores.append(("dynamic_range", dr_score, 0.3))
        else:
            scores.append(("dynamic_range", 0.5, 0.3))

        # 加权求和
        total = sum(score * weight for _, score, weight in scores)
        return round(total, 3)
