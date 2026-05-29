"""
Hermes CLI — one-command vocal mixing from the terminal.

Usage::

    hermes vocal-mix --vocal v.wav --backing b.wav --genre pop --output ./out
    hermes check --profile profiles/rock.yaml
"""

import argparse
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger("hermes")


def _build_vocal_mix_parser(subparsers) -> None:
    p = subparsers.add_parser("vocal-mix", help="One-shot vocal + backing mix")
    p.add_argument("--vocal", required=True, help="Path to vocal stem WAV")
    p.add_argument("--backing", required=True, nargs="+", help="Path(s) to backing stem(s)")
    p.add_argument("--genre", default="pop", help="Genre key (pop, folk, chinese_folk_bel_canto)")
    p.add_argument("--target-lufs", type=float, default=-12.0, help="Target integrated LUFS")
    p.add_argument("--output", "-o", default="./output", help="Output directory")
    p.add_argument("--profile", "-p", default=None, help="Path to YAML mixing profile")
    p.add_argument("--tolerance", type=float, default=0.3, help="LUFS tolerance")
    p.add_argument("--watchdog", action="store_true", help="Enable DialogKiller")


def _build_check_parser(subparsers) -> None:
    p = subparsers.add_parser("check", help="Check plugins are installed")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")


def _build_batch_parser(subparsers) -> None:
    p = subparsers.add_parser("batch", help="Batch process a directory of songs")
    p.add_argument("--input-dir", required=True, help="Directory of song folders")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")
    p.add_argument("--output-dir", default="./masters", help="Output directory")
    p.add_argument("--target-lufs", type=float, default=-12.0)


def cmd_vocal_mix(args) -> int:
    """Run a one-shot vocal mix."""
    from hermes_core import MixingEngine
    from hermes_core.profiles import MixingProfile

    # Build stem list
    stem_paths = [args.vocal] + list(args.backing)
    vocal_indices = [0]
    backing_indices = list(range(1, len(stem_paths)))

    project_name = os.path.splitext(os.path.basename(args.vocal))[0]

    with MixingEngine(watchdog=args.watchdog) as eng:
        log.info("Creating project: %s", project_name)
        eng.create_project(project_name, args.output, sample_rate=48000)

        # Plugin preflight
        if args.profile:
            profile = MixingProfile.from_yaml(args.profile)
        else:
            profile = MixingProfile()
        missing = eng.preflight_plugins(profile.all_fx_names())
        if missing:
            log.error("Missing plugins: %s", ", ".join(missing))
            log.error("Install the missing plugins or update your profile.")
            return 1
        log.info("All plugins available.")

        # Prepare stems
        log.info("Preparing stems (genre: %s)...", args.genre)
        eng.prepare_stems(
            stem_paths,
            genre=args.genre,
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
        )

        # Apply FX chain from profile
        eng.apply_profile(profile, vocal_track=0, backing_tracks=backing_indices)

        # Master
        log.info("Finalizing master (target: %.1f LUFS)...", args.target_lufs)
        result = eng.finalize_master(
            target_lufs=args.target_lufs,
            limiter_fx=profile.master_limiter.name if profile else
                       "FabFilter Pro-L 2 (FabFilter)",
            tolerance=args.tolerance,
        )

        if result.get("passed"):
            log.info(
                "✓ Done — %s | %.1f LUFS | gain %+.1f dB",
                result["output_path"],
                result["achieved_lufs"],
                result["gain_db"],
            )
        else:
            log.error(
                "✗ Failed — achieved %.1f LUFS (target %.1f)",
                result.get("achieved_lufs", float("nan")),
                args.target_lufs,
            )
            return 1

    return 0


def cmd_check(args) -> int:
    """Check whether required plugins are installed."""
    from hermes_core import MixingEngine
    from hermes_core.profiles import MixingProfile

    profile = MixingProfile.from_yaml(args.profile)
    print(f"Profile: {profile.name}")
    print(f"Plugins required: {', '.join(profile.all_fx_names())}")

    with MixingEngine(watchdog=False) as eng:
        missing = eng.preflight_plugins(profile.all_fx_names())
        if missing:
            print(f"✗ Missing: {', '.join(missing)}")
            return 1
        print("✓ All plugins available.")
        return 0


def cmd_batch(args) -> int:
    """Batch process multiple songs."""
    from hermes_core import MixingEngine
    from hermes_core.profiles import MixingProfile

    profile = MixingProfile.from_yaml(args.profile)
    input_dir = Path(args.input_dir)
    output_dir = Path(args.output_dir)

    if not input_dir.is_dir():
        log.error("Input directory not found: %s", input_dir)
        return 1

    songs = sorted(
        [d for d in input_dir.iterdir() if d.is_dir()],
        key=lambda p: p.name,
    )
    if not songs:
        log.error("No song folders found in %s", input_dir)
        return 1

    log.info("Found %d song(s)", len(songs))
    ok = 0

    for song_dir in songs:
        wavs = sorted(song_dir.glob("*.wav"))
        if not wavs:
            log.warning("Skip %s — no WAV files", song_dir.name)
            continue

        song_output = str(output_dir / song_dir.name)
        stem_paths = [str(w) for w in wavs]

        log.info("Processing: %s (%d stems)", song_dir.name, len(stem_paths))
        try:
            with MixingEngine(watchdog=True) as eng:
                eng.create_project(song_dir.name, song_output)
                eng.prepare_stems(stem_paths, genre="pop")
                eng.apply_profile(profile)
                result = eng.finalize_master(target_lufs=args.target_lufs)
                if result.get("passed"):
                    ok += 1
                    log.info("  ✓ %.1f LUFS", result["achieved_lufs"])
                else:
                    log.warning("  ✗ failed")
        except Exception as exc:
            log.error("  ✗ %s: %s", type(exc).__name__, exc)

    log.info("Done: %d/%d succeeded", ok, len(songs))
    return 0 if ok == len(songs) else 1


# ── parser ───────────────────────────────────────────────────────


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="hermes",
        description="REAPER DAW automation — lean 3-layer mixing engine",
    )
    subparsers = parser.add_subparsers(dest="command", help="Available commands")
    _build_vocal_mix_parser(subparsers)
    _build_check_parser(subparsers)
    _build_batch_parser(subparsers)
    return parser


def main(argv: list[str] | None = None) -> None:
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "vocal-mix":
        sys.exit(cmd_vocal_mix(args))
    elif args.command == "check":
        sys.exit(cmd_check(args))
    elif args.command == "batch":
        sys.exit(cmd_batch(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
