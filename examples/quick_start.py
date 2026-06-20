#!/usr/bin/env python3
"""Hermes-Core quick-start example — one-shot vocal mix with a YAML profile.

Run from the project root::

    python examples/quick_start.py

Prerequisites:
    - REAPER 7.73+ running
    - Python 3.11 + ``pip install -e .``
    - Required plugins installed (see profiles/vocal_pop.yaml)
"""

import logging
from hermes_core import MixingEngine
from hermes_core.profiles import MixingProfile

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("quick-start")

# ── configuration ────────────────────────────────────────────────

VOCAL_WAV = "./望归_Vocal.wav"
BACKING_WAV = "./望归_Backing.wav"
OUTPUT_DIR = "./output"
GENRE = "chinese_folk_bel_canto"
TARGET_LUFS = -12.0

# ── main ─────────────────────────────────────────────────────────


def main():
    # Load mixing profile from YAML
    profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")
    log.info("Profile: %s", profile.name)

    stem_paths = [VOCAL_WAV, BACKING_WAV]
    project_name = "望归"

    with MixingEngine(watchdog=True) as eng:
        # 1. Create project
        eng.create_project(project_name, OUTPUT_DIR, sample_rate=48000)
        log.info("Project created: %s", eng._project_path)

        # 2. Preflight — check all required plugins exist
        missing = eng.preflight_plugins(profile.all_fx_names())
        if missing:
            log.error("Missing plugins: %s", ", ".join(missing))
            return
        log.info("All %d plugins available", len(profile.all_fx_names()))

        # 3. Import + gain stage (clip gain to -18 dBFS RMS + genre fader)
        result = eng.prepare_stems(
            stem_paths,
            genre=GENRE,
            vocal_indices=[0],
            backing_indices=[1],
        )
        for s in result["stems"]:
            log.info(
                "  %s (%s): clip=%+.1f dB, fader=%+.1f dB",
                s["track_name"], s["role"],
                s["clip_gain_db"], s["fader_gain_db"],
            )

        # 4. Apply FX chain from profile
        eng.apply_profile(profile, vocal_track=0, backing_tracks=[1])

        # 5. Master + render
        final = eng.finalize_master(target_lufs=TARGET_LUFS)
        if final["passed"]:
            log.info(
                "✓ Mix complete — %s | %.1f LUFS | gain %+.1f dB",
                final["output_path"],
                final["achieved_lufs"],
                final["gain_db"],
            )
        else:
            log.error("✗ Finalize failed: %s", final.get("error", "unknown"))


if __name__ == "__main__":
    main()
