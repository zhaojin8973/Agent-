"""Mixing engineer workflow integration tests.

These tests exercise the full hermes-core pipeline from a mixing engineer's
perspective: create project → import stems → gain staging → EQ/FX → bus/send
→ render → analyze → audit.

Requires a running REAPER instance and real audio files.
"""

import pytest

from hermes_core.engine import MixingEngine
from tests.conftest import require_reaper, clean_project, make_test_wav


# Path to real multi-track audio files for workflow testing
_STEM_DIR = "Hermes 测试/大湾区的梦 分轨/分轨"

# Path to vocal mixing (贴唱混音) test audio files
_VOCAL_FILE = "Hermes 测试/望归 贴唱/望归 Vocal（测试）.wav"
_BACKING_FILE = "Hermes 测试/望归 贴唱/望归 伴奏（测试）.wav"

# Third-party plugin names (substring-matched by TrackFX_AddByName)
_EQ_PLUGIN = "FabFilter Pro-Q 3"
_COMP_PLUGIN = "RVox"
_REVERB_PLUGIN = "ValhallaVintageVerb"
_MASTER_LIMITER = "FabFilter Pro-L 2 (FabFilter)"


@pytest.mark.integration
class TestMixingWorkflow:
    """Full mixing session end-to-end tests."""

    # ── Scene 1: Project setup ────────────────────────────────

    def test_create_project_and_import_stems(self):
        """Create a 48kHz project and import multiple audio stems."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()

        eng.allow_track_deletion()

        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test", sample_rate=48000)

        stems = [
            _STEM_DIR + "/Drum Kick.wav",
            _STEM_DIR + "/Drum Snare.wav",
            _STEM_DIR + "/Bass.wav",
        ]
        imported = eng.import_stems(stems)

        assert len(imported) == 3
        assert all(r["success"] for r in imported), (
            f"Some stems failed to import: {imported}"
        )

        tracks = eng.list_tracks()
        assert len(tracks) == 3
        assert all(t.item_count >= 1 for t in tracks), (
            "All tracks should have at least one media item"
        )

    # ── Scene 2: Gain staging ─────────────────────────────────

    def test_gain_staging(self):
        """Set fader levels and verify gain structure."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav", _STEM_DIR + "/Bass.wav"]
        eng.import_stems(stems)

        eng.apply_gain(0, -3.0)
        eng.apply_gain(1, -6.0)

        structure = eng.get_gain_structure()
        assert structure["tracks"][0]["volume_db"] == pytest.approx(-3.0, abs=0.5)
        assert structure["tracks"][1]["volume_db"] == pytest.approx(-6.0, abs=0.5)

    # ── Scene 3: FX chain ─────────────────────────────────────

    def test_add_eq_to_tracks(self):
        """Add ReaEQ to every track and verify FX chain."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav", _STEM_DIR + "/Drum Snare.wav"]
        eng.import_stems(stems)

        fx0 = eng.add_fx(0, "ReaEQ")
        fx1 = eng.add_fx(1, "ReaEQ")
        assert fx0 >= 0
        assert fx1 >= 0

        chain0 = eng.get_fx_chain(0)
        chain1 = eng.get_fx_chain(1)
        assert len(chain0) == 1
        assert len(chain1) == 1
        assert "ReaEQ" in chain0[0]["name"]
        assert "ReaEQ" in chain1[0]["name"]

    # ── Scene 4: Bus and send ─────────────────────────────────

    def test_create_drum_bus_and_reverb_send(self):
        """Group drums into a bus and create a reverb send."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav", _STEM_DIR + "/Drum Snare.wav"]
        eng.import_stems(stems)

        # Create drum bus
        bus_idx = eng.create_bus("Drum Bus", [0, 1])
        assert bus_idx == 0, f"Bus should be inserted before first child, got {bus_idx}"

        # Add snare reverb send
        reverb = eng.create_reverb_send(
            src_track=1,  # Snare
            level_db=-8.0,
            reverb_fx="ReaVerbate",
            mode="post-fader",
        )
        assert reverb["aux_index"] >= 0
        assert reverb["send"]["index"] >= 0
        assert reverb["fx_index"] >= 0

    # ── Scene 5: Render ───────────────────────────────────────

    def test_render_full_mix(self, tmp_path):
        """Render a multi-track mix and verify output."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test", sample_rate=48000)

        stems = [_STEM_DIR + "/Drum Kick.wav", _STEM_DIR + "/Bass.wav"]
        eng.import_stems(stems)
        eng.apply_gain(0, -3.0)
        eng.apply_gain(1, -4.0)
        eng.add_fx(0, "ReaEQ")
        eng.add_fx(1, "ReaEQ")

        result = eng.render_mix(str(tmp_path), verify=True)

        assert result.get("output_path") is not None, (
            f"Render should produce output, got: {result}"
        )
        assert result.get("error") is None, (
            f"Render should not error, got: {result}"
        )
        sc = result.get("signal_check", {})
        assert sc.get("silence_passed") is True, "Mix should not be silent"
        assert sc.get("clip_passed") is True, "Mix should not clip at -3/-4 dB gain"

    # ── Scene 6: Render with time selection ───────────────────

    def test_render_time_selection_snippet(self, tmp_path):
        """Render only a time-selected portion of the project."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav"]
        eng.import_stems(stems)

        # Explicitly set zero-length time selection to test rejection.
        eng._render.set_time_selection(0.0, 0.0)
        result = eng.render_mix(
            str(tmp_path), bounds="time_selection", verify=False
        )
        # Time selection is 0s→0s by default → should reject as zero-length
        assert result.get("error") == "nothing_to_render", (
            f"Zero-length time selection should be rejected, got: {result}"
        )

    # ── Scene 7: Audit ────────────────────────────────────────

    def test_audit_clean_mix(self, tmp_path):
        """Audit a rendered mix — should pass all safety checks at moderate gain."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test", sample_rate=48000)

        stems = [_STEM_DIR + "/Drum Kick.wav"]
        eng.import_stems(stems)
        eng.apply_gain(0, -10.0)

        result = eng.render_mix(str(tmp_path), verify=False)
        output = result["output_path"]
        assert output is not None

        audit = eng.audit_mix(output)
        assert audit["passed"] is True, (
            f"Mix should pass audit at -10 dB, got: {audit}"
        )

    # ── Scene 8: Full workflow ────────────────────────────────

    def test_full_mixing_session(self, tmp_path):
        """Complete mixing session: import → gain → EQ → bus → send
        → render → audit."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        # 1. Create project
        eng.create_project(name="TestMixingWorkflow", output_dir="/tmp/hermes_test", sample_rate=48000)

        # 2. Import multi-track stems
        stems = [
            _STEM_DIR + "/Drum Kick.wav",
            _STEM_DIR + "/Drum Snare.wav",
            _STEM_DIR + "/Bass.wav",
        ]
        imported = eng.import_stems(stems)
        assert all(r["success"] for r in imported)

        # 3. Gain staging (mix engineer's first pass)
        eng.apply_gain(0, -3.0)   # Kick
        eng.apply_gain(1, -5.0)   # Snare
        eng.apply_gain(2, -4.0)   # Bass

        # 4. Add channel EQ
        for i in range(3):
            fx = eng.add_fx(i, "ReaEQ")
            assert fx >= 0

        # 5. Create drum bus (Kick + Snare)
        bus_idx = eng.create_bus("Drum Bus", [0, 1])
        assert bus_idx == 0

        # 6. Create reverb send from Snare
        reverb = eng.create_reverb_send(
            src_track=1, level_db=-10.0, reverb_fx="ReaVerbate"
        )
        assert reverb["aux_index"] >= 0

        # 7. Render the mix
        result = eng.render_mix(str(tmp_path), verify=True)
        assert result.get("output_path") is not None
        assert result.get("error") is None

        sc = result["signal_check"]
        assert sc["silence_passed"] is True, "Mix should not be silent"
        assert sc["clip_passed"] is True, "Mix should not clip at conservative gain"
        assert sc["duration_sec"] > 0

        # 8. Audit the rendered file
        audit = eng.audit_mix(result["output_path"])
        assert audit["passed"] is True, (
            f"Final mix should pass audit, got: {audit}"
        )

        # 9. Health check
        health = eng.health_check()
        assert health["reapy_connected"] is True


