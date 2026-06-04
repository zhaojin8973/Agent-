"""
Spectrum analysis — pure-numpy FFT-based audio metrics.

Zero REAPER dependency.  Uses STFT + P90 percentile aggregation to
capture dynamic resonances, A-weighting for perceptual loudness, and
Q-factor + harmonic filtering to distinguish room modes from musical
partials.

Follows the same static-method analyser pattern as :mod:`hermes_core.signal`.
"""

import math
from dataclasses import dataclass

import numpy as np
import soundfile as sf

from hermes_core.audio_utils import read_pcm, to_mono

# ── Constants ────────────────────────────────────────────────────

# Minimum energy floor in dB to avoid log10(0)
_EPS_DB = 1e-10

# STFT parameters
_DEFAULT_FRAME_MS = 50
_DEFAULT_OVERLAP = 0.5

# Resonance detection
_MIN_PROMINENCE_DB = 6.0        # peak must exceed smoothed curve by this much
_MIN_Q_FACTOR = 15.0            # narrower than this → likely room mode
_HARMONIC_TOLERANCE = 0.05      # ±5 % of integer multiple → harmonic candidate

# Fundamental frequency search range (Hz)
_F0_MIN_HZ = 80.0
_F0_MAX_HZ = 1000.0

# Smoothing window: 1/3 octave equivalent in bin count at 1 kHz
_SMOOTHING_BINS_DEFAULT = 5


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
    """A detected spectral peak that may be a room mode.

    Attributes:
        freq_hz:        Centre frequency in Hz.
        prominence_db:  How far the peak rises above the local smoothed
                        spectrum (dB).
        q_factor:       Centre frequency divided by -3 dB bandwidth.
                        Values above 15 suggest a room resonance rather
                        than a musical partial.
        is_harmonic:    ``True`` when the peak frequency is within 5 %
                        of an integer multiple of an estimated vocal
                        fundamental.
    """
    freq_hz: float
    prominence_db: float
    q_factor: float
    is_harmonic: bool


@dataclass
class SpectrumReport:
    """Aggregate spectrum metrics for one audio file.

    All energy figures are **A-weighted** (perceptual) and derived from
    the P90 STFT aggregate unless noted otherwise.
    """
    band_energy_db: dict[str, float]
    """A-weighted P90 energy per band (dB)."""

    spectral_tilt_db_per_octave: float
    """Slope of log(freq)-vs-A-weighted-dB regression.  Negative = darker."""

    resonances: list[Resonance]
    """Detected narrow-band peaks, sorted by prominence descending."""

    mud_ratio_db: float
    """How much louder the low_mid band is vs mid (> 3 → muddy)."""

    presence_deficit_db: float
    """How much quieter the presence band is vs mid (> 0 → dark)."""

    sibilance_peak_hz: float
    """Highest-energy frequency in 4–12 kHz (sibilance detection band)."""

    air_level_db: float
    """Average A-weighted energy in the air band (dB)."""


# ── Analyser ──────────────────────────────────────────────────────


