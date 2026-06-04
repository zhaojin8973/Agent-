"""
Mastering loudness optimizer — V2.

Uses a hard-clip limiter model + binary search to find the optimal
Pro-L 2 Gain that produces a target integrated LUFS.  The model
accounts for the limiter's nonlinear behaviour so the open-loop formula
``gain = target - probe`` is no longer needed.  A one-time calibration
compensates for the systematic offset between hard-clip and Pro-L 2.
"""

import json
import logging
import math
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Optional

import numpy as np
import pyloudnorm as pyln

from hermes_core.audio_utils import read_pcm
from hermes_core.signal import SignalAnalyzer

log = logging.getLogger(__name__)

_CALIBRATION_FILE = "loudness_calibration.json"


# ── data structures ──────────────────────────────────────────


@dataclass
class CompressionIntent:
    """Compression goals derived from Crest Factor analysis.

    This is the **adapter-pattern bridge** between signal analysis and
    compressor-specific parameter translation.  It describes *what* the
    compressor should do (in plugin-agnostic terms), not *how* to set
    the knobs.
    """
    amount: str            # "light" | "medium" | "heavy"
    gr_target_db: float    # target gain reduction (dB)
    crest_factor_db: float  # Peak - RMS
    rms_db: float          # reference RMS after clip gain (≈ -18 dBFS)
    peak_db: float         # peak level after clip gain


@dataclass
class EqBandIntent:
    """A single EQ band adjustment derived from spectrum analysis.

    Describes *what* EQ change to make in plugin-agnostic terms.
    The EQ translator layer maps this to physical plugin parameters.
    """
    band_type: str       # "hp", "lp", "bell", "high_shelf"
    freq_hz: float
    gain_db: float       # negative = cut, positive = boost
    q: float
    reason: str          # human-readable, e.g. "340Hz room mode Q=22.3 prominence=8.2dB"


@dataclass
class EqIntent:
    """EQ goals derived from spectrum analysis.

    This is the **adapter-pattern bridge** between spectrum analysis and
    EQ-specific parameter translation — mirroring :class:`CompressionIntent`
    for the EQ domain.  It describes *what* EQ changes to make, not *how*
    to set the knobs on a specific EQ plugin.
    """
    bands: list[EqBandIntent]
    spectral_tilt: str   # "dark" | "neutral" | "bright"
    mud_detected: bool


@dataclass
class LoudnessResult:
    """Optimal-gain search result."""
    gain_db: float
    predicted_lufs: float
    probe_lufs: float
    iterations: int
    converged: bool
    calibration_applied: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class VerifyResult:
    """Verification of the final render against the LUFS target."""
    actual_lufs: float
    target_lufs: float
    deviation: float
    passed: bool
    needs_correction: bool
    suggested_correction: float


# ── hard-clip limiter model ─────────────────────────────────────

def _hard_clip(
    audio: np.ndarray,
    gain_db: float,
    ceiling_db: float = -0.5,
) -> np.ndarray:
    """Apply gain then hard-clip at *ceiling_db* via ``np.clip``.

    This is the simplest possible limiter model — it is NOT a brickwall
    limiter.  Compared to Pro-L 2 it lacks lookahead and smooth release,
    so the waveform differs, but the **integrated LUFS** difference is
    small (0.3-0.8 LUFS) and systematic — a one-time calibration offset
    compensates for it.
    """
    gain_linear = 10.0 ** (gain_db / 20.0)
    ceiling_linear = 10.0 ** (ceiling_db / 20.0)
    limited = np.clip(audio * gain_linear, -ceiling_linear, ceiling_linear)
    return limited


# ── core search engine ────────────────────────────────────────