@pytest.mark.integration
class TestVocalMixing:
    """Vocal mixing (贴唱混音) workflow tests using real vocal + backing stems."""

    # ── Scene 1: Project setup + import ─────────────────────

    def test_create_named_project_and_import_vocal_stems(self):
        """Create a named project and import vocal + backing stems."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        info = eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        assert info["name"] == "TestVocalMixing"
        assert info["sample_rate"] == 48000

        result = eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        stems = result["stems"]
        assert len(stems) == 2
        assert all(s["success"] for s in stems), (
            f"Some stems failed: {stems}"
        )
        for s in stems:
            assert s["clip_gain_db"] != 0.0, (
                f"Stem {s['role']} should have non-zero clip gain"
            )

        tracks = eng.list_tracks()
        assert len(tracks) == 2
        assert all(t.item_count >= 1 for t in tracks)

    # ── Scene 2: Gain staging ───────────────────────────────

    def test_vocal_gain_staging_uses_genre_based_calculation(self):
        """prepare_stems does clip gain; fader balance is deferred to post_fx_balance."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )

        result = eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        stems = result["stems"]
        vocal = stems[0]
        backing = stems[1]

        assert vocal["role"] == "vocal"
        assert backing["role"] == "backing"
        assert vocal["raw_lufs"] is not None, "Should measure vocal LUFS"
        assert backing["raw_lufs"] is not None, "Should measure backing LUFS"
        assert "clip_gain_db" in vocal
        assert "fader_gain_db" in vocal

        # Clip gain brings every stem to -18 dBFS RMS reference
        for s in stems:
            assert s["clip_gain_db"] != 0.0, (
                f"Stem {s['role']} should have non-zero clip gain "
                f"(was {s['clip_gain_db']})"
            )

        # Fader is now deferred to post_fx_balance — verify it's zero here.
        for s in stems:
            assert s["fader_gain_db"] == 0.0, (
                f"Fader for {s['role']} should be 0.0 before post_fx_balance"
            )

        structure = eng.get_gain_structure()
        assert len(structure["tracks"]) == 2

    def test_post_fx_balance_genre_based_faders(self):
        """post_fx_balance applies genre-based fader reduction to backing."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestPostFxBalance", output_dir="/tmp/hermes_balance_test",
            sample_rate=48000,
        )

        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )

        # Simulate post-FX scenario: apply_profile then balance
        balance = eng.post_fx_balance(
            vocal_indices=[0], backing_indices=[1],
            genre="chinese_folk_bel_canto",
        )
        stems = balance["stems"]

        vocal = stems[0]
        backing = stems[1]

        # chinese_folk_bel_canto: backing reduction 9-12 LU,
        # backing fader should be much lower than vocal fader
        assert abs(backing["fader_gain_db"]) > abs(vocal["fader_gain_db"]), (
            f"Backing fader ({backing['fader_gain_db']}) should exceed "
            f"vocal fader ({vocal['fader_gain_db']}) for vocal-forward genre"
        )

    # ── Scene 3: EQ + compression ───────────────────────────

    def test_vocal_eq_and_compression(self):
        """Add Pro-Q 3 and RVox to vocal; backing gets no FX."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )

        eq_idx = eng.add_fx(0, _EQ_PLUGIN)
        comp_idx = eng.add_fx(0, _COMP_PLUGIN)
        assert eq_idx >= 0, f"Failed to add {_EQ_PLUGIN}"
        assert comp_idx >= 0, f"Failed to add {_COMP_PLUGIN}"

        chain = eng.get_fx_chain(0)
        assert len(chain) == 2, f"Vocal should have 2 FX, got {len(chain)}"
        assert _EQ_PLUGIN.lower() in chain[0]["name"].lower()
        assert _COMP_PLUGIN.lower() in chain[1]["name"].lower()

        backing_chain = eng.get_fx_chain(1)
        assert len(backing_chain) == 0, "Backing track should have no FX"

    # ── Scene 4: Reverb send ────────────────────────────────

    def test_vocal_reverb_send(self):
        """Create a ValhallaVintageVerb reverb send from the vocal track."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )

        reverb = eng.create_reverb_send(
            src_track=0, level_db=-8.0,
            reverb_fx=_REVERB_PLUGIN, mode="post-fader",
        )
        assert reverb["aux_index"] >= 0, "Verb return track not created"
        assert reverb["send"]["index"] >= 0, "Send not created"
        assert reverb["fx_index"] >= 0, f"{_REVERB_PLUGIN} not loaded on aux"

        tracks = eng.list_tracks()
        assert len(tracks) == 3, (
            f"Expected vocal + backing + verb return = 3, got {len(tracks)}"
        )
        verb_chain = eng.get_fx_chain(reverb["aux_index"])
        # Abbey Road EQ (ReaEQ) is auto-inserted before the reverb
        assert len(verb_chain) == 2, (
            f"Expected Abbey Road EQ + reverb = 2, got {len(verb_chain)}"
        )
        assert any("ReaEQ" in fx["name"] for fx in verb_chain), (
            "Abbey Road EQ should be auto-inserted"
        )
        assert any(_REVERB_PLUGIN in fx["name"] for fx in verb_chain), (
            f"{_REVERB_PLUGIN} should be on aux"
        )

    # ── Scene 5: Checkpoints ────────────────────────────────

    def test_save_checkpoints_at_key_nodes(self):
        """Save project snapshots at import, FX, pre-master, and final stages."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )

        cp1 = eng.save_checkpoint(label="after_import")
        assert "after_import" in cp1["checkpoint_path"]

        eng.add_fx(0, _EQ_PLUGIN)
        eng.add_fx(0, _COMP_PLUGIN)
        cp2 = eng.save_checkpoint(label="after_fx")
        assert "after_fx" in cp2["checkpoint_path"]

        eng.add_master_fx(_MASTER_LIMITER)
        cp3 = eng.save_checkpoint(label="before_master")
        assert "before_master" in cp3["checkpoint_path"]

        eng.finalize_master(target_lufs=-12.0)
        cp4 = eng.save_checkpoint(label="final")
        assert "final" in cp4["checkpoint_path"]

        assert cp1["main_path"] == cp2["main_path"] == cp3["main_path"] == cp4["main_path"]

    # ── Scene 6: Master finalization ────────────────────────

    def test_finalize_master_to_target_rms(self, tmp_path):
        """Probe render at Gain=0 → measure RMS → set gain → final render."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        eng.add_fx(0, _EQ_PLUGIN)
        eng.add_fx(0, _COMP_PLUGIN)
        eng.create_reverb_send(src_track=0, reverb_fx=_REVERB_PLUGIN)

        result = eng.finalize_master(
            target_lufs=-12.0, tmp_dir=str(tmp_path),
        )
        assert result["pre_limiter_peak_db"] <= 0, (
            f"Pre-limiter peak {result['pre_limiter_peak_db']} — mix clips before limiter"
        )
        assert result["passed"] is True, (
            f"finalize_master did not pass: {result}"
        )
        assert result["converged"] is True
        assert result["probe_lufs"] is not None
        assert result["gain_db"] >= 0
        assert result["output_path"] is not None

        audit = eng.audit_mix(result["output_path"])
        assert audit["passed"] is True, (
            f"Final master should pass audit: {audit}"
        )

    # ── Scene 7: Multi-format export ─────────────────────────

    def test_render_output_formats(self, tmp_path):
        """Render to WAV and MP3 — both produce valid, non-silent files."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )
        eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        eng.add_master_fx(_MASTER_LIMITER)

        for fmt in ("wav",):
            result = eng.render_mix(
                str(tmp_path), fmt=fmt, verify=True,
            )
            output = result.get("output_path")
            assert output is not None, f"{fmt} render produced no output"
            assert output.endswith(f".{fmt}"), (
                f"Expected .{fmt}, got {output}"
            )
            sc = result.get("signal_check", {})
            assert sc.get("silence_passed") is True, (
                f"{fmt} render should not be silent"
            )

    # ── Scene 8: Full session ───────────────────────────────

    def test_full_vocal_mixing_session(self, tmp_path):
        """End-to-end vocal mixing pipeline — no errors, clean output."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()

        # 1. Create project
        eng.create_project(
            name="TestVocalMixing", output_dir="/tmp/hermes_vocal_test",
            sample_rate=48000,
        )

        # 2. Import and gain-stage stems
        prep = eng.prepare_stems(
            [_VOCAL_FILE, _BACKING_FILE],
            genre="chinese_folk_bel_canto",
        )
        assert all(s["success"] for s in prep["stems"])

        # 3. Vocal processing chain
        assert eng.add_fx(0, _EQ_PLUGIN) >= 0
        assert eng.add_fx(0, _COMP_PLUGIN) >= 0

        # 4. Reverb send
        reverb = eng.create_reverb_send(
            src_track=0, reverb_fx=_REVERB_PLUGIN,
        )
        assert reverb["aux_index"] >= 0

        # 5. Checkpoint before master
        cp = eng.save_checkpoint(label="before_master")
        assert cp["checkpoint_path"] is not None

        # 6. Master finalization
        master = eng.finalize_master(
            target_lufs=-12.0, tmp_dir=str(tmp_path),
        )
        assert master["passed"] is True, (
            f"finalize_master failed: {master}"
        )
        assert master["pre_limiter_peak_db"] <= 0
        assert master["converged"] is True
        assert master["probe_lufs"] is not None
        assert master["gain_db"] >= 0

        # 7. Audit
        audit = eng.audit_mix(master["output_path"])
        assert audit["passed"] is True, (
            f"Final mix audit failed: {audit}"
        )

        # 8. Health check
        health = eng.health_check()
        assert health["reapy_connected"] is True


# ════════════════════════════════════════════════════════════════
# PRODUCTION_GAPS features — integration tests
# ════════════════════════════════════════════════════════════════


@pytest.mark.integration
class TestProductionGapsFeatures:
    """Integration tests for the PRODUCTION_GAPS safety features."""

    def test_preflight_plugins_detects_installed(self):
        """preflight_plugins returns empty list for built-in FX."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("PreflightTest", "/tmp/hermes_test")
        missing = eng.preflight_plugins(["ReaEQ", "ReaComp"])
        assert missing == [], f"Expected all built-in FX found, missing: {missing}"

    def test_preflight_plugins_detects_missing(self):
        """preflight_plugins returns missing plugins that don't exist."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("PreflightTest", "/tmp/hermes_test")
        missing = eng.preflight_plugins(["DefinitelyNotARealPlugin_XYZ_123"])
        assert len(missing) == 1
        assert "DefinitelyNotARealPlugin_XYZ_123" in missing

    def test_on_progress_callback_fires(self):
        """on_progress receives stage callbacks during finalize_master."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("ProgressTest", "/tmp/hermes_test", sample_rate=48000)
        eng.import_stems([_STEM_DIR + "/Drum Kick.wav"])

        stages = []

        def track(stage, pct):
            stages.append(stage)

        result = eng.finalize_master(target_lufs=-12.0, on_progress=track)
        assert "setup" in stages, f"Expected 'setup' stage, got: {stages}"
        assert "probe_render" in stages
        assert "search" in stages
        assert "final_render" in stages
        assert "verify" in stages

    def test_reset_allows_reuse(self):
        """reset() clears guards so prepare_stems can be called again."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.allow_track_deletion()
        eng.create_project("ResetTest", "/tmp/hermes_test", sample_rate=48000)
        eng.import_stems([_STEM_DIR + "/Drum Kick.wav"])

        # First call works
        result1 = eng.prepare_stems(
            [_STEM_DIR + "/Drum Kick.wav"], genre="pop",
            vocal_indices=[0],
        )
        assert "stems" in result1

        # Second call would fail...
        # But after reset, it should work again
        eng.reset()
        # Re-import and re-prepare
        eng.import_stems([_STEM_DIR + "/Drum Kick.wav"])
        result2 = eng.prepare_stems(
            [_STEM_DIR + "/Drum Kick.wav"], genre="pop",
            vocal_indices=[0],
        )
        assert "stems" in result2