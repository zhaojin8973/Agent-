"""
Signal analysis — pure-numpy audio metrics (RMS, Peak, LUFS, True Peak).
Zero REAPER dependency. Core analysis is pure Python + numpy.
"""

import math
import wave
from dataclasses import dataclass

import numpy as np

# ITU-R BS.1770-4 calibration offset for 1 kHz full-scale sine reference
_LUFS_CALIBRATION = -0.691
# Silence threshold in dBFS
_SILENCE_THRESHOLD_DB = -60.0


@dataclass
class SignalReport:
    rms_db: float
    peak_db: float
    integrated_lufs: float
    true_peak_dbtp: float
    clip_count: int
    clip_passed: bool
    silence_passed: bool
    duration_sec: float
    sample_rate: int


class SignalAnalyzer:
    """Pure-numpy audio analysis."""

    @staticmethod
    def analyze(file_path: str) -> SignalReport:
        """Read a WAV file and return a full SignalReport."""
        pcm, sample_rate = SignalAnalyzer._read_pcm(file_path)
        n = len(pcm)
        duration = n / sample_rate

        abs_pcm = np.abs(pcm)
        peak_linear = float(np.max(abs_pcm)) if n > 0 else 0.0
        peak_db = 20.0 * math.log10(max(peak_linear, 1e-10))

        rms_linear = float(np.sqrt(np.mean(pcm ** 2))) if n > 0 else 0.0
        rms_db = 20.0 * math.log10(max(rms_linear, 1e-10))

        clip_count = int(np.sum(abs_pcm >= 0.999))
        clip_passed = clip_count == 0
        silence_passed = rms_db > _SILENCE_THRESHOLD_DB

        integrated_lufs = SignalAnalyzer._compute_lufs(pcm, sample_rate)
        true_peak_dbtp = SignalAnalyzer._compute_true_peak(pcm)

        return SignalReport(
            rms_db=round(rms_db, 1),
            peak_db=round(peak_db, 1),
            integrated_lufs=round(integrated_lufs, 1),
            true_peak_dbtp=round(true_peak_dbtp, 1),
            clip_count=clip_count,
            clip_passed=clip_passed,
            silence_passed=silence_passed,
            duration_sec=round(duration, 3),
            sample_rate=sample_rate,
        )

    @staticmethod
    def _read_pcm(file_path: str) -> tuple[np.ndarray, int]:
        """Read WAV, return (mono_float64, sample_rate). Handles 16/24-bit PCM and 32-bit float."""
        with wave.open(file_path, "rb") as wf:
            sw = wf.getsampwidth()
            sr = wf.getframerate()
            nch = wf.getnchannels()
            nframes = wf.getnframes()
            raw = wf.readframes(nframes)

        if sw == 2:
            pcm = np.frombuffer(raw, dtype=np.int16).astype(np.float64) / 32768.0
        elif sw == 3:
            total = len(raw) // 3
            padded = np.frombuffer(
                raw + b"\x00", dtype=np.uint8
            )[: total * 3].reshape(-1, 3)
            i32 = (
                padded[:, 0].astype(np.int32)
                + padded[:, 1].astype(np.int32) * 256
                + padded[:, 2].astype(np.int32) * 65536
            )
            i32[i32 >= 8388608] -= 16777216
            pcm = i32.astype(np.float64) / 8388608.0
        elif sw == 4:
            pcm = np.frombuffer(raw, dtype=np.float32).astype(np.float64)
        else:
            raise ValueError(f"Unsupported sample width: {sw} (only 16/24-bit PCM and 32-bit float)")

        pcm = pcm.reshape(-1, nch)
        if nch >= 2:
            pcm = np.mean(pcm, axis=1)
        else:
            pcm = pcm.flatten()

        return pcm.astype(np.float64), sr

    # ── LUFS (ITU-R BS.1770-4) ──────────────────────────────

    @staticmethod
    def _compute_lufs(pcm: np.ndarray, sample_rate: int) -> float:
        """Integrated LUFS with K-weighting, 400ms blocks, absolute and relative gates."""
        if len(pcm) == 0:
            return -120.0

        k_weighted = SignalAnalyzer._k_weight(pcm, sample_rate)

        block_samples = int(0.4 * sample_rate)
        hop = block_samples // 4
        if block_samples == 0 or len(k_weighted) < block_samples:
            mean_sq = np.mean(k_weighted ** 2)
            return float(10.0 * math.log10(max(mean_sq, 1e-13))) + _LUFS_CALIBRATION

        block_power = []
        for start in range(0, len(k_weighted) - block_samples + 1, hop):
            block = k_weighted[start : start + block_samples]
            block_power.append(float(np.mean(block ** 2)))

        if not block_power:
            mean_sq = np.mean(k_weighted ** 2)
            return float(10.0 * math.log10(max(mean_sq, 1e-13))) + _LUFS_CALIBRATION

        # Absolute gate: -70 LUFS
        abs_thresh = 10 ** (-7.0)
        gated_power = [p for p in block_power if p > abs_thresh]

        if not gated_power:
            return -120.0

        # Relative gate: -10 dB below mean gated level
        mean_gated = np.mean(gated_power)
        rel_thresh = mean_gated / 10.0
        rel_gated = [p for p in gated_power if p > rel_thresh]

        if not rel_gated:
            rel_gated = gated_power

        integrated_power = float(np.mean(rel_gated))
        return float(10.0 * math.log10(max(integrated_power, 1e-13))) + _LUFS_CALIBRATION

    @staticmethod
    def _biquad_hp(x: np.ndarray, fc: float, sample_rate: int) -> np.ndarray:
        """First-order high-pass via bilinear transform of H(s)=s/(s+wc)."""
        if len(x) < 2:
            return x.copy()
        w = 2.0 * math.pi * fc / sample_rate
        k = w / 2.0
        b0 = 1.0 / (1.0 + k)
        b1 = -b0
        a1 = (k - 1.0) / (1.0 + k)
        y = np.zeros_like(x)
        y[0] = x[0]
        for i in range(1, len(x)):
            y[i] = b0 * x[i] + b1 * x[i - 1] - a1 * y[i - 1]
        return y

    @staticmethod
    def _k_weight(pcm: np.ndarray, sample_rate: int) -> np.ndarray:
        """Apply K-weighting: pre-emphasis (38 Hz HP) + RLB (100 Hz HP) via bilinear IIR."""
        stage1 = SignalAnalyzer._biquad_hp(pcm, 38.0, sample_rate)
        return SignalAnalyzer._biquad_hp(stage1, 100.0, sample_rate)

    # ── True Peak (BS.1770-4 Annex 2) ─────────────────────────

    @staticmethod
    def _compute_true_peak(pcm: np.ndarray) -> float:
        """True peak via 4x oversampling with windowed sinc interpolation."""
        if len(pcm) == 0:
            return -120.0

        oversample = 4
        n = len(pcm)
        kernel_size = 64
        k = np.arange(-kernel_size, kernel_size + 1)
        sinc = np.sinc(k / oversample)
        window = np.hamming(2 * kernel_size + 1)
        fir = sinc * window
        fir = fir / np.sum(fir) * oversample

        up = np.zeros(n * oversample, dtype=np.float64)
        up[::oversample] = pcm

        convolved = np.convolve(up, fir, mode="same")
        true_peak_linear = float(np.max(np.abs(convolved)))
        return 20.0 * math.log10(max(true_peak_linear, 1e-10))
