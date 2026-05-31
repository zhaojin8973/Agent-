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
    p.add_argument("--bpm", type=float, default=None, help="Project BPM for tempo-synced compression")
    p.add_argument("--midi", type=Path, default=None, help="MIDI file to extract tempo from")


def _build_check_parser(subparsers) -> None:
    p = subparsers.add_parser("check", help="Check plugins are installed")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")


def _build_batch_parser(subparsers) -> None:
    p = subparsers.add_parser("batch", help="Batch process a directory of songs")
    p.add_argument("--input-dir", required=True, help="Directory of song folders")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")
    p.add_argument("--output-dir", default="./masters", help="Output directory")
    p.add_argument("--target-lufs", type=float, default=-12.0)
    p.add_argument("--bpm", type=float, default=None, help="Project BPM for tempo-synced compression")
    p.add_argument("--midi", type=Path, default=None, help="MIDI file to extract tempo from")


def _build_calibrate_parser(subparsers) -> None:
    p = subparsers.add_parser("calibrate", help="Auto-calibrate compressor knob curves")
    p.add_argument("--plugin", required=True, help="REAPER FX name to calibrate")
    p.add_argument("--param", required=True, help="Parameter name to sweep (e.g. 'Input')")
    p.add_argument("--lo", type=float, required=True, help="Physical value at knob=0.0")
    p.add_argument("--hi", type=float, required=True, help="Physical value at knob=1.0")
    p.add_argument("--steps", type=int, default=10, help="Measurement points (default 10)")
    p.add_argument("--signal", default=None, help="Test WAV path (auto-generated if omitted)")
    p.add_argument("--output", "-o", default=None, help="Save calibration JSON to file")
    p.add_argument("--watchdog", action="store_true", help="Enable DialogKiller")


def _build_adjust_parser(subparsers) -> None:
    p = subparsers.add_parser("adjust", help="Modify mix params with dirty-flag cascade")
    p.add_argument("--project", required=True, help="Project output directory")
    p.add_argument("--comp-ratio", type=float, default=None, help="New compressor ratio")
    p.add_argument("--eq-presence", type=float, default=None, help="EQ presence gain (dB)")
    p.add_argument("--threshold", type=float, default=None, help="Compressor threshold (dB)")
    p.add_argument("--reverb-level", type=float, default=None, help="Reverb send level (dB)")
    p.add_argument("--target-lufs", type=float, default=-12.0)
    p.add_argument("--preview", action="store_true", help="Fast preview (numpy mix, no Pro-L 2)")
    p.add_argument("--watchdog", action="store_true", help="Enable DialogKiller")


def _resolve_bpm(args) -> float | None:
    """Resolve BPM from CLI args: --bpm takes priority, then --midi.

    Returns ``None`` when neither is provided (engine falls back to
    static genre presets).
    """
    if args.bpm is not None:
        return float(args.bpm)
    if args.midi is not None:
        from hermes_core.midi_tempo import read_midi_tempo, MidiTempoError
        try:
            bpm = read_midi_tempo(args.midi)
            log.info("Extracted %.1f BPM from %s", bpm, args.midi)
            return bpm
        except MidiTempoError as exc:
            log.warning("MIDI tempo extraction failed (%s), falling back to genre defaults", exc)
    return None


