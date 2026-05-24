#!/usr/bin/env python3
"""Phase 5 Verification: render.py + signal.py end-to-end.

Tests:
  P5.1: SignalAnalyzer — 16-bit WAV analysis
  P5.2: SignalAnalyzer — 24-bit WAV analysis
  P5.3: SignalAnalyzer — clip detection
  P5.4: SignalAnalyzer — silence detection
  P5.5: RenderManager.render_mix() in REAPER (integration)
  P5.6: render_mix + signal_check pipeline (integration)
"""

import os
import sys
import struct
import wave
import tempfile

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_core.signal import SignalAnalyzer, SignalReport
from hermes_core.bridge import ReaperBridge
from hermes_core.render import RenderManager
from hermes_core.track import TrackManager

# ── Helpers ──────────────────────────────────────────────

def _sine(freq, duration_sec, sample_rate, amplitude=1.0):
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    return amplitude * np.sin(2.0 * np.pi * freq * t)


def _gen_16bit_wav(filepath, mono_samples, sample_rate=48000):
    stereo = np.column_stack([mono_samples, mono_samples])
    i16 = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())


def _gen_24bit_wav(filepath, mono_samples, sample_rate=48000):
    stereo = np.column_stack([mono_samples, mono_samples])
    scaled = np.clip(stereo * 8388607.0, -8388608, 8388607).astype(np.int32)
    flat = scaled.flatten()
    chunks = []
    for v in flat:
        if int(v) < 0:
            v = int(v) + (1 << 24)
        chunks.append(struct.pack("<I", int(v))[:3])
    with wave.open(filepath, "wb") as wf:
        wf.setnchannels(2)
        wf.setsampwidth(3)
        wf.setframerate(sample_rate)
        wf.writeframes(b"".join(chunks))


print("=" * 60)
print("PHASE 5 VERIFICATION")
print("=" * 60)

results = []

# ── P5.1: 16-bit WAV analysis ───────────────────────────

print("\n--- P5.1: SignalAnalyzer — 16-bit WAV ---")
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    tmp = f.name

sr = 48000
duration = 0.5
amplitude = 10.0 ** (-6.0 / 20.0)
pcm = _sine(1000.0, duration, sr, amplitude)
_gen_16bit_wav(tmp, pcm, sr)

report = SignalAnalyzer.analyze(tmp)
print(f"  RMS: {report.rms_db} dB, Peak: {report.peak_db} dB")
print(f"  LUFS: {report.integrated_lufs}, True Peak: {report.true_peak_dbtp} dBTP")
print(f"  Duration: {report.duration_sec}s, SR: {report.sample_rate}")
print(f"  Clips: {report.clip_count} (passed={report.clip_passed})")
print(f"  Silence: {report.silence_passed}")

ok = (
    report.sample_rate == sr
    and abs(report.peak_db - (-6.0)) < 1.0
    and report.clip_passed
    and report.silence_passed
    and abs(report.duration_sec - duration) < 0.05
)
results.append(("P5.1 16-bit analysis", ok))
print(f"  {'PASS' if ok else 'FAIL'}")
os.unlink(tmp)

# ── P5.2: 24-bit WAV analysis ───────────────────────────

print("\n--- P5.2: SignalAnalyzer — 24-bit WAV ---")
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    tmp = f.name

_gen_24bit_wav(tmp, pcm, sr)
report = SignalAnalyzer.analyze(tmp)
print(f"  RMS: {report.rms_db} dB, Peak: {report.peak_db} dB")
print(f"  LUFS: {report.integrated_lufs}, True Peak: {report.true_peak_dbtp} dBTP")

ok = (
    report.sample_rate == sr
    and abs(report.peak_db - (-6.0)) < 1.0
    and report.clip_passed
)
results.append(("P5.2 24-bit analysis", ok))
print(f"  {'PASS' if ok else 'FAIL'}")
os.unlink(tmp)

# ── P5.3: clip detection ────────────────────────────────

print("\n--- P5.3: SignalAnalyzer — clip detection ---")
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    tmp = f.name

