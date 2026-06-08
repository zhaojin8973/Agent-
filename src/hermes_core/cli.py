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
    p.add_argument("--target-lufs", type=float, default=None, help="Target integrated LUFS (default: genre-based)")
    p.add_argument("--output", "-o", default="./output", help="Output directory")
    p.add_argument("--profile", "-p", default=None, help="Path to YAML mixing profile")
    p.add_argument("--tolerance", type=float, default=0.3, help="LUFS tolerance")
    p.add_argument("--watchdog", action="store_true", help="Enable DialogKiller")
    p.add_argument("--bpm", type=float, default=None, help="Project BPM for tempo-synced compression")
    p.add_argument("--midi", type=Path, default=None, help="MIDI file to extract tempo from")
    p.add_argument("--gender", default="", choices=["", "male", "female"],
                   help="歌手性别（默认 female）")
    p.add_argument("--technique", default="",
                   help="演唱方式（pop/rock/folk/bel_canto/chinese_folk_bel_canto）")


def _build_check_parser(subparsers) -> None:
    p = subparsers.add_parser("check", help="Check plugins are installed")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")


def _build_batch_parser(subparsers) -> None:
    p = subparsers.add_parser("batch", help="Batch process a directory of songs")
    p.add_argument("--input-dir", required=True, help="Directory of song folders")
    p.add_argument("--profile", "-p", required=True, help="Path to YAML mixing profile")
    p.add_argument("--output-dir", default="./masters", help="Output directory")
    p.add_argument("--target-lufs", type=float, default=None)
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
    p.add_argument("--genre", default="pop", help="Genre key (pop, folk, rock, electronic, ballad, chinese_folk_bel_canto)")
    p.add_argument("--comp-ratio", type=float, default=None, help="New compressor ratio")
    p.add_argument("--eq-presence", type=float, default=None, help="EQ presence gain (dB)")
    p.add_argument("--threshold", type=float, default=None, help="Compressor threshold (dB)")
    p.add_argument("--reverb-level", type=float, default=None, help="Reverb send level (dB)")
    p.add_argument("--target-lufs", type=float, default=None)
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
                          genre=args.genre, bpm=resolved_bpm,
                          gender=getattr(args, "gender", ""),
                          technique=getattr(args, "technique", ""))

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

        # Bus compressor (master track — user handles TGP + Pro-L 2 manually)
        log.info("Applying bus compressor ...")
        bus_result = eng.apply_bus_compressor(
            bpm=resolved_bpm,
            genre=args.genre,
        )
        if bus_result.get("error"):
            log.warning("Bus compressor: %s", bus_result["error"])
        log.info(
            "Bus comp: peak=%.1f dB → thresh=%.1f dB, attack=%.1f ms, "
            "makeup=%.1f dB, target GR=%.1f dB",
            bus_result["peak_db"],
            bus_result["thresh_db"],
            bus_result["attack_ms"],
            bus_result["makeup_db"],
            bus_result["gr_target"],
        )

        log.info("✓ Pipeline complete — master fader untouched for manual mastering")

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
                from hermes_core.mastering import _get_genre_target_lufs
                target_lufs = args.target_lufs if args.target_lufs is not None else _get_genre_target_lufs("pop")
                result = eng.finalize_master(target_lufs=target_lufs)
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

        from hermes_core.mastering import _get_genre_target_lufs
        genre = getattr(args, "genre", "pop")
        target_lufs = args.target_lufs if args.target_lufs is not None else _get_genre_target_lufs(genre)

        if args.preview:
            log.info("Preview render (numpy mix, no Pro-L 2) ...")
            result = eng.render_preview(
                output_dir=args.project,
                target_lufs=target_lufs,
            )
            if result.get("output_path"):
                log.info("✓ Preview — %s | ~%.1f LUFS (bypassed mastering)",
                         result["output_path"], result.get("estimated_lufs", 0.0))
            else:
                log.error("✗ Preview failed — %s", result.get("error", "unknown"))
                return 1
        else:
            log.info("Finalizing master (target: %.1f LUFS) ...",
                     target_lufs)
            result = eng.finalize_master(target_lufs=target_lufs)
            if result.get("passed"):
                log.info("✓ Finalized — %s | %.1f LUFS",
                         result["output_path"], result["achieved_lufs"])
            else:
                log.error("✗ Failed — %s", result.get("error", "unknown"))
                return 1

    return 0


