#!/usr/bin/env python3
"""Phase 4 Verification: fx.py + send.py in REAPER with real plugins.

Tests:
  P4.1: FxManager.add() + get_chain() — ReaEQ insert & verify
  P4.2: FxManager.add() — Pro-Q 3 VST3 insert & verify
  P4.3: FxManager.set_param() + get_param() — round-trip consistency (Pro-Q 3)
  P4.4: FxManager.set_enabled() — A/B bypass (ReaEQ)
  P4.5: FxManager.remove() — FX deletion
  P4.6: FxManager.get_param_list() — enum all params (Pro-Q 3)
  P4.7: SendManager.create() + set_level() + get_info() — send round-trip
  P4.8: SendManager — reverb aux send + level + mute
  P4.9: SendManager.list_all()
  P4.10: FxManager.get_param_name()
"""

import sys, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from hermes_core.bridge import ReaperBridge
from hermes_core.fx import FxManager
from hermes_core.send import SendManager

bridge = ReaperBridge()
bridge.connect()
fx = FxManager(bridge)
send_mgr = SendManager(bridge)
api = bridge.api

def _find_track_idx(name_contains):
    """Real-time track index lookup by name substring."""
    n = api.CountTracks(0)
    for i in range(n):
        ok, ptr, key, tname, bufsz = api.GetSetMediaTrackInfo_String(
            api.GetTrack(0, i), "P_NAME", "", 256
        )
        if name_contains in tname:
            return i
    return -1

# ── Setup: clean project, create tracks at end ──────────
api.Main_OnCommand(40001, 0)          # File > New project (no save prompt)
print("=" * 60)
print("PHASE 4 VERIFICATION — REAPER INTEGRATION")
print("=" * 60)

# Create all 3 tracks at end
def _add_track_at_end(name):
    n = api.CountTracks(0)
    api.InsertTrackAtIndex(n, True)
    ptr = api.GetTrack(0, n)
    api.GetSetMediaTrackInfo_String(ptr, "P_NAME", name, True)
    return n

eq_track = _add_track_at_end("Test_EQ")
pq_track = _add_track_at_end("Test_ProQ")
rv_track = _add_track_at_end("Verb_Return")
print(f"  Tracks: EQ={eq_track}, ProQ={pq_track}, Verb={rv_track}")

results = []


# ── P4.1: ReaEQ add + get_chain ────────────────────────
print("\n--- P4.1: FxManager.add(ReaEQ) + get_chain ---")
eq_fx_idx = fx.add(eq_track, "ReaEQ")
chain = fx.get_chain(eq_track)

ok = (
    eq_fx_idx >= 0
    and len(chain) >= 1
    and "ReaEQ" in chain[0]["name"]
    and chain[0]["enabled"] is True
)
results.append(("P4.1 ReaEQ add+get_chain", ok))
print(f"  add returned: {eq_fx_idx}")
print(f"  chain: {chain}")
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.2: Pro-Q 3 VST3 add ─────────────────────────────
print("\n--- P4.2: FxManager.add(Pro-Q 3 VST3) ---")
pq_fx_idx = fx.add(pq_track, "VST3: Pro-Q 3 (FabFilter)")
chain = fx.get_chain(pq_track)

ok = (
    pq_fx_idx >= 0
    and len(chain) == 1
    and "Pro-Q" in chain[0]["name"]
)
results.append(("P4.2 Pro-Q 3 VST3 add", ok))
print(f"  add returned: {pq_fx_idx}")
print(f"  chain: {chain}")
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.3: set_param + get_param round-trip (Pro-Q 3) ───
print("\n--- P4.3: set_param + get_param round-trip (Pro-Q 3) ---")
params = fx.get_param_list(pq_track, 0)
print(f"  Pro-Q 3 params ({len(params)} total)")

band_params = {}
for i, p in enumerate(params):
    nl = p['name'].lower()
    if 'band 1 freq' in nl:   band_params['freq'] = i
    elif 'band 1 gain' in nl: band_params['gain'] = i
    elif 'band 1 q' in nl:    band_params['q'] = i

if band_params:
    fx.set_param(pq_track, 0, band_params['freq'], 0.048)
    fx.set_param(pq_track, 0, band_params['gain'], 0.65)
    fx.set_param(pq_track, 0, band_params['q'], 0.5)
    fr = fx.get_param(pq_track, 0, band_params['freq'])
    gr = fx.get_param(pq_track, 0, band_params['gain'])
    qr = fx.get_param(pq_track, 0, band_params['q'])
    ok = abs(fr - 0.048) < 0.01 and abs(gr - 0.65) < 0.01 and abs(qr - 0.5) < 0.01
    print(f"  Set: freq=0.048 gain=0.65 q=0.5")
    print(f"  Read: freq={fr:.4f} gain={gr:.4f} q={qr:.4f}")
else:
    fx.set_param(pq_track, 0, 0, 0.5)
    val = fx.get_param(pq_track, 0, 0)
    ok = abs(val - 0.5) < 0.01
    print(f"  Band1 not found, fallback: param[0]=0.5, read={val:.4f}")

