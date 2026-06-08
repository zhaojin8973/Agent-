"""
频谱分析 — librosa STFT + scipy 峰值检测。

用 librosa（业界标准音频分析库）替换手写 numpy FFT，
scipy.signal.find_peaks 替换手动峰值检测，
全帧均值替换 P90 百分位聚合以得到更稳定的能量读数。

A-weighting（IEC 61672-1:2013）用于感知响度加权。
"""

import math
from dataclasses import dataclass

import librosa
import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import find_peaks

from hermes_core.audio_utils import read_pcm, to_mono

# ── Constants ────────────────────────────────────────────────────

_EPS_DB = 1e-10

# STFT
_N_FFT = 2048
_HOP_LENGTH = 512

# Resonance detection
_MIN_PROMINENCE_DB = 6.0     # 峰必须高于平滑曲线至少 6 dB
_MIN_Q_FACTOR = 8.0          # Q > 8 → 房间共振（放宽于原来的 15）
_HARMONIC_TOLERANCE = 0.05   # ±5 % 整数倍 → 谐波候选
_SMOOTHING_WINDOW = 20       # 平滑窗口（bins）
_PEAK_DISTANCE = 5           # 峰值最小间距（bins）

# 基频搜索范围
_F0_MIN_HZ = 80.0
_F0_MAX_HZ = 400.0


# ── Frequency band definitions ───────────────────────────────────

_BAND_EDGES: dict[str, tuple[float, float]] = {
    "sub":       (20.0,   80.0),
    "low":       (80.0,   250.0),
    "low_mid":   (250.0,  500.0),
    "mid":       (500.0,  2000.0),
    "high_mid":  (2000.0, 5000.0),
    "presence":  (5000.0, 8000.0),
    "air":       (8000.0, 20000.0),
}


# ── Data classes ──────────────────────────────────────────────────


@dataclass
class Resonance:
    """检测到的频谱峰，可能是房间模式。

    Attributes:
        freq_hz:        中心频率 (Hz)。
        prominence_db:  峰高于局部平滑曲线的幅度 (dB)。
        q_factor:       中心频率 / −3 dB 带宽。
                        值 > 8 可能是房间共振而非音乐泛音。
        is_harmonic:    ``True`` 当频率在人声基频的整数倍 ±5% 内。
    """
    freq_hz: float
    prominence_db: float
    q_factor: float
    is_harmonic: bool


@dataclass
class SpectrumReport:
    """单个音频文件的聚合频谱指标。

    所有能量均为 **A-weighted**（感知响度），
    从 RMS 门控均值 STFT 导出（排除静音帧）。
    """
    band_energy_db: dict[str, float]
    """A-weighted 平均能量（每频段, dB）。"""

    spectral_tilt_db_per_octave: float
    """log(freq)-vs-A-weighted-dB 线性回归斜率。负值 = 偏暗。"""

    resonances: list[Resonance]
    """检测到的窄带峰，按 prominence 降序排列。"""

    mud_ratio_db: float
    """low_mid 比 mid 高多少 dB（> 3 → 泥巴）。"""

    presence_deficit_db: float
    """presence 比 mid 低多少 dB（> 0 → 偏暗）。"""

    sibilance_peak_hz: float
    """4–12 kHz 范围内最高能量频率（齿音检测频段）。"""

    air_level_db: float
    """air 频段的平均 A-weighted 能量 (dB)。"""

    presence_gap_hz: float = 3000.0
    """2k–8kHz 内实际频谱 vs 理想平滑过渡差最大的频率 (Hz)。"""

    mud_peak_hz: float = 350.0
    """200–500Hz 范围内能量最高的频率（泥巴中心 Hz）。"""

    air_rolloff_hz: float = 8000.0
    """高频滚降加速开始的频率（空气搁架起点 Hz）。"""

    hpf_hz: float = 0.0
    """40–200Hz 内低频开始显著抬升的频率（低切点 Hz）。0 = 需回退估算。"""


# ── Analyser ──────────────────────────────────────────────────────


