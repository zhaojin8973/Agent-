#!/usr/bin/env python3
"""
Phase 0: REAPER Plugin Diagnostic Suite (ARM64 reapy-based)
===========================================================
Uses reapy high-level API for all checks that fail on ARM64 RPR.
Tests: FabFilter Pro-Q 3, Pro-L 2, ValhallaVintageVerb, +
       Ozone 11 Maximizer, Soundtoys Decapitator, Gullfoss
Waves plugins excluded — they use WaveShell and AddByName hangs.
"""

import json
import sys
import time
from dataclasses import dataclass, field

# ── Plugin registry (only installed + confirmed non-hanging) ──

PLUGINS = [
    {
        "short": "Pro-Q3",
        "name": "FabFilter Pro-Q 3",
        "vendor": "FabFilter",
        "type": "EQ",
        "critical_params": ["Band 1 Frequency", "Band 1 Gain", "Band 1 Q", "Output Gain"],
    },
    {
        "short": "Pro-L2",
        "name": "FabFilter Pro-L 2",
        "vendor": "FabFilter",
        "type": "Limiter",
        "critical_params": ["Gain", "Output", "Lookahead", "Channel Link"],
    },
    {
        "short": "VintageVerb",
        "name": "ValhallaVintageVerb",
        "vendor": "Valhalla",
        "type": "Reverb",
        "critical_params": ["Mix", "Decay", "Predelay", "Size"],
    },
    {
        "short": "Ozone11Max",
        "name": "Ozone 11 Maximizer",
        "vendor": "iZotope",
        "type": "Limiter",
        "critical_params": ["Threshold", "Ceiling", "Character"],
    },
    {
        "short": "Decapitator",
        "name": "Decapitator",
        "vendor": "Soundtoys",
        "type": "Saturation",
        "critical_params": ["Drive", "Output", "Tone"],
    },
    {
        "short": "Gullfoss",
        "name": "Gullfoss",
        "vendor": "Soundtheory",
        "type": "EQ",
        "critical_params": ["Recover", "Tame", "Bias", "Brighten"],
    },
]


@dataclass
class Result:
    short: str = ""
    name: str = ""
    found: bool = False
    add_via_rpr: int = -1
    add_via_reapy: int = -1
    param_count: int = 0
    param_names: list[str] = field(default_factory=list)
    round_trips: list[dict] = field(default_factory=list)
    all_rt_pass: bool = False
    errors: list[str] = field(default_factory=list)


def run_all():
    import reapy

    reapy.connect()
    api = reapy.reascript_api
    proj = reapy.Project()

    if api.CountTracks(0) == 0:
        api.InsertTrackAtIndex(0, True)

    results = []
    for p in PLUGINS:
        r = Result(short=p["short"], name=p["name"])

        # 1. Add via RPR
        track_ptr = api.GetTrack(0, 0)
        n_before = api.TrackFX_GetCount(track_ptr)
        rpr_idx = api.TrackFX_AddByName(track_ptr, p["name"], False, 1)
        n_after_rpr = api.TrackFX_GetCount(track_ptr)

        # 2. Verify via reapy (ARM64-safe exists check)
        reapy_track = proj.tracks[0]
        if rpr_idx >= 0 and n_after_rpr > n_before:
            r.add_via_rpr = rpr_idx
            try:
                fx_name = reapy_track.fxs[rpr_idx].name
                r.found = bool(fx_name and fx_name.strip() and fx_name.strip() != "(0)")
            except Exception:
                r.found = False
        else:
            r.found = False

        # 3. Cleanup RPR attempt, try reapy add_fx
        if not r.found and rpr_idx >= 0:
            api.TrackFX_Delete(track_ptr, rpr_idx)

        if not r.found:
            try:
                reapy_track.add_fx(p["name"])
                n_after_reapy = api.TrackFX_GetCount(track_ptr)
                if n_after_reapy > n_before:
                    reapy_idx = n_after_reapy - 1
                    fx_name = reapy_track.fxs[reapy_idx].name
                    if fx_name and fx_name.strip() and fx_name.strip() != "(0)":
                        r.add_via_reapy = reapy_idx
                        r.found = True
                        rpr_idx = reapy_idx
            except Exception as e:
                r.errors.append(f"reapy add_fx failed: {e}")

        if not r.found:
            r.errors.append("Plugin not found via RPR or reapy")
            results.append(r)
            continue

        # 4. Param enumeration via reapy
        try:
            fx = reapy_track.fxs[rpr_idx]
            r.param_count = fx.n_params
            r.param_names = [fx.params[i].name for i in range(min(fx.n_params, 20))]
        except Exception as e:
            r.errors.append(f"Param enum failed: {e}")

        # 5. Round-trip test via reapy params[]
        try:
            fx = reapy_track.fxs[rpr_idx]
            for label in p["critical_params"]:
                pi = _find_param(r.param_names, label)
                if pi < 0:
                    r.round_trips.append({"label": label, "idx": -1, "match": False, "error": "not found"})
                    continue
                try:
                    fx.params[pi] = 0.5
                    time.sleep(0.02)
                    back = float(fx.params[pi])
                    ok = abs(back - 0.5) < 0.01
                    r.round_trips.append({"label": label, "idx": pi, "set": 0.5, "got": round(back, 4), "match": ok})
                except Exception as e:
                    r.round_trips.append({"label": label, "idx": pi, "match": False, "error": str(e)})
            r.all_rt_pass = all(rt["match"] for rt in r.round_trips)
        except Exception as e:
            r.errors.append(f"Round-trip failed: {e}")

        # 6. Cleanup
        api.TrackFX_Delete(track_ptr, rpr_idx)
        results.append(r)

    summary = {
        "total": len(PLUGINS),
        "found": sum(1 for r in results if r.found),
        "all_rt_pass": sum(1 for r in results if r.all_rt_pass),
    }
    return {"summary": summary, "results": [vars(r) for r in results]}


def _find_param(names, label):
    ll = label.lower()
    for i, n in enumerate(names):
        if ll in n.lower():
            return i
    return -1


if __name__ == "__main__":
    print("=" * 60)
    print("PHASE 0: PLUGIN DIAGNOSTICS (ARM64 reapy-based)")
    print("=" * 60)
    report = run_all()
    s = report["summary"]
    print(f"\nSUMMARY: {s['found']}/{s['total']} found, {s['all_rt_pass']}/{s['total']} all round-trips pass")

    for r in report["results"]:
        status = "PASS" if r["all_rt_pass"] else ("FOUND" if r["found"] else "MISS")
        print(f"\n  [{status}] {r['short']} ({r['name']})")
        print(f"    RPR add: {r['add_via_rpr']}, reapy add: {r['add_via_reapy']}, params: {r['param_count']}")
        for rt in r["round_trips"]:
            m = "OK" if rt["match"] else "FAIL"
            print(f"    {m:4s} {rt['label']:20s} set=0.5 got={rt.get('got')}")
        for e in r["errors"]:
            print(f"    ERR: {e}")

    with open("phase0_diagnostic_report.json", "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nReport saved to phase0_diagnostic_report.json")
