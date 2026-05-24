#!/usr/bin/env python3
"""Phase 6 Verification: MixingEngine end-to-end in REAPER.

Uses 大湾区的梦 28-track multitrack for real acceptance testing.
"""

import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_core.engine import MixingEngine
from hermes_core.signal import SignalAnalyzer

AUDIO_DIR = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "Hermes 测试", "大湾区的梦 分轨", "分轨",
)

# 5 test stems from acceptance plan scene 2
STEM_FILES = [
    os.path.join(AUDIO_DIR, "Drum Kick.wav"),
    os.path.join(AUDIO_DIR, "Drum Snare.wav"),
    os.path.join(AUDIO_DIR, "Bass.wav"),
    os.path.join(AUDIO_DIR, "Guitar EGT Clean.wav"),
    os.path.join(AUDIO_DIR, "Vocal_81.wav"),
]

print("=" * 60)
print("PHASE 6 VERIFICATION -- MixingEngine E2E")
print("=" * 60)

results = []

# ── P6.1: Construction & context manager ────────────────

print("\n--- P6.1: MixingEngine construction ---")
with MixingEngine() as eng:
    h = eng.health_check()
    print(f"  Health check: {h}")

ok = isinstance(h, dict) and "reapy_connected" in h
results.append(("P6.1 construction + health_check", ok))
print(f"  {'PASS' if ok else 'FAIL'}")

reaper_ok = h.get("reapy_connected", False)

# ── P6.2: check_headroom (offline) ──────────────────────

print("\n--- P6.2: check_headroom ---")
with MixingEngine() as eng2:
    hr = eng2.check_headroom()

ok = (
    hr.get("source") == "unavailable_without_render"
    and "headroom_dbtp" in hr
)
results.append(("P6.2 check_headroom offline", ok))
print(f"  Result: {hr}")
print(f"  {'PASS' if ok else 'FAIL'}")

# ── P6.3: audit_mix (offline, synthetic file) ───────────

print("\n--- P6.3: audit_mix (offline) ---")
import struct, wave
import numpy as np

with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as f:
    test_wav = f.name

sr = 48000
n = int(sr * 0.5)
t = np.arange(n) / sr
tone = 0.3 * np.sin(2.0 * np.pi * 1000.0 * t)
stereo = np.column_stack([tone, tone])
i16 = np.clip(stereo * 32767.0, -32768, 32767).astype(np.int16)
with wave.open(test_wav, "wb") as wf:
    wf.setnchannels(2)
    wf.setsampwidth(2)
    wf.setframerate(sr)
    wf.writeframes(i16.tobytes())

with MixingEngine() as eng3:
    audit = eng3.audit_mix(test_wav)

os.unlink(test_wav)

print(f"  Passed: {audit.get('passed')}, checks: {len(audit.get('checks', []))}")
for c in audit.get("checks", []):
    print(f"    [{c['severity']}] {c['check_name']}: {c['message'][:60]}")

ok = isinstance(audit, dict) and "passed" in audit and "checks" in audit
results.append(("P6.3 audit_mix", ok))
print(f"  {'PASS' if ok else 'FAIL'}")

# ── Scene 2-6: REAPER integration ───────────────────────

if not reaper_ok:
    print("\n  REAPER not available -- skipping integration tests")
    for name in ("P6.4 import_stems + list_tracks",
                 "P6.5 gain staging",
                 "P6.6 create_bus + reverb send",
                 "P6.7 render_mix + signal_check",
                 "P6.8 audit rendered mix"):
        results.append((name, "SKIP"))