results.append(("P4.3 set/get round-trip", ok))
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.4: set_enabled A/B bypass (ReaEQ) ───────────────
print("\n--- P4.4: set_enabled (A/B bypass) ---")
chain = fx.get_chain(eq_track)
print(f"  Initial enabled: {chain[0]['enabled']}")

fx.set_enabled(eq_track, 0, False)
chain = fx.get_chain(eq_track)
bypassed = not chain[0]["enabled"]
print(f"  After bypass: enabled={chain[0]['enabled']}")

fx.set_enabled(eq_track, 0, True)
chain = fx.get_chain(eq_track)
re_enabled = chain[0]["enabled"]
print(f"  After re-enable: enabled={chain[0]['enabled']}")

ok = bypassed and re_enabled
results.append(("P4.4 set_enabled A/B bypass", ok))
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.5: remove FX ─────────────────────────────────────
print("\n--- P4.5: remove FX ---")
chain_before = fx.get_chain(eq_track)
n_before = len(chain_before)
print(f"  Chain before remove: {n_before} FX")
fx.remove(eq_track, 0)
chain_after = fx.get_chain(eq_track)
n_after = len(chain_after)
print(f"  Chain after remove: {n_after} FX")
ok = n_before > 0 and n_after == n_before - 1
results.append(("P4.5 remove FX", ok))
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.6: get_param_list completeness ───────────────────
print("\n--- P4.6: get_param_list completeness ---")
params = fx.get_param_list(pq_track, 0)
ok = (
    len(params) > 10
    and all("name" in p and "value" in p for p in params)
    and all(isinstance(p["value"], float) for p in params)
)
results.append(("P4.6 get_param_list", ok))
print(f"  Param count: {len(params)}")
if params:
    print(f"  First: {params[0]}")
    print(f"  Last: {params[-1]}")
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.7: Send create + get_info ────────────────────────
print("\n--- P4.7: Send create + set_level + get_info ---")
rv_fx_idx = fx.add(rv_track, "ReaVerbate")
print(f"  Reverb return FX index: {rv_fx_idx}")

send_result = send_mgr.create(src=eq_track, dest=rv_track, level_db=-8.0, mode="post-fader")
print(f"  Send create result: {send_result}")
ok_create = send_result["index"] >= 0

info = send_mgr.get_info(eq_track, send_result["index"])
print(f"  get_info: {info}")
ok_info = (
    info is not None
    and abs(info.get("volume_norm", -999) - 10 ** (-8.0 / 20)) < 0.02
)
results.append(("P4.7 send create + get_info", ok_create and ok_info))
print(f"  {'PASS' if (ok_create and ok_info) else 'FAIL'}")


# ── P4.8: Send level + mute ─────────────────────────────
print("\n--- P4.8: Send level + mute ---")
send_mgr.set_level(eq_track, send_result["index"], -12.0)
info2 = send_mgr.get_info(eq_track, send_result["index"])
expected_norm = 10 ** (-12.0 / 20)
actual_norm = info2.get("volume_norm", -999) if info2 else -999
print(f"  Set: -12.0 dB -> norm={expected_norm:.4f}, actual={actual_norm:.4f}")
ok_level = abs(actual_norm - expected_norm) < 0.02

send_mgr.set_mute(eq_track, send_result["index"], True)
info_muted = send_mgr.get_info(eq_track, send_result["index"])
ok_mute = info_muted and info_muted.get("mute") is True
print(f"  Muted: {info_muted.get('mute') if info_muted else 'N/A'}")

send_mgr.set_mute(eq_track, send_result["index"], False)
info_unmuted = send_mgr.get_info(eq_track, send_result["index"])
ok_unmute = info_unmuted and info_unmuted.get("mute") is False
print(f"  Unmuted: {info_unmuted.get('mute') if info_unmuted else 'N/A'}")

ok = ok_level and ok_mute and ok_unmute
results.append(("P4.8 send level + mute", ok))
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.9: list_all sends ────────────────────────────────
print("\n--- P4.9: list_all sends ---")
all_sends = send_mgr.list_all(eq_track)
ok = len(all_sends) >= 1
results.append(("P4.9 list_all sends", ok))
print(f"  Sends from track {eq_track}: {all_sends}")
print(f"  {'PASS' if ok else 'FAIL'}")


# ── P4.10: get_param_name ───────────────────────────────
print("\n--- P4.10: get_param_name (Pro-Q 3) ---")
name0 = fx.get_param_name(pq_track, 0, 0)
name1 = fx.get_param_name(pq_track, 0, 1)
print(f"  Param 0: '{name0}'")
print(f"  Param 1: '{name1}'")
ok = isinstance(name0, str) and len(name0) > 0
results.append(("P4.10 get_param_name", ok))
print(f"  {'PASS' if ok else 'FAIL'}")


# ── Summary ─────────────────────────────────────────────
print("\n" + "=" * 60)
print("PHASE 4 VERIFICATION RESULTS")
print("=" * 60)
passed = sum(1 for _, ok in results if ok)
failed = sum(1 for _, ok in results if not ok)
for name, ok in results:
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
print(f"\n  Total: {passed} passed, {failed} failed, {len(results)} total")
if failed == 0:
    print("\n  *** PHASE 4: PASS ***")
else:
    print(f"\n  *** PHASE 4: FAIL ({failed} checks failed) ***")