def find_optimal_gain(
    probe_path: str,
    target_lufs: float = -12.0,
    ceiling_dbtp: float = -0.5,
    tolerance: float = 0.3,
    gain_range: tuple[float, float] = (-6.0, 24.0),
    max_iterations: int = 25,
    calibration_offset: float = 0.0,
) -> LoudnessResult:
    """Binary-search the Gain that makes the brickwall-limited output
    hit *target_lufs*.

    The function ``f(gain) = LUFS(brickwall( audio × 10^(gain/20) ))``
    is monotonic, so binary search finds the root in ~log₂(range) steps.
    """
    # ── read probe WAV ──
    pcm, sr = read_pcm(probe_path)
    if pcm.size == 0:
        return LoudnessResult(0.0, -120.0, -120.0, 0, False, 0.0)

    # pyloudnorm expects (samples, channels); _read_pcm returns float64
    meter = pyln.Meter(sr)
    probe_lufs = meter.integrated_loudness(pcm)
    log.info("Probe LUFS (Gain=0): %.1f", probe_lufs)

    if math.isinf(probe_lufs) or probe_lufs < -70:
        log.warning("Probe is near-silent (LUFS=%.1f) — cannot compute gain", probe_lufs)
        return LoudnessResult(0.0, probe_lufs, probe_lufs, 0, False, 0.0)

    # ── binary search ──
    lo, hi = gain_range
    measured_lufs = probe_lufs
    iterations = 0

    for i in range(max_iterations):
        mid = (lo + hi) / 2.0
        iterations = i + 1

        limited = _hard_clip(pcm, mid, ceiling_dbtp)
        measured_lufs = meter.integrated_loudness(limited)

        log.debug("  iter %d: gain=%+.2f dB → LUFS=%.1f", iterations, mid, measured_lufs)

        if abs(measured_lufs - target_lufs) < tolerance:
            break

        if measured_lufs < target_lufs:
            lo = mid
        else:
            hi = mid

    final_gain = round((lo + hi) / 2.0 + calibration_offset, 2)
    converged = abs(measured_lufs - target_lufs) < tolerance

    result = LoudnessResult(
        gain_db=final_gain,
        predicted_lufs=float(round(measured_lufs, 1)),
        probe_lufs=float(round(probe_lufs, 1)),
        iterations=iterations,
        converged=bool(converged),
        calibration_applied=calibration_offset,
    )

    log.info(
        "Search done: gain=%+.2f dB, predicted=%.1f LUFS, "
        "converged=%s, iters=%d",
        result.gain_db, result.predicted_lufs, result.converged, result.iterations,
    )
    return result


# ── verification ──────────────────────────────────────────────

def verify_output(
    final_path: str,
    target_lufs: float = -12.0,
    pass_threshold: float = 1.0,
    damping: float = 0.8,
) -> VerifyResult:
    """Check whether the final render hit the LUFS target."""
    pcm, sr = read_pcm(final_path)
    meter = pyln.Meter(sr)
    actual_lufs = meter.integrated_loudness(pcm)

    deviation = float(actual_lufs - target_lufs)
    passed = bool(abs(deviation) < pass_threshold)
    correction = -deviation * damping if not passed else 0.0

    return VerifyResult(
        actual_lufs=round(actual_lufs, 1),
        target_lufs=target_lufs,
        deviation=round(deviation, 2),
        passed=passed,
        needs_correction=not passed,
        suggested_correction=round(correction, 2),
    )


# ── calibration ───────────────────────────────────────────────

def run_calibration(
    probe_path: str,
    final_path: str,
    applied_gain: float,
    ceiling_dbtp: float = -0.5,
) -> float:
    """Measure the systematic offset between brickwall simulation and Pro-L 2.

    Run once after changing limiter style/settings.  The offset is saved
    to disk and loaded automatically by ``load_calibration()``.
    """
    pcm, sr = read_pcm(probe_path)
    meter = pyln.Meter(sr)

    sim_lufs = meter.integrated_loudness(_hard_clip(pcm, applied_gain, ceiling_dbtp))

    final_pcm, _ = read_pcm(final_path)
    actual_lufs = meter.integrated_loudness(final_pcm)

    offset_lufs = actual_lufs - sim_lufs
    calibration_offset = -offset_lufs

    cal_data = {
        "calibration_offset_db": round(calibration_offset, 3),
        "sim_lufs": round(sim_lufs, 2),
        "actual_lufs": round(actual_lufs, 2),
        "offset_lufs": round(offset_lufs, 2),
        "applied_gain_db": applied_gain,
    }

    Path(_CALIBRATION_FILE).write_text(
        json.dumps(cal_data, indent=2, ensure_ascii=False),
    )
    log.info(
        "Calibration: sim=%.1f, actual=%.1f, offset=%+.2f LUFS, "
        "gain_correction=%+.3f dB → saved to %s",
        sim_lufs, actual_lufs, offset_lufs, calibration_offset, _CALIBRATION_FILE,
    )
    return calibration_offset