clipped = _sine(500.0, 0.1, sr, amplitude=2.0)
_gen_16bit_wav(tmp, clipped, sr)
report = SignalAnalyzer.analyze(tmp)
print(f"  Clip count: {report.clip_count}, clip_passed={report.clip_passed}")

ok = report.clip_count > 0 and not report.clip_passed
results.append(("P5.3 clip detection", ok))
print(f"  {'PASS' if ok else 'FAIL'}")
os.unlink(tmp)

# ── P5.4: silence detection ─────────────────────────────

print("\n--- P5.4: SignalAnalyzer — silence detection ---")
with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    tmp = f.name

silent = np.zeros(int(sr * 0.2), dtype=np.float64)
_gen_16bit_wav(tmp, silent, sr)
report = SignalAnalyzer.analyze(tmp)
print(f"  RMS: {report.rms_db} dB, silence_passed={report.silence_passed}")

ok = not report.silence_passed
results.append(("P5.4 silence detection", ok))
print(f"  {'PASS' if ok else 'FAIL'}")
os.unlink(tmp)

# ── P5.5: Render in REAPER (integration) ─────────────────

print("\n--- P5.5: RenderManager — REAPER render (integration) ---")
bridge = ReaperBridge()
reaper_available = bridge.connect()

if not reaper_available:
    print("  REAPER not running — skipping integration tests")
    results.append(("P5.5 render_mix in REAPER", "SKIP"))
    results.append(("P5.6 render + signal pipeline", "SKIP"))
else:
    api = bridge.api
    api.Main_OnCommand(40001, 0)

    track_mgr = TrackManager(bridge)
    render_mgr = RenderManager(bridge)

    idx = track_mgr.create(name="Test_Tone")

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
        test_wav = f.name
    tone = _sine(440.0, 1.0, 44100, amplitude=0.3)
    _gen_16bit_wav(test_wav, tone, 44100)
    track_mgr.import_media(idx, test_wav, position=0.0)

    with tempfile.TemporaryDirectory() as out_dir:
        result = render_mgr.render_mix(out_dir, bounds="entire_project", fmt="wav_24bit", timeout=30.0)

        ok = (
            "output_path" in result
            and result["output_path"] is not None
            and os.path.isfile(result["output_path"])
            and os.path.getsize(result["output_path"]) > 100
        )
        results.append(("P5.5 render_mix in REAPER", ok))
        print(f"  Output: {result.get('output_path')}")
        print(f"  {'PASS' if ok else 'FAIL'}")

        # ── P5.6: Render + signal pipeline ──────────────────
        print("\n--- P5.6: Render + signal check pipeline ---")
        if ok:
            sig = SignalAnalyzer.analyze(result["output_path"])
            print(f"  RMS: {sig.rms_db} dB, Peak: {sig.peak_db} dB")
            print(f"  LUFS: {sig.integrated_lufs}, True Peak: {sig.true_peak_dbtp} dBTP")
            print(f"  Clips: {sig.clip_count}, Silence: {sig.silence_passed}")

            pipe_ok = (
                sig.silence_passed
                and np.isfinite(sig.integrated_lufs)
                and sig.duration_sec > 0.5
            )
            results.append(("P5.6 render + signal pipeline", pipe_ok))
            print(f"  {'PASS' if pipe_ok else 'FAIL'}")
        else:
            results.append(("P5.6 render + signal pipeline", "SKIP"))

    os.unlink(test_wav)

# ── Summary ──────────────────────────────────────────────

print("\n" + "=" * 60)
print("PHASE 5 VERIFICATION RESULTS")
print("=" * 60)
passed = sum(1 for _, ok in results if ok is True)
failed = sum(1 for _, ok in results if ok is False)
skipped = sum(1 for _, ok in results if ok == "SKIP")
for name, ok in results:
    status = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    print(f"  [{status}] {name}")
print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped, {len(results)} total")
if failed == 0:
    print("\n  *** PHASE 5: PASS ***")
else:
    print(f"\n  *** PHASE 5: FAIL ({failed} checks failed) ***")