# ── parser ───────────────────────────────────────────────────────


# ── project 子命令 ─────────────────────────────────────────────

def _build_project_parser(subparsers):
    p = subparsers.add_parser("project", help="工程管理")
    sp = p.add_subparsers(dest="project_action")

    # project list
    lp = sp.add_parser("list", help="列出所有工程")
    lp.add_argument("--genre", default=None, help="按流派筛选")
    lp.add_argument("--stage", default=None, help="按管线阶段筛选")
    lp.add_argument("--category", default=None, help="按分类筛选")

    # project info
    ip = sp.add_parser("info", help="显示工程详情")
    ip.add_argument("name", help="工程名称（支持模糊匹配）")

    # project create
    cp = sp.add_parser("create", help="创建新工程")
    cp.add_argument("name", help="工程名称")
    cp.add_argument("--category", default="", help="分类目录")
    cp.add_argument("--producer", default="", help="制作人")
    cp.add_argument("--genre", default="pop", help="流派")

    # project scan
    sp.add_parser("scan", help="重新扫描工程索引")

    # project status
    sp.add_parser("status", help="显示当前工程的生命周期和管线状态").add_argument(
        "name", nargs="?", default="", help="工程名（留空则显示 REAPER 当前工程）")

    # project close
    sp.add_parser("close", help="保存并关闭当前工程（不弹窗）")


def cmd_project(args):
    from hermes_core.config import HermesConfig
    from hermes_core.project_meta import ProjectIndex, ProjectMeta
    from hermes_core.engine import MixingEngine

    cfg = HermesConfig.load()
    root = cfg.project_root_expanded

    if args.project_action == "list":
        idx = ProjectIndex.load(root)
        if not idx.projects:
            print("没有找到工程。用 hermes project scan 扫描一下？")
            return 0
        items = idx.filter_by(genre=args.genre, stage=args.stage,
                              category=args.category)
        if not items:
            items = idx.list_all()
        print(f"{'工程名':<20} {'流派':<8} {'阶段':<12} {'分类':<16} {'修改时间':<12}")
        print("-" * 68)
        for rel_path, entry in items:
            name = entry.get("name", "")[:18]
            genre = entry.get("genre", "")[:6]
            stage = entry.get("stage", "")[:10]
            cat = entry.get("category", "")[:14]
            mod = entry.get("last_modified", "")[:10]
            print(f"{name:<20} {genre:<8} {stage:<12} {cat:<16} {mod:<12}")
        print(f"\n共 {len(items)} 个工程")

    elif args.project_action == "info":
        idx = ProjectIndex.load(root)
        found = idx.find(args.name)
        if not found:
            print(f"找不到工程 '{args.name}'")
            return 1
        for rel_path, entry in found:
            meta = ProjectMeta.load(os.path.join(root, rel_path))
            if meta:
                print(meta.summary())
                print(f"\n路径: {os.path.join(root, rel_path)}")
            else:
                print(f"{entry['name']}: 元数据文件缺失")
            print()

    elif args.project_action == "create":
        eng = MixingEngine()
        if not eng._bridge.connect():
            print("REAPER 未运行，无法创建工程")
            return 1
        eng.allow_track_deletion()
        info = eng.create_project(
            args.name, category=args.category,
            producer=args.producer, genre=args.genre,
        )
        print(f"工程已创建: {info['name']}")
        print(f"路径: {info['meta_dir']}")
        if info.get("conflict_renamed"):
            print(f"注意: 同名工程已存在，已重命名为 {info['path']}")

    elif args.project_action == "scan":
        idx = ProjectIndex()
        n = idx.scan(root)
        print(f"扫描完成: 找到 {n} 个工程")

    elif args.project_action == "status":
        if args.name:
            # 按名称查
            idx = ProjectIndex.load(root)
            found = idx.find(args.name)
            if not found:
                print(f"找不到工程 '{args.name}'")
                return 1
            for rel_path, entry in found:
                meta = ProjectMeta.load(os.path.join(root, rel_path))
                if meta:
                    meta.update_lifecycle()
                    print(f"{meta.name}: 管线={meta.pipeline_stage or '未开始'}, "
                          f"生命周期={meta.lifecycle_state}")
        else:
            # 显示 REAPER 当前工程状态
            eng = MixingEngine()
            if not eng._bridge.connect():
                print("REAPER 未运行")
                return 1
            info = eng.get_project_info()
            print(f"当前工程: {info['name']}")
            print(f"路径: {info['path']}")
            print(f"轨道: {info['track_count']}")
            if eng._meta:
                eng._meta.update_lifecycle()
                print(f"管线阶段: {eng._meta.pipeline_stage or '未开始'}")
                print(f"生命周期: {eng._meta.lifecycle_state}")

    elif args.project_action == "close":
        eng = MixingEngine()
        if not eng._bridge.connect():
            print("REAPER 未运行")
            return 1
        info = eng.get_project_info()
        print(f"关闭工程: {info['name']}")
        result = eng.close_project(save=True)
        print(f"已保存: {result['saved']}")
        print("工程已关闭（无弹窗）")

    else:
        print("用法: hermes project {list|info|create|scan|status|close}")
        return 1
    return 0