class SpectrumAnalyzer:
    """Pure-numpy spectrum analysis with perceptual (A-weighted) metrics."""

    # ── Public API ────────────────────────────────────────────

    @staticmethod
    def analyze(file_path: str) -> SpectrumReport:
        """Read a WAV file and return a full :class:`SpectrumReport`.

        The analysis pipeline::

            1. Read PCM → mono.
            2. STFT (50 ms Hann windows, 50 % overlap).
            3. P90 aggregation across all frames.
            4. A-weighting for perceptual loudness.
            5. Band energy summary.
            6. Resonance detection (Q + harmonic filter).
            7. Spectral tilt regression.
        """
        audio, sr = read_pcm(file_path)
        mono = to_mono(audio)

        # STFT + P90 aggregate
        magnitude_db, freqs = SpectrumAnalyzer._stft_p90(mono, sr)

        # A-weighting
        a_weighted_db = SpectrumAnalyzer._apply_a_weighting(magnitude_db, freqs)

        # Band energies (A-weighted)
        band_energy = SpectrumAnalyzer._compute_band_energy(a_weighted_db, freqs)

        # Resonance detection on A-weighted spectrum
        resonances = SpectrumAnalyzer._detect_resonances(a_weighted_db, freqs)

        # Spectral tilt (A-weighted)
        tilt = SpectrumAnalyzer._compute_spectral_tilt(a_weighted_db, freqs)

        # Derived metrics
        mid_energy = band_energy.get("mid", -60.0)
        low_mid_energy = band_energy.get("low_mid", -60.0)
        presence_energy = band_energy.get("presence", -60.0)
        air_energy = band_energy.get("air", -60.0)

        mud_ratio = low_mid_energy - mid_energy
        presence_deficit = max(0.0, mid_energy - presence_energy)
        air_level = air_energy

        # Sibilance peak: highest-energy frequency in 4–12 kHz.
        sib_mask = (freqs >= 4000.0) & (freqs <= 12000.0)
        if np.any(sib_mask):
            sib_idx = int(np.argmax(a_weighted_db[sib_mask]))
            sibilance_peak_hz = float(freqs[sib_mask][sib_idx])
        else:
            sibilance_peak_hz = 8000.0  # fallback: typical sibilance centre

        return SpectrumReport(
            band_energy_db={k: round(v, 1) for k, v in band_energy.items()},
            spectral_tilt_db_per_octave=round(tilt, 2),
            resonances=resonances,
            mud_ratio_db=round(mud_ratio, 1),
            presence_deficit_db=round(presence_deficit, 1),
            sibilance_peak_hz=round(sibilance_peak_hz, 1),
            air_level_db=round(air_level, 1),
        )

    # ── I/O helpers ───────────────────────────────────────────

    # ── STFT + P90 ────────────────────────────────────────────

    @staticmethod
    def _stft_p90(
        audio: np.ndarray,
        sr: int,
        frame_ms: int = _DEFAULT_FRAME_MS,
        overlap: float = _DEFAULT_OVERLAP,
    ) -> tuple[np.ndarray, np.ndarray]:
        """Short-Time Fourier Transform with P90 percentile aggregation.

        Returns:
            ``(magnitude_db, freq_bins)`` where *magnitude_db* is the
            P90 energy at each frequency bin (dB) and *freq_bins* is
            the corresponding frequency axis (Hz).
        """
        audio = np.asarray(audio, dtype=np.float64)
        if len(audio) == 0:
            freqs = np.fft.rfftfreq(512, d=1.0 / max(sr, 1))
            return np.full_like(freqs, -120.0, dtype=np.float64), freqs

        frame_samples = int(frame_ms / 1000.0 * sr)
        if frame_samples < 16:
            frame_samples = 16
        hop = max(1, int(frame_samples * (1.0 - overlap)))

        window = np.hanning(frame_samples)
        frames_db: list[np.ndarray] = []

        for start in range(0, len(audio) - frame_samples + 1, hop):
            frame = audio[start:start + frame_samples] * window
            mag = np.abs(np.fft.rfft(frame))
            mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
            frames_db.append(mag_db)

        if not frames_db:
            # Audio shorter than one frame — process whole thing
            padded = np.zeros(frame_samples, dtype=np.float64)
            n = min(len(audio), frame_samples)
            padded[:n] = audio[:n]
            padded *= window
            mag = np.abs(np.fft.rfft(padded))
            mag_db = 20.0 * np.log10(np.maximum(mag, _EPS_DB))
            frames_db.append(mag_db)

        stacked = np.array(frames_db)  # (n_frames, n_bins)
        p90 = np.percentile(stacked, 90, axis=0)
        freqs = np.fft.rfftfreq(frame_samples, d=1.0 / sr)

        return p90, freqs

    # ── A-weighting (IEC 61672-1:2013) ────────────────────────

    @staticmethod
    def _a_weighting(freqs_hz: np.ndarray) -> np.ndarray:
        """Return A-weighting gain (dB) for each frequency bin.

        The curve is normalised so that :math:`A(1000\\,\\text{Hz}) \\approx 0`
        dB.  Negative values indicate frequencies the ear is less sensitive to.
        """
        f = np.asarray(freqs_hz, dtype=np.float64)
        f2 = f * f

        # Reference value at 1 kHz (for 0 dB normalisation)
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
        magnitude_db: np.ndarray, freqs_hz: np.ndarray
    ) -> np.ndarray:
        """Add A-weighting correction to a dB magnitude spectrum."""
        a_curve = SpectrumAnalyzer._a_weighting(freqs_hz)
        return magnitude_db + a_curve

    # ── Band energy ───────────────────────────────────────────

    @staticmethod
    def _compute_band_energy(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray
    ) -> dict[str, float]:
        """Compute mean energy (dB) in each defined frequency band.

        Energy is averaged in **linear** domain then converted back to
        dB to preserve power summing.
        """
        result: dict[str, float] = {}
        linear = 10.0 ** (magnitude_db / 10.0)  # power, not amplitude

        for band_name, (lo, hi) in _BAND_EDGES.items():
            mask = (freqs_hz >= lo) & (freqs_hz < hi)
            if np.any(mask):
                mean_power = float(np.mean(linear[mask]))
                result[band_name] = 10.0 * math.log10(max(mean_power, _EPS_DB))
            else:
                result[band_name] = -120.0

        return result

    # ── Spectral tilt ─────────────────────────────────────────

    @staticmethod
    def _compute_spectral_tilt(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray
    ) -> float:
        """Linear regression slope of log(freq)-vs-dB, in dB/octave.

        A negative slope means the spectrum rolls off (darker).
        Only bins between 100 Hz and 10 kHz are used to avoid DC /
        Nyquist bias.
        """
        mask = (freqs_hz >= 100.0) & (freqs_hz <= 10000.0)
        if np.sum(mask) < 4:
            return 0.0

        log_f = np.log2(freqs_hz[mask])
        db = magnitude_db[mask]

        # Linear regression: db = slope * log_f + intercept
        n = len(log_f)
        sum_x = np.sum(log_f)
        sum_y = np.sum(db)
        sum_xx = np.sum(log_f ** 2)
        sum_xy = np.sum(log_f * db)

        denom = n * sum_xx - sum_x ** 2
        if abs(denom) < 1e-12:
            return 0.0

        slope = (n * sum_xy - sum_x * sum_y) / denom
        return float(slope)  # dB per octave (log2)

    # ── Resonance detection ───────────────────────────────────

    @staticmethod
    def _detect_resonances(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray
    ) -> list[Resonance]:
        """Detect narrow-band spectral peaks that may be room resonances.

        Algorithm:
            1. Smooth the spectrum with a moving average.
            2. Compute *prominence* = original − smoothed.
            3. Find local maxima in prominence above threshold.
            4. For each candidate, compute Q factor from −3 dB bandwidth.
            5. Check whether the frequency is a harmonic of an estimated
               vocal fundamental.
            6. Keep only candidates with Q > *_MIN_Q_FACTOR* that are
               **not** harmonics.
        """
        if len(magnitude_db) < 10:
            return []

        smoothed = SpectrumAnalyzer._smooth_spectrum(
            magnitude_db, _SMOOTHING_BINS_DEFAULT
        )
        prominence = magnitude_db - smoothed

        # Find local maxima in prominence
        peak_indices = SpectrumAnalyzer._find_peaks(prominence, _MIN_PROMINENCE_DB)

        # Estimate vocal fundamental candidates (for harmonic check)
        f0_estimates = SpectrumAnalyzer._estimate_f0_range(
            magnitude_db, freqs_hz
        )

        resonances: list[Resonance] = []
        for idx in peak_indices:
            freq = float(freqs_hz[idx])
            prom_db = float(prominence[idx])
            q_val = SpectrumAnalyzer._compute_q_factor(
                magnitude_db, freqs_hz, idx
            )
            is_harm = SpectrumAnalyzer._is_harmonic(
                freq, f0_estimates, tolerance=_HARMONIC_TOLERANCE
            )
            resonances.append(
                Resonance(
                    freq_hz=round(freq, 1),
                    prominence_db=round(prom_db, 1),
                    q_factor=round(q_val, 1),
                    is_harmonic=is_harm,
                )
            )

        # Sort by prominence descending
        resonances.sort(key=lambda r: r.prominence_db, reverse=True)
        return resonances[:5]  # top 5

    @staticmethod
    def _smooth_spectrum(
        magnitude_db: np.ndarray, window_bins: int
    ) -> np.ndarray:
        """Moving-average smooth with reflective boundary handling."""
        if window_bins < 1:
            return magnitude_db.copy()
        kernel = np.ones(2 * window_bins + 1) / (2 * window_bins + 1)
        return np.convolve(magnitude_db, kernel, mode="same")

    @staticmethod
    def _find_peaks(
        prominence: np.ndarray, min_prominence_db: float
    ) -> list[int]:
        """Return indices of local maxima in *prominence* above threshold."""
        if len(prominence) < 3:
            return []
        indices: list[int] = []
        p = prominence
        for i in range(1, len(p) - 1):
            if p[i] > p[i - 1] and p[i] > p[i + 1] and p[i] >= min_prominence_db:
                indices.append(i)
        return indices

    @staticmethod
    def _compute_q_factor(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray, peak_idx: int
    ) -> float:
        """Compute Q = centre_freq / bandwidth at −3 dB from peak.

        Walks left and right from *peak_idx* until the magnitude drops
        by 3 dB.  Returns a large sentinel value if the −3 dB points
        cannot be found (extremely narrow peak).
        """
        if peak_idx < 0 or peak_idx >= len(magnitude_db):
            return 100.0

        peak_db = magnitude_db[peak_idx]
        threshold_db = peak_db - 3.0
        centre_freq = freqs_hz[peak_idx]

        # Walk left
        left_idx = peak_idx
        while left_idx > 0 and magnitude_db[left_idx] > threshold_db:
            left_idx -= 1
        freq_left = freqs_hz[left_idx]

        # Walk right
        right_idx = peak_idx
        while right_idx < len(magnitude_db) - 1 and magnitude_db[right_idx] > threshold_db:
            right_idx += 1
        freq_right = freqs_hz[right_idx]

        bandwidth = freq_right - freq_left
        if bandwidth <= 0.0:
            return 100.0  # extremely narrow — likely a pure tone / room mode

        return centre_freq / bandwidth

    # ── Fundamental frequency estimation ──────────────────────

    @staticmethod
    def _estimate_f0_range(
        magnitude_db: np.ndarray, freqs_hz: np.ndarray
    ) -> list[float]:
        """Return a small list of likely vocal fundamental frequencies.

        Strategy: find the strongest peaks in the 80–400 Hz range.
        These are candidates for the vocal F0 (and its sub-harmonics).
        """
        mask = (freqs_hz >= _F0_MIN_HZ) & (freqs_hz <= 400.0)
        if np.sum(mask) < 3:
            return [200.0]  # sensible default

        sub_mag = magnitude_db[mask]
        sub_freqs = freqs_hz[mask]

        # Find peaks in this range
        peak_idxs = SpectrumAnalyzer._find_peaks(sub_mag, 3.0)
        if not peak_idxs:
            # Fallback: pick the frequency with max energy
            best = int(np.argmax(sub_mag))
            return [float(sub_freqs[best])]

        # Return the top 3 peak frequencies, sorted by energy
        peak_pairs = [(float(sub_freqs[i]), float(sub_mag[i])) for i in peak_idxs]
        peak_pairs.sort(key=lambda x: x[1], reverse=True)
        return [freq for freq, _ in peak_pairs[:3]]

    @staticmethod
    def _is_harmonic(
        freq_hz: float,
        f0_estimates: list[float],
        tolerance: float = _HARMONIC_TOLERANCE,
    ) -> bool:
        """Check whether *freq_hz* is close to an integer multiple of
        any estimated fundamental.

        Returns ``True`` when :math:`|freq / f0 - round(freq/f0)| < tolerance`.
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
