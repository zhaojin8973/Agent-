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

        使用 pyloudnorm 计算集成/短时 LUFS，numpy FFT 分析频谱倾斜、
        低频平衡和临场感频段，scipy 4x 过采样估算真峰值。

        Args:
            path: 参考曲目的文件路径（WAV/FLAC/MP3）。

        Returns:
            ReferenceProfile: 包含完整频谱和响度分析的数据对象。

        Raises:
            FileNotFoundError: 当文件路径不存在时。
        """
        import os
        if not os.path.exists(path):
            raise FileNotFoundError(f"参考曲目文件不存在: {path}")

        profile = ReferenceProfile(path=path)

        # 尝试读取音频数据
        try:
            import soundfile as sf
            data, sample_rate = sf.read(path, dtype="float64")
            if data.ndim == 1:
                data = data.reshape(-1, 1)
            n_channels = data.shape[1]
            profile.duration_sec = len(data) / sample_rate
            log.debug("参考曲目 %s: %d声道 %dHz %.1fs", path, n_channels, sample_rate, profile.duration_sec)
        except Exception as exc:
            log.warning("无法读取音频文件 %s: %s，使用占位数据", path, exc)
            profile.duration_sec = 0.0
            self._fill_placeholder(profile, path)
            return profile

        # ── 响度分析（pyloudnorm）───────────────────────────
        try:
            import pyloudnorm as pyln
            import numpy as np

            # 下混到单声道用于 LUFS 测量
            mono = np.mean(data, axis=1)

            # 集成 LUFS
            meter = pyln.Meter(sample_rate)
            profile.integrated_lufs = float(meter.integrated_loudness(mono))

            # 短时 LUFS（取最后 3 秒，模拟曲尾响度）
            st_window = int(sample_rate * 3.0)
            if len(mono) > st_window:
                st = mono[-st_window:]
                profile.short_term_lufs = float(meter.integrated_loudness(st))
            else:
                profile.short_term_lufs = profile.integrated_lufs
        except Exception as exc:
            log.warning("LUFS 分析失败: %s", exc)
            profile.integrated_lufs = -14.0
            profile.short_term_lufs = -12.0

        # ── 真峰值（scipy 4x 过采样）─────────────────────────
        try:
            from scipy import signal
            peak_linear = 0.0
            for ch in range(n_channels):
                ch_data = data[:, ch]
                upsampled = signal.resample_poly(ch_data, 4, 1)
                ch_peak = np.max(np.abs(upsampled))
                peak_linear = max(peak_linear, ch_peak)
            profile.true_peak_db = float(20.0 * np.log10(max(peak_linear, 1e-12)))
        except Exception:
            # scipy 不可用，回退到采样峰值
            profile.true_peak_db = float(20.0 * np.log10(max(np.max(np.abs(data)), 1e-12)))

        # ── 频谱分析（numpy FFT）────────────────────────────
        try:
            # 对每个声道做 FFT，取平均幅度谱
            n_fft = 8192
            hop = n_fft // 2
            n_frames = (len(data) - n_fft) // hop + 1
            if n_frames < 1:
                n_frames = 1

            avg_mag = np.zeros(n_fft // 2 + 1)
            for ch in range(n_channels):
                ch_data = data[:, ch]
                for f in range(min(n_frames, 50)):  # 采样前 50 帧
                    start = f * hop
                    frame = ch_data[start:start + n_fft] * np.hanning(n_fft)
                    mag = np.abs(np.fft.rfft(frame))
                    avg_mag += mag
            avg_mag /= max(n_channels * min(n_frames, 50), 1)

            freqs = np.fft.rfftfreq(n_fft, 1.0 / sample_rate)
            mag_db = 20.0 * np.log10(np.maximum(avg_mag, 1e-12))

            # 频谱倾斜: 100Hz-10kHz 范围内 dB/octave 线性拟合
            tilt_mask = (freqs >= 100) & (freqs <= 10000)
            if np.sum(tilt_mask) > 10:
                log_freqs = np.log2(np.maximum(freqs[tilt_mask], 1.0))
                tilt_db = mag_db[tilt_mask]
                slope, _ = np.polyfit(log_freqs, tilt_db, 1)
                profile.spectral_tilt_db = float(slope)
            else:
                profile.spectral_tilt_db = -2.0

            # 低频/中频平衡: 100-500Hz vs 500-2000Hz
            low_mask = (freqs >= 100) & (freqs < 500)
            mid_mask = (freqs >= 500) & (freqs < 2000)
            low_db = float(np.mean(mag_db[low_mask])) if np.any(low_mask) else -40.0
            mid_db = float(np.mean(mag_db[mid_mask])) if np.any(mid_mask) else -40.0
            profile.low_mid_balance_db = float(round(low_db - mid_db, 1))

            # 临场感频段: 2kHz-5kHz 平均值相对于全频段均值的偏差
            presence_mask = (freqs >= 2000) & (freqs <= 5000)
            all_mask = (freqs >= 100) & (freqs <= 15000)
            presence_db = float(np.mean(mag_db[presence_mask])) if np.any(presence_mask) else -40.0
            all_db = float(np.mean(mag_db[all_mask])) if np.any(all_mask) else -40.0
            profile.presence_band_db = float(round(presence_db - all_db, 1))
        except Exception as exc:
            log.warning("频谱分析失败: %s", exc)
            profile.spectral_tilt_db = -2.0
            profile.low_mid_balance_db = 0.0
            profile.presence_band_db = -3.0

        # ── 动态范围（PLR: Peak-to-Loudness Ratio）───────────
        if profile.integrated_lufs is not None:
            peak_db = float(20.0 * np.log10(max(np.max(np.abs(data)), 1e-12)))
            profile.dynamic_range_db = float(round(peak_db - profile.integrated_lufs, 1))
        else:
            profile.dynamic_range_db = 10.0

        self._references[path] = profile
        log.info(
            "已分析参考曲目: %s LUFS=%.1f Tilt=%.1fdB DR=%.1fdB",
            path, profile.integrated_lufs, profile.spectral_tilt_db, profile.dynamic_range_db,
        )
        return profile

    def _fill_placeholder(self, profile: ReferenceProfile, path: str) -> None:
        """无法读取音频文件时填充占位值。"""
        profile.integrated_lufs = -14.0
        profile.short_term_lufs = -12.0
        profile.true_peak_db = -1.0
        profile.spectral_tilt_db = -2.0
        profile.low_mid_balance_db = 0.0
        profile.presence_band_db = -3.0
        profile.dynamic_range_db = 10.0
        self._references[path] = profile

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
