"""
Standard MIDI File tempo extraction — pure Python, zero dependencies.

Parses the Set Tempo meta event (``FF 51 03``) from a Standard MIDI
File and returns the BPM.  Only the first tempo event in the first
track is read; tempo changes mid-song are deliberately ignored.
"""

import struct
from pathlib import Path
from typing import Union


class MidiTempoError(Exception):
    """Raised when tempo cannot be extracted from a MIDI file."""


def read_midi_tempo(filepath: Union[str, Path]) -> float:
    """Extract BPM from a Standard MIDI File.

    Parses the SMF header and first track chunk to find the initial
    Set Tempo meta event (``FF 51 03``).  Tempo is computed as::

        BPM = 60,000,000 ÷ µs_per_quarter

    Only the **first** tempo event is used — subsequent tempo changes
    (common in orchestral / progressive music) are ignored.

    Args:
        filepath: Path to a ``.mid`` or ``.midi`` file.

    Returns:
        BPM as a ``float`` (e.g. ``120.0``, ``140.5``).

    Raises:
        MidiTempoError: If the file doesn't exist, isn't a valid SMF,
            or doesn't contain a Set Tempo event.
    """
    path = Path(filepath)

    if not path.exists():
        raise MidiTempoError(f"MIDI file not found: {filepath}")

    try:
        data = path.read_bytes()
    except OSError as exc:
        raise MidiTempoError(f"Cannot read MIDI file: {exc}") from exc

    if len(data) < 14:
        raise MidiTempoError(f"File too small — not a valid MIDI file ({len(data)} bytes)")

    # ── Parse header chunk ──────────────────────────────────────
    if data[:4] != b"MThd":
        raise MidiTempoError("not a valid MIDI file (missing MThd header)")

    header_len = struct.unpack_from(">I", data, 4)[0]
    if header_len < 6:
        raise MidiTempoError(f"Invalid MIDI header length: {header_len}")

    fmt = struct.unpack_from(">H", data, 8)[0]
    num_tracks = struct.unpack_from(">H", data, 10)[0]
    # division = struct.unpack_from(">H", data, 12)[0]  # not needed for tempo

    if fmt not in (0, 1, 2):
        raise MidiTempoError(f"Unsupported MIDI format: {fmt}")

    if num_tracks < 1:
        raise MidiTempoError("MIDI file has no tracks")

    # ── Find first track chunk ──────────────────────────────────
    pos = 8 + header_len  # skip header

    for _ in range(num_tracks):
        if pos + 8 > len(data):
            raise MidiTempoError("Unexpected end of MIDI file (missing track chunk)")

        if data[pos:pos + 4] != b"MTrk":
            raise MidiTempoError(f"Expected MTrk at offset {pos}, got {data[pos:pos+4]!r}")

        track_len = struct.unpack_from(">I", data, pos + 4)[0]
        track_start = pos + 8
        track_end = track_start + track_len

        if track_end > len(data):
            raise MidiTempoError("Track length exceeds file size")

        # ── Scan track events for Set Tempo ──────────────────
        bpm = _scan_track_for_tempo(data, track_start, track_end)
        if bpm is not None:
            return bpm

        pos = track_end

    raise MidiTempoError("No tempo event found in MIDI file")


def _scan_track_for_tempo(data: bytes, start: int, end: int) -> float | None:
    """Scan a track chunk for the first Set Tempo meta event.

    Returns BPM if found, ``None`` otherwise.
    """
    pos = start
    while pos < end:
        # ── Variable-length delta time ───────────────────────
        delta, pos = _read_varint(data, pos)
        if pos >= end:
            break

        status = data[pos]

        if status == 0xFF:
            # ── Meta event ───────────────────────────────────
            if pos + 2 >= end:
                break
            meta_type = data[pos + 1]
            if meta_type == 0x51:  # Set Tempo
                if pos + 5 >= end:
                    raise MidiTempoError("Truncated tempo event in MIDI file")
                tempo_us = (data[pos + 3] << 16) | (data[pos + 4] << 8) | data[pos + 5]
                if tempo_us <= 0:
                    raise MidiTempoError(f"Invalid tempo value: {tempo_us} µs/quarter")
                return 60_000_000.0 / tempo_us
            else:
                # Skip other meta events
                if pos + 2 >= end:
                    break
                length = data[pos + 2]
                pos += 3 + length
        elif status == 0xF0 or status == 0xF7:
            # ── SysEx event ──────────────────────────────────
            if pos + 1 >= end:
                break
            length, pos = _read_varint(data, pos + 1)
            pos += length
        elif status & 0x80:
            # ── MIDI voice event ─────────────────────────────
            event_len = _VOICE_EVENT_LENGTHS.get(status & 0xF0, 0)
            pos += event_len
        else:
            # Running status — shouldn't appear as first event,
            # but handle gracefully by skipping one byte.
            pos += 1

    return None


# Standard MIDI voice event lengths (status nibble → total bytes including status).
_VOICE_EVENT_LENGTHS: dict[int, int] = {
    0x80: 3,  # Note Off
    0x90: 3,  # Note On
    0xA0: 3,  # Polyphonic Key Pressure
    0xB0: 3,  # Control Change
    0xC0: 2,  # Program Change
    0xD0: 2,  # Channel Pressure
    0xE0: 3,  # Pitch Bend
}


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    """Read a MIDI variable-length integer starting at *pos*.

    Returns ``(value, new_position)``.
    """
    value = 0
    while pos < len(data):
        byte = data[pos]
        pos += 1
        value = (value << 7) | (byte & 0x7F)
        if not (byte & 0x80):
            break
    return value, pos
