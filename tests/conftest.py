"""Shared fixtures and helpers for hermes-core tests."""

import numpy as np
import pytest
import struct
import wave

from hermes_core.bridge import ReaperBridge


def require_reaper():
    """Connect to REAPER or skip the current test.

    Usage:
        require_reaper()
    """
    bridge = ReaperBridge()
    if not bridge.connect():
        pytest.skip("REAPER is not running -- skipping integration test")


def make_test_wav(filepath, duration_sec=1.0, sample_rate=48000, frequency=440.0):
    """Generate a mono 16-bit WAV file with a sine wave. Returns filepath."""
    n = int(sample_rate * duration_sec)
    t = np.arange(n) / sample_rate
    signal = 0.5 * np.sin(2.0 * np.pi * frequency * t)
    i16 = (signal * 32767.0).astype(np.int16)
    with wave.open(str(filepath), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(i16.tobytes())
    return filepath


def clean_project(bridge):
    """Delete all tracks from the current REAPER project."""
    api = bridge.api
    n = api.CountTracks(0)
    for i in range(n - 1, -1, -1):
        tr = api.GetTrack(0, i)
        if tr:
            api.DeleteTrack(tr)
    return api.CountTracks(0)
