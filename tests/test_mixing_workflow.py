"""Mixing engineer workflow integration tests.

These tests exercise the full hermes-core pipeline from a mixing engineer's
perspective: create project → import stems → gain staging → EQ/FX → bus/send
→ render → analyze → normalize → audit.

Requires a running REAPER instance and real audio files.
"""

import pytest

from hermes_core.engine import MixingEngine
from tests.conftest import require_reaper, clean_project, make_test_wav


# Path to real multi-track audio files for workflow testing
_STEM_DIR = "Hermes 测试/大湾区的梦 分轨/分轨"


@pytest.mark.integration
class TestMixingWorkflow:
    """Full mixing session end-to-end tests."""

    # ── Scene 1: Project setup ────────────────────────────────

    def test_create_project_and_import_stems(self):
        """Create a 48kHz project and import multiple audio stems."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()

        eng.create_project(name="Test", output_dir="/tmp/hermes_test", sample_rate=48000)

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test", sample_rate=48000)

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav"]
        eng.import_stems(stems)

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
        eng.create_project(name="Test", output_dir="/tmp/hermes_test", sample_rate=48000)

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

    # ── Scene 8: Normalize ────────────────────────────────────

    def test_normalize_track_to_target_lufs(self):
        """Normalize a single track to -14 LUFS."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav"]
        eng.import_stems(stems)

        result = eng.normalize_track(0, target_lufs=-14.0, duration=3.0)
        assert result.success is True, (
            f"Normalization should succeed, got: {result}"
        )
        assert result.original_lufs != 0.0, "Should measure actual LUFS"
        assert result.gain_applied_db != 0.0, "Should apply gain correction"

    def test_normalize_all_tracks(self):
        """Batch-normalize all tracks."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()
        eng.create_project(name="Test", output_dir="/tmp/hermes_test")

        stems = [_STEM_DIR + "/Drum Kick.wav", _STEM_DIR + "/Bass.wav"]
        eng.import_stems(stems)

        results = eng.normalize_all(target_lufs=-14.0, duration=3.0)
        assert len(results) == 2
        assert all(r.success for r in results), (
            f"All tracks should normalize successfully, got: {results}"
        )

    # ── Scene 9: Full workflow ────────────────────────────────

    def test_full_mixing_session(self, tmp_path):
        """Complete mixing session: import → gain → EQ → bus → send
        → render → audit → normalize."""
        require_reaper()
        eng = MixingEngine()
        eng._bridge.connect()

        # 1. Create project
        eng.create_project(name="Test", output_dir="/tmp/hermes_test", sample_rate=48000)

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

        # 9. Normalize all tracks to -14 LUFS
        norm_results = eng.normalize_all(target_lufs=-14.0, duration=5.0)
        assert len(norm_results) >= 3  # 3 source + 1 aux
        for r in norm_results:
            if r.track_name in ("Kick", "Snare", "Bass"):
                assert r.success, f"Track {r.track_name} normalize failed"

        # 10. Health check
        health = eng.health_check()
        assert health["reapy_connected"] is True