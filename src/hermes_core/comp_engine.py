"""
压缩器参数引擎 — 从信号分析推导压缩器物理参数。

从 engine.py 提取的模块级辅助函数。
"""

import bisect

from hermes_core.loudness_optimizer import CompressionIntent
from hermes_core.genre_tables import (
    _GENRE_CREST_GR_RATIO,
    _GENRE_RVOX_MULTIPLIER,
)




def _derive_compressor_intent(
    rms_db: float, peak_db: float, *, genre: str = "pop"
) -> CompressionIntent:
    """Derive compression targets from Crest Factor (Peak – RMS).

    ==============  ============  ============================
    Crest Factor    Amount        Typical material
    ==============  ============  ============================
    ≥ 15 dB         ``"heavy"``   Folk ballad, classical vocal
    10–15 dB        ``"medium"``  Pop vocal, rock vocal
    < 10 dB         ``"light"``   Pre-compressed, synth, EDM
    ==============  ============  ============================

    *gr_target_db* is genre-aware — transparent genres (folk, ballad)
    use a lighter crest multiplier (0.10) while dense genres
    (electronic) use 0.20.  This keeps per-track compression aligned
    with the bus compressor's genre-based GR target.
    """
    crest = peak_db - rms_db
    ratio = _GENRE_CREST_GR_RATIO.get(genre, 0.15)

    if crest >= 15.0:
        amount = "heavy"
        gr_target = round(crest * ratio, 1)
    elif crest >= 10.0:
        amount = "medium"
        gr_target = round(crest * ratio, 1)
    else:
        amount = "light"
        gr_target = round(crest * ratio, 1)

    return CompressionIntent(
        amount=amount,
        gr_target_db=gr_target,
        crest_factor_db=round(crest, 1),
        rms_db=round(rms_db, 1),
        peak_db=round(peak_db, 1),
    )


def _apply_vca_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """VCA / digital compressor → physical parameter dict.

    Threshold is placed so that the signal's peak exceeds it by
    *gr_target_db* — the compressor catches the transient and
    reduces it by the target amount.
    """
    threshold = intent.peak_db - intent.gr_target_db
    ratio = {
        "light":  2.0,
        "medium": 4.0,
        "heavy":  8.0,
    }.get(intent.amount, 4.0)

    return {
        "Threshold":   round(threshold, 1),
        "Ratio":       ratio,
        "Attack":      preset["attack_ms"],
        "Release":     preset["release_ms"],
        # 0.6: conservative makeup gain coefficient (60% of GR)
        #      prevents over-compensation while restoring perceived loudness
        "Makeup Gain": round(intent.gr_target_db * 0.6, 1),
    }


def _apply_fet_params(intent: CompressionIntent,
                       preset: dict[str, float]) -> dict[str, float]:
    """FET compressor (1176-style) → physical parameter dict.

    The Input knob sets an *equivalent threshold* — we compute the
    threshold from peak + target GR, then let the normalisation layer
    reverse-lookup the knob position via the calibration table.
    """
    threshold = intent.peak_db - intent.gr_target_db
    return {
        "Input":    round(threshold, 1),
        "Output":   round(intent.gr_target_db * 0.5, 1),
        "Attack":   preset["attack_ms"],
        "Release":  preset["release_ms"],
    }


def _apply_opto_params(intent: CompressionIntent,
                        preset: dict[str, float]) -> dict[str, float]:
    """Optical compressor (LA-2A style) → physical parameter dict."""
    return {
        "Peak Reduction": round(intent.gr_target_db, 1),
        "Gain":           round(intent.gr_target_db * 0.4, 1),
    }


def _apply_rvox_params(intent: CompressionIntent,
                        preset: dict[str, float],
                        rvox_multiplier: float = 1.0) -> dict[str, float]:
    """Waves RVox → physical parameter dict.

    RVox is a single-fader dynamic processor with fixed internal
    ceiling (0 dBFS).  The Compression fader combines threshold,
    auto-ratio, and auto make-up gain into one control.

    Body compression = CLA-76 GR × *rvox_multiplier*.  Since CLA-76
    already grabbed the peaks, RVox focuses on body/RMS consistency.
    Dense genres use >1.0 for more body control; sparse genres use
    lower values.

    Calibration (望归 Vocal, 2026-05-31):
      Comp  →  GR_peak  (3 data points, 1:1 linear relationship)
      -12.3  →  -12 dB
      -6.0   →  -6 dB
      -3.0   →  -2.5 dB

    Level-match: Gain = Comp × 0.6 (verified by A/B bypass).
    Gate is a gentle downward expander, defaulting to off.
    """
    # Compression: genre-scaled body targeting.
    compression_db = -intent.gr_target_db * rvox_multiplier
    compression_db = max(-36.0, min(0.0, compression_db))

    # Gain: level-match — prevent auto-gain loudness from masking compression.
    # Coeff 0.6 is the user-verified sweet spot.
    gain_db = compression_db * 0.6
    gain_db = max(-36.0, min(0.0, gain_db))

    # Gate: off by default (-120 dB ≈ -Inf).  Signal analysis does not yet
    # produce a noise-floor estimate to justify an automatic gate.
    gate_db = -120.0

    return {
        "Compression": round(compression_db, 1),
        "Gate":        round(gate_db, 1),
        "Gain":        round(gain_db, 1),
    }



# ════════════════════════════════════════════════════════════════
# 校准信号生成
# ════════════════════════════════════════════════════════════════


def generate_calibration_signal(output_dir: str,
                                duration: float = 5.0,
                                sr: int = 48000) -> str:
    """Generate a -18 dBFS RMS pink-like noise WAV for calibration.

    生成用于压缩器校准的粉噪测试信号，电平 -18 dBFS RMS。

    Parameters
    ----------
    output_dir : str
        输出目录。
    duration : float
        信号时长（秒），默认 5.0。
    sr : int
        采样率，默认 48000。

    Returns
    -------
    str
        生成的 WAV 文件路径。
    """
    import logging
    import os

    import numpy as np
    import soundfile as sf

    log = logging.getLogger(__name__)

    n = int(sr * duration)
    rng = np.random.default_rng(42)
    # Approximate pink noise via filtered white noise
    white = rng.standard_normal(n)
    # Simple 1/f filter: cumulative sum of white noise
    pink = np.cumsum(white)
    pink /= np.max(np.abs(pink)) + 1e-10
    # Scale to -18 dBFS RMS
    target_linear = 10.0 ** (-18.0 / 20.0)
    pink *= target_linear / (np.sqrt(np.mean(pink ** 2)) + 1e-10)
    stereo = np.column_stack([pink, pink])

    out_path = os.path.join(output_dir, "cal_signal.wav")
    sf.write(out_path, stereo, sr, subtype="FLOAT")
    log.info("Generated calibration signal: %s (%.1fs, -18 dBFS RMS)",
             out_path, duration)
    return out_path
