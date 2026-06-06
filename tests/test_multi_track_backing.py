"""MultiTrackBackingProcessor 单元测试。"""
from unittest.mock import MagicMock
import pytest
from hermes_core.backing import MultiTrackBackingProcessor


class TestClassifyTracks:
    def test_drums(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks(["kick.wav", "snare.wav", "hihat.wav"])
        assert len(result["drums"]) == 3

    def test_bass(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks(["bass.wav", "bassline.wav", "sub_bass.wav"])
        assert len(result["bass"]) == 3

    def test_guitar(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks(["guitar.wav", "lead_gtr.wav"])
        assert len(result["guitar"]) == 2

    def test_keys(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks(["piano.wav", "synth_pad.wav", "strings.wav"])
        assert len(result["keys"]) == 3

    def test_mixed(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks([
            "kick.wav", "bass.wav", "guitar.wav", "piano.wav", "unknown.wav",
        ])
        assert len(result["drums"]) == 1
        assert len(result["bass"]) == 1
        assert len(result["guitar"]) == 1
        assert len(result["keys"]) == 1
        assert len(result["other"]) == 1

    def test_unknown(self):
        mtp = MultiTrackBackingProcessor(MagicMock())
        result = mtp.classify_tracks(["weird_sound.wav", "noise.wav"])
        assert len(result["other"]) == 2


class TestClassifyName:
    def test_drum_patterns(self):
        assert MultiTrackBackingProcessor.classify_name("kick.wav") == "drums"
        assert MultiTrackBackingProcessor.classify_name("snare_top.wav") == "drums"
        assert MultiTrackBackingProcessor.classify_name("overhead_L.wav") == "drums"

    def test_bass_patterns(self):
        assert MultiTrackBackingProcessor.classify_name("bass.wav") == "bass"
        assert MultiTrackBackingProcessor.classify_name("sub_808.wav") == "bass"

    def test_guitar_patterns(self):
        assert MultiTrackBackingProcessor.classify_name("guitar_solo.wav") == "guitar"
        assert MultiTrackBackingProcessor.classify_name("acoustic.wav") == "guitar"

    def test_keys_patterns(self):
        assert MultiTrackBackingProcessor.classify_name("piano.wav") == "keys"
        assert MultiTrackBackingProcessor.classify_name("strings_section.wav") == "keys"

    def test_unknown(self):
        assert MultiTrackBackingProcessor.classify_name("weird.wav") == "other"


class TestInstrumentTypes:
    def test_get_instrument_types(self):
        types = MultiTrackBackingProcessor.get_instrument_types()
        assert "drums" in types
        assert "bass" in types
        assert "guitar" in types
        assert "keys" in types


class TestApplyProcessing:
    def test_drums_eq_and_comp(self):
        fx = MagicMock()
        fx.add.return_value = 0
        fx.set_param.return_value = True
        mtp = MultiTrackBackingProcessor(fx)
        mtp.classify_tracks(["kick.wav"])
        result = mtp.apply_instrument_processing(genre="rock")
        assert "drums" in result
        assert len(result["drums"]["eq"]) >= 1
        assert len(result["drums"]["comp"]) >= 1

    def test_bass_processing(self):
        fx = MagicMock()
        fx.add.return_value = 0
        fx.set_param.return_value = True
        mtp = MultiTrackBackingProcessor(fx)
        mtp.classify_tracks(["bass.wav"])
        result = mtp.apply_instrument_processing()
        assert "bass" in result

    def test_skips_other(self):
        fx = MagicMock()
        mtp = MultiTrackBackingProcessor(fx)
        mtp.classify_tracks(["unknown.wav"])
        result = mtp.apply_instrument_processing()
        assert "other" not in result  # other tracks are skipped

    def test_no_tracks_no_error(self):
        fx = MagicMock()
        mtp = MultiTrackBackingProcessor(fx)
        mtp.classify_tracks([])
        result = mtp.apply_instrument_processing()
        assert result == {}