def cmd_vocal_mix(args) -> int:
    """Run a one-shot vocal mix."""
    from hermes_core import MixingEngine
    from hermes_core.profiles import MixingProfile

    # Build stem list
    stem_paths = [args.vocal] + list(args.backing)
    vocal_indices = [0]
    backing_indices = list(range(1, len(stem_paths)))

    project_name = os.path.splitext(os.path.basename(args.vocal))[0]
    resolved_bpm = _resolve_bpm(args)

    with MixingEngine(watchdog=args.watchdog) as eng:
        log.info("Creating project: %s", project_name)
        eng.create_project(project_name, args.output, sample_rate=48000)

        # Plugin preflight
        if args.profile:
            profile = MixingProfile.from_yaml(args.profile)
        else:
            from hermes_core.profiles import get_default_vocal_chain
            profile = MixingProfile(
                vocal_chain=get_default_vocal_chain(),
                backing_chain=[],
            )
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
            bpm=resolved_bpm,
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
        )

        # Apply FX chain from profile (EQ baseline + auto-compression + reverb)
        eng.apply_profile(profile, vocal_track=0, backing_tracks=backing_indices,
                          genre=args.genre, bpm=resolved_bpm)

        # Post-FX fader balance
        log.info("Measuring post-FX loudness and balancing faders...")
        balance = eng.post_fx_balance(
            vocal_indices=vocal_indices,
            backing_indices=backing_indices,
            genre=args.genre,
        )
        log.info(
            "Balance: vocal=%.1f LUFS, backing=%.1f LUFS, combined=%.1f LUFS",
            balance.get("vocal_lufs", float("nan")),
            balance.get("backing_lufs", float("nan")),
            balance.get("combined_lufs", float("nan")),
        )

        # Master
        log.info("Finalizing master (target: %.1f LUFS)...", args.target_lufs)
        result = eng.finalize_master(
            target_lufs=args.target_lufs,
            limiter_fx=profile.master_limiter.name if profile else
                       "VST: FabFilter Pro-L 2 (FabFilter)",
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
    resolved_bpm = _resolve_bpm(args)

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
                eng.prepare_stems(stem_paths, genre="pop", bpm=resolved_bpm)
                eng.apply_profile(profile, genre="pop", bpm=resolved_bpm)
                eng.post_fx_balance()
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


def cmd_calibrate(args) -> int:
    """Auto-calibrate a compressor parameter's knob curve."""
    import json
    from hermes_core import MixingEngine

    log.info("Calibrating %s.%s ...", args.plugin, args.param)
    with MixingEngine(watchdog=args.watchdog) as eng:
        table = eng.calibrate_compressor(
            plugin_name=args.plugin,
            param_name=args.param,
            param_range=(args.lo, args.hi),
            steps=args.steps,
            test_signal_path=args.signal,
        )
    if args.output:
        out = {
            "plugin": args.plugin,
            "param": args.param,
            "range": [args.lo, args.hi],
            "steps": args.steps,
            "table": table,
        }
        with open(args.output, "w") as f:
            json.dump(out, f, indent=2)
        log.info("Calibration saved to %s", args.output)
    else:
        for norm, phys in table:
            print(f"  ({norm:.2f}, {phys:.1f})")
    return 0


def cmd_adjust(args) -> int:
    """Modify FX parameters on an existing mix and re-render.

    Loads the project from --project, applies parameter changes via
    update_node_param (dirty-flag cascade), re-runs post_fx_balance
    and finalize_master.
    """
    from hermes_core import MixingEngine
    from hermes_core.profiles import MixingProfile

    if not os.path.isdir(args.project):
        log.error("Project directory not found: %s", args.project)
        return 1

    with MixingEngine(watchdog=args.watchdog) as eng:
        # ── Discover existing project ──
        log.info("Loading project from %s ...", args.project)
        # For adjust, we re-create the state from the project dir
        # The engine auto-detects existing RPP files and cached stems

        # ── Apply parameter changes via dirty-flag cascade ──
        changes_applied = 0
        for node in (eng._vocal_chain_nodes + eng._backing_chain_nodes):
            if args.comp_ratio is not None and node.fx_type == "comp":
                eng.update_node_param(node, "Ratio", args.comp_ratio)
                changes_applied += 1
            if args.eq_presence is not None and node.fx_type == "eq":
                eng.update_node_param(node, "Band 2 Gain", args.eq_presence)
                changes_applied += 1
            if args.threshold is not None and node.fx_type == "comp":
                eng.update_node_param(node, "Threshold", args.threshold)
                changes_applied += 1

        if args.reverb_level is not None and eng._reverb_send_node:
            eng.update_node_param(
                eng._reverb_send_node, "level_db", args.reverb_level,
            )
            changes_applied += 1

        if changes_applied == 0:
            log.warning("No changes specified — use --comp-ratio, --eq-presence, "
                        "--threshold, or --reverb-level")
            return 1

        log.info("Applied %d parameter change(s) — dirty cascade triggered",
                 changes_applied)

        # ── Re-run balance + render ──
        log.info("Re-balancing post-FX faders ...")
        balance = eng.post_fx_balance()
        log.info(
            "Balance: vocal=%.1f LUFS, backing=%.1f LUFS",
            balance.get("vocal_lufs", float("nan")),
            balance.get("backing_lufs", float("nan")),
        )

        if args.preview:
            log.info("Preview render (numpy mix, no Pro-L 2) ...")
            result = eng.render_preview(
                output_dir=args.project,
                target_lufs=args.target_lufs,
            )
            if result.get("output_path"):
                log.info("✓ Preview — %s | ~%.1f LUFS (bypassed mastering)",
                         result["output_path"], result.get("estimated_lufs", 0.0))
            else:
                log.error("✗ Preview failed — %s", result.get("error", "unknown"))
                return 1
        else:
            log.info("Finalizing master (target: %.1f LUFS) ...",
                     args.target_lufs)
            result = eng.finalize_master(target_lufs=args.target_lufs)
            if result.get("passed"):
                log.info("✓ Finalized — %s | %.1f LUFS",
                         result["output_path"], result["achieved_lufs"])
            else:
                log.error("✗ Failed — %s", result.get("error", "unknown"))
                return 1

    return 0


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
    _build_calibrate_parser(subparsers)
    _build_adjust_parser(subparsers)
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
    elif args.command == "calibrate":
        sys.exit(cmd_calibrate(args))
    elif args.command == "adjust":
        sys.exit(cmd_adjust(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