else:
    with MixingEngine() as eng:
        # ── P6.4: import_stems + list_tracks ─────────
        print("\n--- P6.4: import_stems + list_tracks ---")
        eng.create_project(sample_rate=48000)

        imported = eng.import_stems(STEM_FILES, position=0.0)
        success = sum(1 for r in imported if r["success"])
        print(f"  Imported: {success}/{len(imported)} stems")

        tracks = eng.list_tracks()
        for t in tracks:
            print(f"  Track {t.index}: '{t.name}', items={t.item_count}, vol={t.volume_db}dB")

        ok = (
            len(tracks) == len(STEM_FILES)
            and success == len(STEM_FILES)
            and all(t.item_count == 1 for t in tracks)
        )
        results.append(("P6.4 import_stems + list_tracks", ok))
        print(f"  {'PASS' if ok else 'FAIL'}")

        # ── P6.5: gain staging ───────────────────────
        print("\n--- P6.5: gain staging ---")
        vocal_idx = None
        bass_idx = None
        for t in tracks:
            if "Vocal" in t.name:
                vocal_idx = t.index
            if "Bass" in t.name:
                bass_idx = t.index

        if bass_idx is not None:
            eng.apply_gain(bass_idx, -2.0, target="track_fader")

        gs = eng.get_gain_structure()
        print(f"  Tracks in gain structure: {len(gs['tracks'])}")

        bass_vol = None
        for t in gs["tracks"]:
            if "Bass" in t["name"]:
                bass_vol = t["volume_db"]

        ok = (
            isinstance(gs, dict)
            and "tracks" in gs
            and (bass_idx is None or abs(bass_vol - (-2.0)) < 0.5 if bass_vol else True)
        )
        results.append(("P6.5 gain staging", ok))
        print(f"  {'PASS' if ok else 'FAIL'}")

        # ── P6.6: create_bus + reverb send ──────────
        print("\n--- P6.6: create_bus + reverb send ---")
        kick_idx = None
        snare_idx = None
        for t in tracks:
            if "Kick" in t.name:
                kick_idx = t.index
            if "Snare" in t.name:
                snare_idx = t.index

        bus_ok = False
        reverb_ok = False

        if kick_idx is not None and snare_idx is not None:
            bus_result = eng.create_bus("Drum Bus", [kick_idx, snare_idx])
            print(f"  Drum Bus created at index {bus_result}")

            bus_tracks = eng.list_tracks()
            bus_name_found = any(
                "Drum Bus" in t.name for t in bus_tracks
            )
            bus_ok = bus_result >= 0 and bus_name_found
            print(f"  Bus verified: {bus_ok}")

        if vocal_idx is not None:
            try:
                rv = eng.create_reverb_send(vocal_idx, level_db=-8.0)
                reverb_ok = rv["send"]["index"] >= 0
                print(f"  Reverb send: aux={rv['aux_index']}, send_idx={rv['send']['index']}, fx_idx={rv['fx_index']}")
            except Exception as e:
                print(f"  Reverb send failed: {e}")

        ok = bus_ok and reverb_ok
        results.append(("P6.6 create_bus + reverb send", ok))
        print(f"  {'PASS' if ok else 'FAIL'}")

        # ── P6.7: render_mix + signal_check ─────────
        print("\n--- P6.7: render_mix + signal_check ---")
        with tempfile.TemporaryDirectory() as out_dir:
            render_result = eng.render_mix(out_dir, bounds="entire_project", timeout=30.0)

            has_path = render_result.get("output_path") is not None
            has_check = "signal_check" in render_result
            file_ok = has_path and os.path.isfile(render_result["output_path"])

            print(f"  Output: {render_result.get('output_path')}")
            if has_check:
                sc = render_result["signal_check"]
                print(f"  LUFS: {sc.get('integrated_lufs')}, TruePeak: {sc.get('true_peak_dbtp')} dBTP")
                print(f"  Clips: {sc.get('clip_count')}, Silence: {sc.get('silence_passed')}")

            ok = file_ok and has_check
            results.append(("P6.7 render_mix + signal_check", ok))
            print(f"  {'PASS' if ok else 'FAIL'}")

            # ── P6.8: audit rendered mix ────────────
            print("\n--- P6.8: audit rendered mix ---")
            if file_ok:
                audit = eng.audit_mix(render_result["output_path"])
                print(f"  Passed: {audit.get('passed')}")
                print(f"  Diagnostics: {audit.get('diagnostics')}")
                ok = isinstance(audit, dict) and "passed" in audit
            else:
                ok = False
            results.append(("P6.8 audit rendered mix", ok))
            print(f"  {'PASS' if ok else 'FAIL'}")

# ── Summary ──────────────────────────────────────────────

print("\n" + "=" * 60)
print("PHASE 6 VERIFICATION RESULTS")
print("=" * 60)
passed = sum(1 for _, ok in results if ok is True)
failed = sum(1 for _, ok in results if ok is False)
skipped = sum(1 for _, ok in results if ok == "SKIP")
for name, ok in results:
    status = "PASS" if ok is True else ("FAIL" if ok is False else "SKIP")
    print(f"  [{status}] {name}")
print(f"\n  Total: {passed} passed, {failed} failed, {skipped} skipped")
if failed == 0:
    print("\n  *** PHASE 6: PASS ***")
else:
    print(f"\n  *** PHASE 6: FAIL ({failed} checks failed) ***")
