"""Tests for hermes_core.midi_tempo — pure-Python MIDI tempo parsing.

These tests run without REAPER.  They verify Standard MIDI File
tempo extraction including edge cases.

Usage:
    PYTHONPATH=src python3 -m pytest tests/test_midi_tempo.py -v
"""

import struct
from pathlib import Path

import pytest

from hermes_core.midi_tempo import read_midi_tempo, MidiTempoError


# ══════════════════════════════════════════════════════════════
# Test helpers
# ══════════════════════════════════════════════════════════════

_TMP = Path(__file__).resolve().parent / "_test_midi_tmp"


def _write_midi(filepath: Path, tempo_us_per_quarter: int = 500000,
                num_tracks: int = 1) -> None:
    """Write a minimal Standard MIDI File (format 0 or 1) with one tempo event.

    SMF structure:
        Header:  "MThd" + len(6) + format(2) + ntrks(2) + division(2)
        Track:   "MTrk" + len(...) + events
                  00 FF 51 03 tt tt tt  →  Set Tempo (microseconds per quarter)

    Default 500000 µs = 120 BPM.
    """
    tempo_bytes = struct.pack(">I", tempo_us_per_quarter)[1:]  # 3 bytes

    # Track chunk: delta=0, FF 51 03 <3 bytes tempo>
    track_events = bytes([0x00, 0xFF, 0x51, 0x03]) + tempo_bytes
    # End of track: delta=0, FF 2F 00
    track_events += bytes([0x00, 0xFF, 0x2F, 0x00])

    tracks_data = b""
    for _ in range(num_tracks):
        track_header = b"MTrk" + struct.pack(">I", len(track_events))
        tracks_data += track_header + track_events

    header = (
        b"MThd" +
        struct.pack(">I", 6) +           # header length
        struct.pack(">H", 0 if num_tracks == 1 else 1) +  # format
        struct.pack(">H", num_tracks) +  # ntrks
        struct.pack(">H", 480)           # division (ticks per quarter)
    )

    _TMP.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(header + tracks_data)


def _corrupt_midi(filepath: Path) -> None:
    """Write a file that looks like MIDI but has a truncated tempo event."""
    header = (
        b"MThd" +
        struct.pack(">I", 6) +
        struct.pack(">H", 0) +
        struct.pack(">H", 1) +
        struct.pack(">H", 480)
    )
    # Track with truncated Set Tempo (FF 51 03 but only 2 data bytes)
    track_events = bytes([0x00, 0xFF, 0x51, 0x03, 0x07, 0xA1])  # missing 3rd byte
    track_header = b"MTrk" + struct.pack(">I", len(track_events))
    _TMP.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(header + track_header + track_events)


def _midi_no_tempo(filepath: Path) -> None:
    """Write a valid MIDI file with no Set Tempo event."""
    # Just an End of Track event
    track_events = bytes([0x00, 0xFF, 0x2F, 0x00])
    track_header = b"MTrk" + struct.pack(">I", len(track_events))
    header = (
        b"MThd" +
        struct.pack(">I", 6) +
        struct.pack(">H", 0) +
        struct.pack(">H", 1) +
        struct.pack(">H", 480)
    )
    _TMP.mkdir(parents=True, exist_ok=True)
    filepath.write_bytes(header + track_header + track_events)


# ══════════════════════════════════════════════════════════════
# Tests
# ══════════════════════════════════════════════════════════════

class TestReadMidiTempo:
    """Verify tempo extraction from valid SMF files."""

    def test_default_120_bpm(self):
        """500000 µs/quarter → 120 BPM."""
        path = _TMP / "test_120.mid"
        _write_midi(path, tempo_us_per_quarter=500000)
        bpm = read_midi_tempo(str(path))
        assert bpm == pytest.approx(120.0)

    def test_60_bpm(self):
        """1,000,000 µs/quarter → 60 BPM."""
        path = _TMP / "test_60.mid"
        _write_midi(path, tempo_us_per_quarter=1000000)
        bpm = read_midi_tempo(str(path))
        assert bpm == pytest.approx(60.0)

    def test_140_bpm(self):
        """~428,571 µs/quarter → 140 BPM."""
        path = _TMP / "test_140.mid"
        _write_midi(path, tempo_us_per_quarter=428571)
        bpm = read_midi_tempo(str(path))
        assert bpm == pytest.approx(140.0, abs=0.1)

    def test_200_bpm(self):
        """300,000 µs/quarter → 200 BPM (fast dance)."""
        path = _TMP / "test_200.mid"
        _write_midi(path, tempo_us_per_quarter=300000)
        bpm = read_midi_tempo(str(path))
        assert bpm == pytest.approx(200.0)

    def test_format_1_multiple_tracks(self):
        """Format 1 MIDI with 3 tracks — tempo in track 0."""
        path = _TMP / "test_format1.mid"
        _write_midi(path, tempo_us_per_quarter=480000, num_tracks=3)
        bpm = read_midi_tempo(str(path))
        # 60,000,000 / 480,000 = 125 BPM
        assert bpm == pytest.approx(125.0)


class TestMidiTempoErrors:
    """Verify error handling for invalid files."""

    def test_file_not_found(self):
        """Non-existent file raises MidiTempoError."""
        with pytest.raises(MidiTempoError, match="not found"):
            read_midi_tempo("/nonexistent/midi_file.mid")

    def test_empty_file(self):
        """Empty file raises MidiTempoError."""
        path = _TMP / "empty.mid"
        path.write_bytes(b"")
        with pytest.raises(MidiTempoError, match="not a valid MIDI file"):
            read_midi_tempo(str(path))

    def test_not_a_midi_file(self):
        """Arbitrary binary data raises MidiTempoError."""
        path = _TMP / "not_midi.bin"
        path.write_bytes(b"Hello, world! This is not MIDI.")
        with pytest.raises(MidiTempoError, match="not a valid MIDI file"):
            read_midi_tempo(str(path))

    def test_corrupted_tempo_event(self):
        """Truncated tempo event raises MidiTempoError."""
        path = _TMP / "corrupt.mid"
        _corrupt_midi(path)
        with pytest.raises(MidiTempoError, match="tempo"):
            read_midi_tempo(str(path))

    def test_no_tempo_event(self):
        """MIDI file without Set Tempo raises MidiTempoError."""
        path = _TMP / "no_tempo.mid"
        _midi_no_tempo(path)
        with pytest.raises(MidiTempoError, match="tempo"):
            read_midi_tempo(str(path))

    def test_pathlib_path(self):
        """Pathlib path should also work (not just str)."""
        path = _TMP / "pathlib_test.mid"
        _write_midi(path, tempo_us_per_quarter=500000)
        bpm = read_midi_tempo(path)
        assert bpm == pytest.approx(120.0)