# ── config 子命令 ─────────────────────────────────────────────

def _build_config_parser(subparsers):
    p = subparsers.add_parser("config", help="全局配置管理")
    sp = p.add_subparsers(dest="config_action")

    sp.add_parser("show", help="显示当前配置")

    sp_cfg = sp.add_parser("set", help="设置配置项")
    sp_cfg.add_argument("key", help="配置键名")
    sp_cfg.add_argument("value", help="配置值")


def cmd_config(args):
    from hermes_core.config import HermesConfig

    if args.config_action == "show":
        cfg = HermesConfig.load()
        print(cfg.show())
    elif args.config_action == "set":
        cfg = HermesConfig.load()
        try:
            # 尝试类型转换
            if args.value.lower() in ("true", "false"):
                val = args.value.lower() == "true"
            elif args.value.isdigit():
                val = int(args.value)
            else:
                val = args.value
            cfg.set(args.key, val)
            print(f"已设置 {args.key} = {val}")
        except KeyError as e:
            print(f"错误: {e}")
            print(f"可用配置项: project_root, default_sample_rate, "
                  f"default_genre, auto_save_prompt")
            return 1
    else:
        print("用法: hermes config {show|set}")
        return 1
    return 0


# ── preflight 子命令 ──────────────────────────────────────────

def cmd_preflight(args):
    from hermes_core.engine import MixingEngine
    eng = MixingEngine()
    if not eng._bridge.connect():
        print("REAPER 未运行，无法检查插件")
        return 1
    plugins = args.plugin if args.plugin else None
    result = eng.preflight_plugins(required=plugins)
    ok = sum(1 for v in result.values() if v)
    total = len(result)
    print(f"\n插件检查: {ok}/{total} 可用")
    for name, available in sorted(result.items()):
        status = "✓" if available else "✗ 缺失"
        print(f"  {status}  {name}")
    if ok < total:
        print(f"\n⚠ {total - ok} 个插件缺失")
        return 1
    return 0


# ── parser 构建 ───────────────────────────────────────────────

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
    _build_project_parser(subparsers)
    _build_config_parser(subparsers)

    # preflight
    p = subparsers.add_parser("preflight", help="检查空间插件可用性")
    p.add_argument("--plugin", nargs="*", help="指定检查的插件（默认全部空间插件）")

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
    elif args.command == "project":
        sys.exit(cmd_project(args))
    elif args.command == "config":
        sys.exit(cmd_config(args))
    elif args.command == "preflight":
        sys.exit(cmd_preflight(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