def load_calibration() -> float:
    """Return the saved calibration offset, or 0.0 if none exists."""
    path = Path(_CALIBRATION_FILE)
    if not path.exists():
        return 0.0
    data = json.loads(path.read_text())
    return data.get("calibration_offset_db", 0.0)


# ── report ────────────────────────────────────────────────────

def generate_report(
    result: LoudnessResult,
    verify: Optional[VerifyResult] = None,
) -> str:
    """Human-readable mastering loudness report."""
    lines = [
        "=" * 50,
        "  Mastering Loudness Report",
        "=" * 50,
        f"  Probe LUFS       : {result.probe_lufs} LUFS",
        f"  Target LUFS      : -12.0 LUFS",
        f"  " + chr(0x2500) * 25,
        f"  Optimal Gain     : {result.gain_db:+.2f} dB",
        f"  Predicted LUFS   : {result.predicted_lufs} LUFS",
        f"  Search iters     : {result.iterations}",
        f"  Converged        : {'Yes' if result.converged else 'No'}",
        f"  Calibration      : {result.calibration_applied:+.3f} dB",
    ]
    if verify:
        lines += [
            f"  " + chr(0x2500) * 25,
            f"  Actual LUFS      : {verify.actual_lufs} LUFS",
            f"  Deviation        : {verify.deviation:+.2f} LUFS",
            f"  Status           : {'PASS' if verify.passed else 'NEEDS CORRECTION'}",
        ]
        if verify.needs_correction:
            lines.append(
                f"  Suggested fix    : {verify.suggested_correction:+.2f} dB",
            )
    lines.append("=" * 50)
    return "\n".join(lines)


# ── ITU-R BS.1770-4 集成响度计算（完整实现）───────────────────


def calculate_lufs_bs1770_4(
    pcm: np.ndarray,
    sample_rate: int,
    channel_weights: list[float] | None = None,
) -> float:
    """ITU-R BS.1770-4 集成响度计算。

    与 ``pyloudnorm`` 的 K-weighting（一阶近似）不同，此函数实现了
    完整的 ITU-R BS.1770-4 规范：

    1. **K-weighting 滤波器**：二阶 RLB 高通 + 一阶预加重高通
    2. **声道加权求和**：前置声道权重 1.0，环绕声道权重 1.41 (+1.5 dB)
    3. **门限均值**：-70 LKFS 绝对门限 + -10 LU 相对门限

    Args:
        pcm: 音频数据，shape (samples, channels) 或 (samples,)
        sample_rate: 采样率 (Hz)
        channel_weights: 每声道权重列表。为 None 时自动分配
            （前 3 声道为前置权重 1.0，之后为环绕权重 1.41）

    Returns:
        集成 LUFS 值（float）

    Examples:
        >>> import numpy as np
        >>> sr = 48000
        >>> pcm = np.random.normal(0, 0.1, (sr * 2, 2))  # 2 秒立体声
        >>> lufs = calculate_lufs_bs1770_4(pcm, sr)
        >>> print(f"{lufs:.1f} LUFS")
    """
    from hermes_core.signal import SignalAnalyzer
    return SignalAnalyzer._compute_lufs_bs1770_4(
        pcm, sample_rate, channel_weights=channel_weights,
    )
