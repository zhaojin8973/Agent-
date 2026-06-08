"""
人声–伴奏交叉频谱分析。

比较两轨的频谱报告，检测频率掩蔽区，
输出伴奏避让建议和人声参考调整因子。
"""

from dataclasses import dataclass
import logging

import numpy as np

from hermes_core.spectrum import SpectrumReport

log = logging.getLogger(__name__)


# ── 掩蔽阈值 ──────────────────────────────────────────────

_FUNDAMENTAL_MASKING_THRESHOLD_DB = 3.0   # 基频区：伴奏超过人声 3dB 触发
_PRESENCE_MASKING_THRESHOLD_DB = 2.0      # 存在感区：2dB 触发
_MAX_BACKING_CUT_DB = 3.0                 # 伴奏单频段最大衰减
_MIN_BACKING_CUT_DB = 0.5                 # 低于此值不触发


# ── 数据结构 ──────────────────────────────────────────────


@dataclass
class MaskingZone:
    """伴奏掩蔽人声的频段。"""
    freq_hz: float
    masking_ratio_db: float          # 伴奏超过人声多少 dB
    cut_db: float                    # 建议的伴奏衰减量
    q: float                         # 建议的 Q 值
    zone_type: str                   # "fundamental" | "presence"


@dataclass
class CrossSpectrumReport:
    """交叉频谱分析结果。"""

    masking_zones: list[MaskingZone]
    """伴奏需要避让的频段列表。"""

    vocal_presence_factor: float
    """人声存在感增益倍率 (0.5–1.15)。<1 = 伴奏抢占，少补；>1 = 伴奏空，可多补。"""

    vocal_air_factor: float
    """人声空气感增益倍率。"""

    vocal_hpf_offset_hz: float
    """人声 HPF 偏移 (Hz)。伴奏低频重时略提高。"""

    vocal_thin_boost_blocked: bool
    """人声 thin boost 是否被阻挡（伴奏低频重时阻止补低频）。"""


# ── 分析器 ────────────────────────────────────────────────


class CrossSpectrumAnalyzer:
    """比较人声和伴奏频谱，生成双向调整建议。"""

    @staticmethod
    def analyze(
        vocal_report: SpectrumReport,
        backing_report: SpectrumReport,
        vocal_profile: object | None = None,
    ) -> CrossSpectrumReport:
        """分析人声和伴奏频谱的互相影响。

        返回包含伴奏避让频段和人声参考系数的 :class:`CrossSpectrumReport`。
        """
        # ── 读取声纹参数 ──
        vp = vocal_profile
        fund_lo = getattr(vp, "fundamental_lo", 85.0) if vp else 85.0
        fund_hi = getattr(vp, "fundamental_hi", 255.0) if vp else 255.0
        pres_lo = getattr(vp, "presence_scan_lo", 3000.0) if vp else 3000.0
        pres_hi = getattr(vp, "presence_scan_hi", 6000.0) if vp else 6000.0

        vocal_bands = vocal_report.band_energy_db
        backing_bands = backing_report.band_energy_db

        masking_zones: list[MaskingZone] = []

        # ── 基频区掩蔽检测 ──
        # 比较 low + low_mid 区域（人声基频所在）
        vocal_fund_energy = max(
            vocal_bands.get("low", -60.0),
            vocal_bands.get("low_mid", -60.0),
        )
        backing_fund_energy = max(
            backing_bands.get("low", -60.0),
            backing_bands.get("low_mid", -60.0),
        )
        fund_masking = backing_fund_energy - vocal_fund_energy

        if fund_masking > _FUNDAMENTAL_MASKING_THRESHOLD_DB:
            cut_db = min(fund_masking * 0.4, _MAX_BACKING_CUT_DB)
            if cut_db >= _MIN_BACKING_CUT_DB:
                # 频点用人声基频中心
                freq = (fund_lo + fund_hi) / 2.0
                masking_zones.append(MaskingZone(
                    freq_hz=freq,
                    masking_ratio_db=round(fund_masking, 1),
                    cut_db=round(cut_db, 1),
                    q=4.0,  # 窄 Q — 只切掩蔽频率，保留两侧包裹感
                    zone_type="fundamental",
                ))

        # ── 存在感区掩蔽检测 ──
        vocal_pres_energy = vocal_bands.get("high_mid", -60.0)
        backing_pres_energy = backing_bands.get("high_mid", -60.0)
        pres_masking = backing_pres_energy - vocal_pres_energy

        if pres_masking > _PRESENCE_MASKING_THRESHOLD_DB:
            cut_db = min(pres_masking * 0.3, 2.0)
            if cut_db >= _MIN_BACKING_CUT_DB:
                # 频点用人声存在感缺口频率
                pres_freq = vocal_report.presence_gap_hz or 4000.0
                masking_zones.append(MaskingZone(
                    freq_hz=pres_freq,
                    masking_ratio_db=round(pres_masking, 1),
                    cut_db=round(cut_db, 1),
                    q=5.0,  # 窄 Q — 存在感区精准避让
                    zone_type="presence",
                ))

        # ── 人声参考调整因子 ──────────────────────────────

        # 存在感竞争度
        pres_competition = backing_pres_energy - vocal_pres_energy
        if pres_competition > 3.0:
            vocal_presence_factor = 0.5   # 伴奏抢占，人声少补
        elif pres_competition < -6.0:
            vocal_presence_factor = 1.15  # 伴奏空，人声多占
        else:
            vocal_presence_factor = 1.0

        # 空气感竞争度
        vocal_air = vocal_bands.get("air", -60.0)
        backing_air = backing_bands.get("air", -60.0)
        air_competition = backing_air - vocal_air
        if air_competition > 0:
            vocal_air_factor = 0.5   # 伴奏 air 区强 → 跳过或减量
        elif air_competition < -10.0:
            vocal_air_factor = 1.2   # 伴奏 air 空白 → 更积极
        else:
            vocal_air_factor = 1.0

        # HPF 偏移：伴奏低频重时略提高人声低切
        backing_low = backing_bands.get("low", -60.0)
        vocal_low = vocal_bands.get("low", -60.0)
        if backing_low > vocal_low + 6.0:
            vocal_hpf_offset_hz = 10.0
        else:
            vocal_hpf_offset_hz = 0.0

        # 低频掩蔽阻挡 thin boost
        vocal_thin_blocked = fund_masking > 4.0

        report = CrossSpectrumReport(
            masking_zones=masking_zones,
            vocal_presence_factor=round(vocal_presence_factor, 2),
            vocal_air_factor=round(vocal_air_factor, 2),
            vocal_hpf_offset_hz=vocal_hpf_offset_hz,
            vocal_thin_boost_blocked=vocal_thin_blocked,
        )

        log.info(
            "Cross-spectrum: fund_masking=%.1fdB pres_masking=%.1fdB "
            "→ %d masking zones, presence_factor=%.2f air_factor=%.2f",
            fund_masking, pres_masking,
            len(masking_zones),
            vocal_presence_factor, vocal_air_factor,
        )

        return report