class SpectrumAnalyzer:
    """librosa + scipy 频谱分析，带感知（A-weighted）指标。"""

    # ── Public API ────────────────────────────────────────────

    @staticmethod
    def analyze(
        file_path: str,
        vocal_profile: object | None = None,
    ) -> SpectrumReport:
        """读取 WAV 文件，返回完整 :class:`SpectrumReport`。

        分析流程::

            1. 读取 PCM → mono。
            2. librosa STFT → 帧均值聚合。
            3. A-weighting（感知响度）。
            4. 频段能量汇总。
            5. scipy.find_peaks 共振检测（Q + 谐波过滤）。
            6. 频谱倾斜线性回归。

        *vocal_profile* 为 :class:`VocalProfile` 时，所有频点检测的
        扫描范围将根据性别/唱法动态调整。
        """
        audio, sr = read_pcm(file_path)
        mono = to_mono(audio)

        # ── 读取声纹参数 ──
        vp = vocal_profile
        mud_lo = getattr(vp, "mud_scan_lo", 200.0) if vp else 200.0
        mud_hi = getattr(vp, "mud_scan_hi", 500.0) if vp else 500.0
        pres_lo = getattr(vp, "presence_scan_lo", 2000.0) if vp else 2000.0
        pres_hi = getattr(vp, "presence_scan_hi", 8000.0) if vp else 8000.0
        air_lo = getattr(vp, "air_scan_lo", 4000.0) if vp else 4000.0
        air_hi = getattr(vp, "air_scan_hi", 15000.0) if vp else 15000.0
        res_q = getattr(vp, "resonance_q_threshold", 8.0) if vp else 8.0

        # STFT（librosa）
        magnitude_db, freqs = SpectrumAnalyzer._stft_mean(mono, sr)

        # A-weighting
        a_weighted_db = SpectrumAnalyzer._apply_a_weighting(magnitude_db, freqs)

        # 频段能量（A-weighted）
        band_energy = SpectrumAnalyzer._compute_band_energy(a_weighted_db, freqs)

        # 共振检测（scipy.find_peaks，声纹感知 Q 阈值）
        resonances = SpectrumAnalyzer._detect_resonances(
            a_weighted_db, freqs, min_q=res_q,
        )

        # 频谱倾斜
        tilt = SpectrumAnalyzer._compute_spectral_tilt(a_weighted_db, freqs)

        # 衍生指标
        mid_energy = band_energy.get("mid", -60.0)
        low_mid_energy = band_energy.get("low_mid", -60.0)
        presence_energy = band_energy.get("presence", -60.0)
        air_energy = band_energy.get("air", -60.0)

        mud_ratio = low_mid_energy - mid_energy
        presence_deficit = max(0.0, mid_energy - presence_energy)
        air_level = air_energy

        # 声纹感知的精准频点检测
        presence_gap_hz = SpectrumAnalyzer._find_presence_gap(
            a_weighted_db, freqs, lo_hz=pres_lo, hi_hz=pres_hi,
        )
        mud_peak_hz = SpectrumAnalyzer._find_mud_peak(
            a_weighted_db, freqs, lo_hz=mud_lo, hi_hz=mud_hi,
        )
        air_rolloff_hz = SpectrumAnalyzer._find_air_rolloff(
            a_weighted_db, freqs, lo_hz=air_lo, hi_hz=air_hi,
        )
        hpf_hz = SpectrumAnalyzer._find_hpf_freq(
            a_weighted_db, freqs, mid_energy,
        )

        # 齿音频峰：4–12 kHz 内能量最高的频率
        sib_mask = (freqs >= 4000.0) & (freqs <= 12000.0)
        if np.any(sib_mask):
            sib_idx = int(np.argmax(a_weighted_db[sib_mask]))
            sibilance_peak_hz = float(freqs[sib_mask][sib_idx])
        else:
            sibilance_peak_hz = 8000.0

        return SpectrumReport(
            band_energy_db={k: round(v, 1) for k, v in band_energy.items()},
            spectral_tilt_db_per_octave=round(tilt, 2),
            resonances=resonances,
            mud_ratio_db=round(mud_ratio, 1),
            presence_deficit_db=round(presence_deficit, 1),
            presence_gap_hz=round(presence_gap_hz, 1),
            mud_peak_hz=round(mud_peak_hz, 1),
            air_rolloff_hz=round(air_rolloff_hz, 1),
            hpf_hz=round(hpf_hz, 1),
            sibilance_peak_hz=round(sibilance_peak_hz, 1),
            air_level_db=round(air_level, 1),
        )

    # ── STFT（librosa） ────────────────────────────────────────

    # RMS 门控阈值 (dB)：帧能量高于噪声地板此值才参与统计
    _GATE_THRESHOLD_DB = 6.0

    @staticmethod
    def _stft_mean(
        audio: np.ndarray,
        sr: int,
        n_fft: int = _N_FFT,
        hop_length: int = _HOP_LENGTH,
    ) -> tuple[np.ndarray, np.ndarray]:
        """librosa STFT → RMS 门控均值 (dB)。

        用 librosa.feature.rms 计算每帧能量，
        以中位数 RMS 为噪声地板，排除低于地板 + 6 dB 的静音帧，
        只对有效帧取均值。
        """
        audio = np.asarray(audio, dtype=np.float64)
        if len(audio) == 0:
            freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)
            return np.full_like(freqs, -120.0, dtype=np.float64), freqs

        D = librosa.stft(audio, n_fft=n_fft, hop_length=hop_length)
        S = np.abs(D)
        S_db = librosa.amplitude_to_db(S, ref=np.max)
        freqs = librosa.fft_frequencies(sr=sr, n_fft=n_fft)

        # ── RMS 门控：排除静音帧 ──
        rms = librosa.feature.rms(
            y=audio, frame_length=n_fft, hop_length=hop_length,
        )[0]
        rms_db = librosa.amplitude_to_db(rms)
        noise_floor = float(np.median(rms_db))
        gate = noise_floor + SpectrumAnalyzer._GATE_THRESHOLD_DB
        active = rms_db > gate

        if np.any(active):
            mean_db = np.mean(S_db[:, active], axis=1)
        else:
            # 全部静音 → 回退全帧均值
            mean_db = np.mean(S_db, axis=1)

        return mean_db.astype(np.float64), freqs

    # ── A-weighting (IEC 61672-1:2013) ────────────────────────

    @staticmethod
    def _a_weighting(freqs_hz: np.ndarray) -> np.ndarray:
        """返回每个频率 bin 的 A-weighting 增益 (dB)。

        曲线归一化为 A(1000 Hz) ≈ 0 dB。
        负值表示人耳对该频率较不敏感。
        """
        f = np.asarray(freqs_hz, dtype=np.float64)
        f2 = f * f

        ref_num = 12200.0 ** 2 * 1000.0 ** 4
        ref_den = (
            (1000.0 ** 2 + 20.6 ** 2)
            * np.sqrt((1000.0 ** 2 + 107.7 ** 2) * (1000.0 ** 2 + 737.9 ** 2))
            * (1000.0 ** 2 + 12200.0 ** 2)
        )
        ref_ra = ref_num / ref_den

        num = 12200.0 ** 2 * f2 ** 2
        den = (
            (f2 + 20.6 ** 2)
            * np.sqrt((f2 + 107.7 ** 2) * (f2 + 737.9 ** 2))
            * (f2 + 12200.0 ** 2)
        )
        ra = np.divide(num, den, out=np.full_like(f, 1e-10), where=den > 0)
        a_db = 20.0 * np.log10(np.maximum(ra / ref_ra, 1e-10))
        return a_db

    @staticmethod
    def _apply_a_weighting(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray,
    ) -> np.ndarray:
        """对 dB 幅度谱应用 A-weighting 校正。"""
        a_curve = SpectrumAnalyzer._a_weighting(freqs_hz)
        return magnitude_db + a_curve

    # ── Band energy ───────────────────────────────────────────

    @staticmethod
    def _compute_band_energy(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray,
    ) -> dict[str, float]:
        """计算每个定义频段的平均能量 (dB)。

        在线性域（功率）取均值后转回 dB 以保证功率求和。
        """
        result: dict[str, float] = {}
        linear = 10.0 ** (magnitude_db / 10.0)

        for band_name, (lo, hi) in _BAND_EDGES.items():
            mask = (freqs_hz >= lo) & (freqs_hz < hi)
            if np.any(mask):
                mean_power = float(np.mean(linear[mask]))
                result[band_name] = 10.0 * math.log10(
                    max(mean_power, _EPS_DB),
                )
            else:
                result[band_name] = -120.0

        return result

    # ── Spectral tilt ─────────────────────────────────────────

    @staticmethod
    def _compute_spectral_tilt(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray,
    ) -> float:
        """log(freq)-vs-dB 线性回归斜率，单位 dB/octave。

        负值 = 频谱滚降（偏暗）。
        仅使用 100 Hz–10 kHz 的 bin，避免 DC/Nyquist 偏差。
        """
        mask = (freqs_hz >= 100.0) & (freqs_hz <= 10000.0)
        if np.sum(mask) < 4:
            return 0.0

        log_f = np.log2(freqs_hz[mask])
        db = magnitude_db[mask]

        n = len(log_f)
        sum_x = np.sum(log_f)
        sum_y = np.sum(db)
        sum_xx = np.sum(log_f ** 2)
        sum_xy = np.sum(log_f * db)

        denom = n * sum_xx - sum_x ** 2
        if abs(denom) < 1e-12:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denom
        return float(slope)

    # ── Resonance detection（scipy.find_peaks） ────────────────

    @staticmethod
    def _detect_resonances(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray,
        min_q: float = _MIN_Q_FACTOR,
    ) -> list[Resonance]:
        """用 scipy.signal.find_peaks 检测窄带频谱峰。

        算法:
            1. uniform_filter1d 平滑频谱。
            2. 计算 prominence = 原始 − 平滑。
            3. scipy.find_peaks 找局部极大值（prominence > 阈值）。
            4. 对每个候选计算 Q 因子（−3 dB 带宽）。
            5. 检查是否为人声基频的谐波。
        """
        if len(magnitude_db) < 10:
            return []

        smoothed = uniform_filter1d(
            magnitude_db.astype(np.float64),
            size=_SMOOTHING_WINDOW,
        )
        prominence = magnitude_db - smoothed

        # scipy 峰值检测
        peaks, props = find_peaks(
            prominence,
            prominence=_MIN_PROMINENCE_DB,
            distance=_PEAK_DISTANCE,
        )

        # 估算人声基频候选（谐波检测用）
        f0_estimates = SpectrumAnalyzer._estimate_f0_range(
            magnitude_db, freqs_hz,
        )

        resonances: list[Resonance] = []
        for idx in peaks:
            freq = float(freqs_hz[idx])
            prom_db = float(prominence[idx])
            q_val = SpectrumAnalyzer._compute_q_factor(
                magnitude_db, freqs_hz, idx,
            )
            is_harm = SpectrumAnalyzer._is_harmonic(
                freq, f0_estimates, tolerance=_HARMONIC_TOLERANCE,
            )
            resonances.append(
                Resonance(
                    freq_hz=round(freq, 1),
                    prominence_db=round(prom_db, 1),
                    q_factor=round(q_val, 1),
                    is_harmonic=is_harm,
                )
            )

        resonances.sort(key=lambda r: r.prominence_db, reverse=True)
        return resonances[:5]

    @staticmethod
    def _compute_q_factor(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray, peak_idx: int,
    ) -> float:
        """计算 Q = 中心频率 / −3 dB 带宽。

        从 *peak_idx* 向左右扩展，直到幅度下降 3 dB。
        若无法找到 −3 dB 点则返回 100.0（极窄峰）。
        """
        if peak_idx < 0 or peak_idx >= len(magnitude_db):
            return 100.0

        peak_db = magnitude_db[peak_idx]
        threshold_db = peak_db - 3.0
        centre_freq = freqs_hz[peak_idx]

        left_idx = peak_idx
        while left_idx > 0 and magnitude_db[left_idx] > threshold_db:
            left_idx -= 1
        freq_left = freqs_hz[left_idx]

        right_idx = peak_idx
        while (right_idx < len(magnitude_db) - 1
               and magnitude_db[right_idx] > threshold_db):
            right_idx += 1
        freq_right = freqs_hz[right_idx]

        bandwidth = freq_right - freq_left
        if bandwidth <= 0.0:
            return 100.0

        return centre_freq / bandwidth

    # ── Presence gap detection ────────────────────────────────

    @staticmethod
    def _find_presence_gap(
        magnitude_db: np.ndarray,
        freqs_hz: np.ndarray,
        lo_hz: float = 2000.0,
        hi_hz: float = 8000.0,
    ) -> float:
        """在 2k–8kHz 内找实际频谱与理想平滑过渡差距最大的频率。

        理想曲线 = 从 mid 平均能量到 presence 平均能量的线性插值。
        返回实际频谱低于理想曲线最多的频率点（Hz），
        即为「存在感缺失最严重」的位置。
        """
        from scipy.ndimage import uniform_filter1d

        mask = (freqs_hz >= lo_hz) & (freqs_hz <= hi_hz)
        if np.sum(mask) < 10:
            return 3000.0  # 回退到经验默认值

        f = freqs_hz[mask]
        m = magnitude_db[mask]

        # 平滑
        s = uniform_filter1d(m.astype(np.float64), size=5)

        # 理想曲线端点
        mid_mask = (freqs_hz >= 500.0) & (freqs_hz <= 2000.0)
        pre_mask = (freqs_hz >= 5000.0) & (freqs_hz <= 8000.0)
        mid_avg = float(np.mean(magnitude_db[mid_mask]))
        pre_avg = float(np.mean(magnitude_db[pre_mask]))

        # 线性插值
        ideal = np.interp(f, [lo_hz, hi_hz], [mid_avg, pre_avg])

        # 找实际低于理想最多的点
        gap = ideal - s
        max_idx = int(np.argmax(gap))
        return float(f[max_idx])

    @staticmethod
    def _find_mud_peak(
        magnitude_db: np.ndarray,
        freqs_hz: np.ndarray,
        lo_hz: float = 200.0,
        hi_hz: float = 500.0,
    ) -> float:
        """在 200–500Hz 内找能量最高的频率（泥巴中心）。

        返回该范围内幅度最大的频点。用于精准定位泥巴切的位置。
        """
        mask = (freqs_hz >= lo_hz) & (freqs_hz <= hi_hz)
        if np.sum(mask) < 3:
            return 350.0
        idx = int(np.argmax(magnitude_db[mask]))
        return float(freqs_hz[mask][idx])

    @staticmethod
    def _find_air_rolloff(
        magnitude_db: np.ndarray,
        freqs_hz: np.ndarray,
        lo_hz: float = 4000.0,
        hi_hz: float = 15000.0,
    ) -> float:
        """找高频衰减开始加速的频率（空气搁架起点）。

        在 4k–15kHz 范围计算局部斜率（一阶差分），
        找到斜率开始持续为负的第一个频率点。
        """
        from scipy.ndimage import uniform_filter1d

        mask = (freqs_hz >= lo_hz) & (freqs_hz <= hi_hz)
        if np.sum(mask) < 10:
            return 8000.0

        f = freqs_hz[mask]
        m = magnitude_db[mask]
        s = uniform_filter1d(m.astype(np.float64), size=5)
        diff = np.diff(s)

        # 找第一个连续 3 个 bin 都为负的位置
        for i in range(len(diff) - 2):
            if diff[i] < 0 and diff[i + 1] < 0 and diff[i + 2] < 0:
                return float(f[i + 1])

        # 回退：找最大负斜率位置
        steepest = int(np.argmin(diff))
        return float(f[steepest + 1])

    @staticmethod
    def _find_hpf_freq(
        magnitude_db: np.ndarray,
        freqs_hz: np.ndarray,
        mid_energy_db: float,
        lo_hz: float = 40.0,
        hi_hz: float = 200.0,
    ) -> float:
        """在 40–200Hz 内找低频开始显著抬升的频率。

        从高频向低频扫描，找到第一个能量超过 mid_energy + 3dB 的频点。
        如果未找到，回退到 80Hz（人声安全默认值）。
        """
        from scipy.ndimage import uniform_filter1d

        mask = (freqs_hz >= lo_hz) & (freqs_hz <= hi_hz)
        if np.sum(mask) < 5:
            return 80.0

        f = freqs_hz[mask]
        m = magnitude_db[mask]
        s = uniform_filter1d(m.astype(np.float64), size=3)

        # 阈值：至少 -50 dB（A-weighted），低于此值视为无显著低频累积
        min_meaningful_db = -50.0
        threshold = max(mid_energy_db + 3.0, min_meaningful_db)

        # 从高频向低频扫描，找第一个超过阈值的
        for i in range(len(f) - 1, -1, -1):
            if s[i] > threshold:
                return float(f[i])

        # 无显著低频累积 → 回退 80Hz（安全默认值）
        return 80.0

    # ── Fundamental frequency estimation ──────────────────────

    @staticmethod
    def _estimate_f0_range(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray,
    ) -> list[float]:
        """返回可能的人声基频列表。

        策略：找 80–400 Hz 范围内最强的峰作为 F0 候选。
        """
        mask = (freqs_hz >= _F0_MIN_HZ) & (freqs_hz <= _F0_MAX_HZ)
        if np.sum(mask) < 3:
            return [200.0]

        sub_mag = magnitude_db[mask]
        sub_freqs = freqs_hz[mask]

        peak_idxs, _ = find_peaks(sub_mag, prominence=3.0)
        if len(peak_idxs) == 0:
            best = int(np.argmax(sub_mag))
            return [float(sub_freqs[best])]

        peak_pairs = [
            (float(sub_freqs[i]), float(sub_mag[i])) for i in peak_idxs
        ]
        peak_pairs.sort(key=lambda x: x[1], reverse=True)
        return [freq for freq, _ in peak_pairs[:3]]

    @staticmethod
    def _is_harmonic(
        freq_hz: float,
        f0_estimates: list[float],
        tolerance: float = _HARMONIC_TOLERANCE,
    ) -> bool:
        """检查 *freq_hz* 是否接近任意估计基频的整数倍。

        当 |freq/f0 − round(freq/f0)| < tolerance 时返回 ``True``。
        """
        if not f0_estimates:
            return False
        for f0 in f0_estimates:
            if f0 <= 0:
                continue
            ratio = freq_hz / f0
            nearest_int = round(ratio)
            if nearest_int < 1:
                continue
            if abs(ratio - nearest_int) / nearest_int < tolerance:
                return True
        return False
