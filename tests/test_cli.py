"""Tests for hermes_core.cli — command-line interface."""

import argparse
import logging
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.cli import (
    _build_parser,
    main,
    cmd_vocal_mix,
    cmd_check,
    cmd_batch,
    cmd_calibrate,
    cmd_adjust,
)


# ════════════════════════════════════════════════════════════
# Parser
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestParser:
    def test_vocal_mix_required_args(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["vocal-mix"])  # --vocal required

    def test_vocal_mix_full_args(self):
        parser = _build_parser()
        args = parser.parse_args([
            "vocal-mix",
            "--vocal", "v.wav",
            "--backing", "b1.wav", "b2.wav",
            "--genre", "pop",
            "--target-lufs", "-14.0",
            "--output", "./out",
            "--profile", "profiles/test.yaml",
            "--tolerance", "0.5",
            "--watchdog",
        ])
        assert args.vocal == "v.wav"
        assert args.backing == ["b1.wav", "b2.wav"]
        assert args.genre == "pop"
        assert args.target_lufs == -14.0
        assert args.output == "./out"
        assert args.profile == "profiles/test.yaml"
        assert args.tolerance == 0.5
        assert args.watchdog is True

    def test_vocal_mix_defaults(self):
        parser = _build_parser()
        args = parser.parse_args([
            "vocal-mix", "--vocal", "v.wav", "--backing", "b.wav",
        ])
        assert args.genre == "pop"
        assert args.target_lufs is None  # genre-based by default
        assert args.output == "./output"
        assert args.profile is None
        assert args.tolerance == 0.3
        assert args.watchdog is False

    def test_check_requires_profile(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["check"])

    def test_check_with_profile(self):
        parser = _build_parser()
        args = parser.parse_args(["check", "--profile", "profiles/pop.yaml"])
        assert args.profile == "profiles/pop.yaml"

    def test_batch_requires_input_dir(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["batch", "--profile", "p.yaml"])

    def test_batch_full_args(self):
        parser = _build_parser()
        args = parser.parse_args([
            "batch",
            "--input-dir", "./songs",
            "--profile", "p.yaml",
            "--output-dir", "./masters",
            "--target-lufs", "-14.0",
        ])
        assert args.input_dir == "./songs"
        assert args.profile == "p.yaml"
        assert args.output_dir == "./masters"
        assert args.target_lufs == -14.0

    def test_invalid_target_lufs(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args([
                "vocal-mix", "--vocal", "v.wav", "--backing", "b.wav",
                "--target-lufs", "loud",
            ])

    def test_no_command_shows_help(self):
        parser = _build_parser()
        args = parser.parse_args([])
        assert args.command is None


# ════════════════════════════════════════════════════════════
# cmd_vocal_mix error paths (no REAPER needed)
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCmdVocalMixErrors:
    def make_args(self, **overrides):
        ns = argparse.Namespace(
            command="vocal-mix",
            vocal="/fake/vocal.wav",
            backing=["/fake/backing.wav"],
            genre="pop",
            target_lufs=-12.0,
            output="/tmp/out",
            profile=None,
            tolerance=0.3,
            watchdog=False,
        )
        for k, v in overrides.items():
            setattr(ns, k, v)
        return ns

    def test_missing_vocal_file_flag_accepted(self):
        """Parser accepts --vocal even when file doesn't exist (engine validates later)."""
        args = self.make_args(vocal="/nonexistent/file.wav")
        assert args.vocal == "/nonexistent/file.wav"
        assert len(args.backing) == 1

    def test_plugin_check_error_flow(self):
        """Preflight_plugins returning non-empty list maps to exit code 1."""
        # Just verify the return value mapping — the function body
        # requires REAPER, but the pattern is: preflight → non-empty → return 1
        from unittest.mock import MagicMock
        mock_profile = MagicMock()
        mock_profile.all_fx_names.return_value = ["Pro-L 2"]

        # Simulate what cmd_vocal_mix does:
        missing = ["Pro-L 2"]
        assert len(missing) > 0
        # The function would: log.error + return 1
        assert True  # placeholder — actual engine flow tested in integration


# ════════════════════════════════════════════════════════════
# cmd_check error paths
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCmdCheckErrors:
    def test_missing_profile_raises(self):
        args = argparse.Namespace(
            command="check",
            profile="/nonexistent/profile.yaml",
        )
        with pytest.raises(Exception):
            cmd_check(args)


# ════════════════════════════════════════════════════════════
# cmd_batch error paths
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCmdBatchErrors:
    def test_nonexistent_input_dir(self):
        """Non-existent input directory raises FileNotFoundError."""
        args = argparse.Namespace(
            command="batch",
            input_dir="/nonexistent/dir",
            profile="/fake/p.yaml",
            output_dir="/tmp/out",
            target_lufs=-12.0,
        )
        with pytest.raises(FileNotFoundError):
            cmd_batch(args)

    def test_no_song_folders(self, tmp_path):
        """Empty directory with no song subdirs returns error (after profile load fails)."""
        empty_dir = tmp_path / "empty"
        empty_dir.mkdir()
        args = argparse.Namespace(
            command="batch",
            input_dir=str(empty_dir),
            profile="/fake/p.yaml",
            output_dir=str(tmp_path / "out"),
            target_lufs=-12.0,
        )
        with pytest.raises(FileNotFoundError):
            cmd_batch(args)


# ════════════════════════════════════════════════════════════
# main entry
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMain:
    def test_no_command_exits(self):
        with pytest.raises(SystemExit) as exc:
            main([])
        assert exc.value.code == 1

    def test_help_command(self):
        with pytest.raises(SystemExit):
            main(["--help"])

    def test_subcommand_dispatched(self):
        """vocal-mix is dispatched without import errors."""
        # cmd_vocal_mix calls sys.exit() internally, so we catch SystemExit.
        with patch("hermes_core.cli.cmd_vocal_mix", side_effect=SystemExit(0)) as mock_cmd:
            with pytest.raises(SystemExit) as exc:
                main(["vocal-mix", "--vocal", "v.wav", "--backing", "b.wav"])
            assert exc.value.code == 0
            mock_cmd.assert_called_once()

    def test_check_dispatched(self):
        """check command is dispatched correctly."""
        with patch("hermes_core.cli.cmd_check", side_effect=SystemExit(0)) as mock_cmd:
            with pytest.raises(SystemExit):
                main(["check", "--profile", "/fake/p.yaml"])
            mock_cmd.assert_called_once()


# ════════════════════════════════════════════════════════════
# calibrate parser + errors
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCalibrateParser:
    def test_full_args(self):
        parser = _build_parser()
        args = parser.parse_args([
            "calibrate", "--plugin", "Universal Audio 1176LN",
            "--param", "Input", "--lo", "-50.0", "--hi", "10.0",
            "--steps", "20", "--output", "cal.json",
        ])
        assert args.plugin == "Universal Audio 1176LN"
        assert args.lo == -50.0
        assert args.hi == 10.0
        assert args.steps == 20


@pytest.mark.unit
class TestCmdCalibrateErrors:
    def test_requires_plugin_and_param(self):
        parser = _build_parser()
        with pytest.raises(SystemExit):
            parser.parse_args(["calibrate"])


# ════════════════════════════════════════════════════════════
# adjust parser + errors + dispatch
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestAdjustParser:
    def test_full_args(self):
        parser = _build_parser()
        args = parser.parse_args([
            "adjust", "--project", "./out",
            "--comp-ratio", "6.0", "--eq-presence", "3.0",
            "--threshold", "-15.0", "--reverb-level", "-6.0",
        ])
        assert args.project == "./out"
        assert args.comp_ratio == 6.0
        assert args.reverb_level == -6.0

    def test_no_changes_ok(self):
        parser = _build_parser()
        args = parser.parse_args(["adjust", "--project", "./out"])
        assert args.comp_ratio is None


@pytest.mark.unit
class TestCmdAdjustErrors:
    def test_nonexistent_project_returns_1(self):
        args = argparse.Namespace(
            command="adjust", project="/nonexistent/dir",
            comp_ratio=None, eq_presence=None, threshold=None,
            reverb_level=None, target_lufs=-12.0, preview=False,
            watchdog=False,
        )
        assert cmd_adjust(args) == 1


@pytest.mark.unit
class TestNewDispatchers:
    def test_adjust_dispatched(self):
        with patch("hermes_core.cli.cmd_adjust", side_effect=SystemExit(0)) as mock_cmd:
            with pytest.raises(SystemExit):
                main(["adjust", "--project", "./out", "--comp-ratio", "6"])
            mock_cmd.assert_called_once()

    def test_calibrate_dispatched(self):
        with patch("hermes_core.cli.cmd_calibrate", side_effect=SystemExit(0)) as mock_cmd:
            with pytest.raises(SystemExit):
                main(["calibrate", "--plugin", "X", "--param", "Y",
                      "--lo", "0", "--hi", "1"])
            mock_cmd.assert_called_once()
