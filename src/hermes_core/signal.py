"""
Signal analysis — pure-numpy audio metrics (RMS, Peak, LUFS, True Peak).
Zero REAPER dependency. Core analysis is pure Python + numpy.
"""

import math
import struct
import wave
from dataclasses import dataclass

import numpy as np

# ITU-R BS.1770-4 calibration offset for 1 kHz full-scale sine reference
_LUFS_CALIBRATION = -0.691
# Silence threshold in dBFS
_SILENCE_THRESHOLD_DB = -60.0


def _read_wav_manual(file_path: str) -> tuple[int, int, int, bytes]:
    """Manually parse a WAV header for formats the stdlib ``wave`` rejects.

    Returns (sample_width, sample_rate, channels, raw_pcm_bytes).
    Supports 16-bit PCM and 32-bit IEEE float.
    """
    with open(file_path, "rb") as fh:
        riff = fh.read(4)
        if riff != b"RIFF":
            raise ValueError("Not a WAV file")
        fh.read(4)  # file size
        wave_id = fh.read(4)
        if wave_id != b"WAVE":
            raise ValueError("Not a WAV file")

        fmt_tag = 0
        channels = 1
        sr = 44100
        bits_per_sample = 16

        while True:
            chunk_id = fh.read(4)
            if len(chunk_id) < 4:
                break
            chunk_size = struct.unpack("<I", fh.read(4))[0]
            if chunk_id == b"fmt ":
                fmt_data = fh.read(chunk_size)
                fmt_tag = struct.unpack_from("<H", fmt_data, 0)[0]
                channels = struct.unpack_from("<H", fmt_data, 2)[0]
                sr = struct.unpack_from("<I", fmt_data, 4)[0]
                if chunk_size >= 16:
                    bits_per_sample = struct.unpack_from("<H", fmt_data, 14)[0]
            elif chunk_id == b"data":
                raw = fh.read(chunk_size)
                break
            else:
                fh.read(chunk_size)

    sw = bits_per_sample // 8
    if fmt_tag == 3:  # IEEE float
        sw = 4
    return sw, sr, channels, raw


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
        """Read a WAV file and return a full SignalReport.

        Multi-channel files are measured with power-preserving channel
        averaging for RMS and LUFS (per ITU-R BS.1770-4).
        """
        pcm, sample_rate = SignalAnalyzer._read_pcm(file_path)
        if pcm.size == 0:
            return SignalReport(
                rms_db=-200.0, peak_db=-200.0, integrated_lufs=-120.0,
                true_peak_dbtp=-120.0, clip_count=0, clip_passed=True,
                silence_passed=True, duration_sec=0.0, sample_rate=sample_rate,
            )

        n_samples = pcm.shape[0]
        duration = n_samples / sample_rate

        # RMS — power over all samples and channels
        rms_linear = float(np.sqrt(np.mean(pcm ** 2)))
        rms_db = 20.0 * math.log10(max(rms_linear, 1e-10))

        # Peak — max absolute value across all channels
        abs_pcm = np.abs(pcm)
        peak_linear = float(np.max(abs_pcm))
        peak_db = 20.0 * math.log10(max(peak_linear, 1e-10))

        clip_count = int(np.sum(abs_pcm >= 0.999))
        clip_passed = clip_count == 0
        silence_passed = rms_db > _SILENCE_THRESHOLD_DB

        integrated_lufs = SignalAnalyzer._compute_lufs(pcm, sample_rate)

        mono = SignalAnalyzer._to_mono(pcm)
        true_peak_dbtp = SignalAnalyzer._compute_true_peak(mono)

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
        """Read WAV, return (multi-channel_float64, sample_rate).

        Returns ``(n_samples, n_channels)`` shaped array.  The caller is
        responsible for downmixing when mono is needed.

        Handles 16/24-bit PCM and 32-bit float.  Falls back to a manual
        header parse when the stdlib ``wave`` module rejects float WAVs.
        """
        # Try stdlib wave first (handles 16/24-bit PCM cleanly)
        try:
            with wave.open(file_path, "rb") as wf:
                sw = wf.getsampwidth()
                sr = wf.getframerate()
                nch = wf.getnchannels()
                nframes = wf.getnframes()
                raw = wf.readframes(nframes)
        except wave.Error:
            # Manual parse for float WAVs (format tag 3)
            sw, sr, nch, raw = _read_wav_manual(file_path)

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

        return pcm.astype(np.float64), sr

    @staticmethod
    def _to_mono(pcm: np.ndarray) -> np.ndarray:
        """Downmix multi-channel to mono via standard (L+R) / nch."""
        if pcm.ndim == 1:
            return pcm
        return np.mean(pcm, axis=1)

    # ── LUFS (ITU-R BS.1770-4) ──────────────────────────────

    @staticmethod
    def _compute_lufs(pcm: np.ndarray, sample_rate: int) -> float:
        """Integrated LUFS with per-channel K-weighting (ITU-R BS.1770-4)."""
        if pcm.size == 0:
            return -120.0

        if pcm.ndim == 1:
            pcm = pcm.reshape(-1, 1)
        nch = pcm.shape[1]

        # K-weight each channel independently
        k_weighted = [
            SignalAnalyzer._k_weight(pcm[:, ch], sample_rate) for ch in range(nch)
        ]
        # Power-average across channels per sample
        kw = np.sqrt(np.mean([kw_ch ** 2 for kw_ch in k_weighted], axis=0))

        block_samples = int(0.4 * sample_rate)
        hop = block_samples // 4
        if block_samples == 0 or len(kw) < block_samples:
            mean_sq = np.mean(kw ** 2)
            return float(10.0 * math.log10(max(mean_sq, 1e-13))) + _LUFS_CALIBRATION

        block_power = []
        for start in range(0, len(kw) - block_samples + 1, hop):
            block = kw[start : start + block_samples]
            block_power.append(float(np.mean(block ** 2)))

        if not block_power:
            mean_sq = np.mean(kw ** 2)
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
