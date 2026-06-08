"""
Decapitator 单元测试 — 独立模块参数推导。
"""

import pytest
from hermes_core.decapitator import (
    style_code,
    drive,
    mix_val,
    tone_val,
    output_trim,
    build_params,
    normalize_params,
    _STYLE_A,
    _STYLE_E,
    _STYLE_N,
    _STYLE_T,
)

# ════════════════════════════════════════════════════════════════
# Helpers — 最小化 FXBuildContext
# ════════════════════════════════════════════════════════════════


class _FakeCtx:
    """最小化 FXBuildContext 替代。"""
    def __init__(self, genre="pop", rms=-18.0, peak=-6.0, presence=0.0):
        self.genre = genre
        self.raw_rms_db = rms
        self.raw_peak_db = peak
        self.presence_deficit = presence


# ════════════════════════════════════════════════════════════════
# Style
# ════════════════════════════════════════════════════════════════

class TestStyleCode:
    def test_folk_uses_ampex(self):
        assert style_code("folk") == _STYLE_A

    def test_ballad_uses_ampex(self):
        assert style_code("ballad") == _STYLE_A

    def test_cfbc_uses_emi(self):
        assert style_code("chinese_folk_bel_canto") == _STYLE_E

    def test_pop_uses_emi(self):
        assert style_code("pop") == _STYLE_E

    def test_rock_uses_neve(self):
        assert style_code("rock") == _STYLE_N

    def test_electronic_uses_triode(self):
        assert style_code("electronic") == _STYLE_T

    def test_unknown_genre_falls_back(self):
        assert style_code("jazz") == _STYLE_E  # default=pop -> EMI


# ════════════════════════════════════════════════════════════════
# Drive
# ════════════════════════════════════════════════════════════════

class TestDrive:
    def test_pop_crest12(self):
        """crest=12dB (典型人声) → Drive ~ base。"""
        d = drive(12.0, "pop")
        assert 0.18 <= d <= 0.22  # 接近 base 0.20

    def test_high_crest_low_drive(self):
        """高波峰 = 低 drive（保留瞬态）。"""
        d = drive(22.0, "pop")
        # crest=22: 0.20 - (22-10)*0.010 = 0.08
        assert d < 0.12

    def test_low_crest_high_drive(self):
        """低波峰 = 高 drive（增加谐波密度）。"""
        d = drive(4.0, "pop")
        # crest=4: 0.20 - (4-10)*0.010 = 0.26
        assert d > 0.20

    def test_drive_clamp_max(self):
        """Drive 不超过 0.30（GUI 3.0）。"""
        for genre in ["folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto"]:
            d = drive(0.0, genre)  # 极低 crest → 最高 drive
            assert d <= 0.30, f"{genre}: {d} > 0.30"

    def test_drive_clamp_min(self):
        """Drive 不低于 0.05（GUI 0.5）。"""
        for genre in ["folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto"]:
            d = drive(30.0, genre)  # 极高 crest → 最低 drive
            assert d >= 0.05, f"{genre}: {d} < 0.05"

    def test_folk_lowest_drive(self):
        """民谣 Drive 最低。"""
        folk = drive(12.0, "folk")
        electronic = drive(12.0, "electronic")
        assert folk < electronic

    def test_electronic_highest_drive(self):
        """电子 Drive 最高。"""
        electronic = drive(12.0, "electronic")
        assert electronic > 0.25


# ════════════════════════════════════════════════════════════════
# Mix
# ════════════════════════════════════════════════════════════════

class TestMix:
    def test_folk_mix_lowest(self):
        assert mix_val("folk") == 0.30

    def test_electronic_mix_highest(self):
        assert mix_val("electronic") == 0.50

    def test_all_genres_in_range(self):
        for genre in ["folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto"]:
            m = mix_val(genre)
            assert 0.30 <= m <= 0.50, f"{genre}: {m}"


# ════════════════════════════════════════════════════════════════
# Tone
# ════════════════════════════════════════════════════════════════

class TestTone:
    def test_folk_tone_dark(self):
        """民谣 Tone 偏暗（pre-saturation 低频先失真 → 温暖）。"""
        t = tone_val("folk")
        assert t < 0.50

    def test_electronic_tone_bright(self):
        """电子 Tone 偏亮（pre-saturation 高频先失真 → 颗粒清晰）。"""
        t = tone_val("electronic")
        assert t > 0.50

    def test_presence_deficit_increases_tone(self):
        """声音偏暗 → Tone 补偿增亮。"""
        t0 = tone_val("pop", presence_deficit=0.0)
        t1 = tone_val("pop", presence_deficit=5.0)
        assert t1 > t0

    def test_tone_clamp(self):
        """Tone 不超出 0.42-0.58。"""
        t_low = tone_val("folk", presence_deficit=-10.0)
        t_high = tone_val("electronic", presence_deficit=10.0)
        assert t_low >= 0.42
        assert t_high <= 0.58


# ════════════════════════════════════════════════════════════════
# Output Trim
# ════════════════════════════════════════════════════════════════

class TestOutputTrim:
    def test_zero_drive_means_no_cut(self):
        assert output_trim(0.0) == 1.0

    def test_higher_drive_more_cut(self):
        assert output_trim(0.30) < output_trim(0.10)

    def test_output_range(self):
        """OutputTrim 在 0.825-1.0 内。"""
        for d in [0.05, 0.10, 0.15, 0.20, 0.25, 0.30]:
            o = output_trim(d)
            assert 0.825 <= o <= 1.0, f"drive={d} → output={o}"


# ════════════════════════════════════════════════════════════════
# Builder
# ════════════════════════════════════════════════════════════════

class TestBuildParams:
    def test_all_parameters_present(self):
        ctx = _FakeCtx(genre="pop", rms=-18.0, peak=-6.0)  # crest=12
        result = build_params(ctx)
        assert result is not None
        expected_keys = [
            "Style", "Drive", "Punish", "LowCut", "Tone",
            "HighCut", "Mix", "AutoGain", "LowThump", "HighSlope", "OutputTrim",
        ]
        for key in expected_keys:
            assert key in result, f"{key} missing"

    def test_fixed_off_params(self):
        """Punish/LowCut/HighCut/AutoGain/Thump/Steep 全部为 OFF/0。"""
        ctx = _FakeCtx(genre="pop", rms=-18.0, peak=-6.0)
        result = build_params(ctx)
        assert result["Punish"] == 0.0
        assert result["LowCut"] == 0.0
        assert result["HighCut"] == 0.0
        assert result["AutoGain"] == 0.0
        assert result["LowThump"] == 0.0
        assert result["HighSlope"] == 0.0

    def test_style_varies_by_genre(self):
        """不同流派 → 不同 Style。"""
        r_folk = build_params(_FakeCtx(genre="folk"))
        r_rock = build_params(_FakeCtx(genre="rock"))
        assert r_folk["Style"] != r_rock["Style"]

    def test_no_audio_data_returns_none(self):
        ctx = _FakeCtx(genre="pop", rms=None, peak=None)
        # 需要真正 None
        from hermes_core.fx_builder import FXBuildContext
        real_ctx = FXBuildContext(
            fx_name="Decapitator", fx_type="saturation",
            role="vocal", genre="pop",
        )
        assert build_params(real_ctx) is None

    def test_all_genres_no_exception(self):
        """所有流派均不抛异常。"""
        for genre in ["folk", "ballad", "pop", "rock", "electronic", "chinese_folk_bel_canto"]:
            result = build_params(_FakeCtx(genre=genre))
            assert result is not None, f"{genre} returned None"
            assert len(result) == 11, f"{genre}: expected 11 params, got {len(result)}"


# ════════════════════════════════════════════════════════════════
# Normalize
# ════════════════════════════════════════════════════════════════

class TestNormalizeParams:
    def test_normalize_identity(self):
        """VST3 参数已是 0-1 归一化，normalize 只做 clamp。"""
        physical = {
            "Style": 0.25, "Drive": 0.20, "Punish": 0.0,
            "LowCut": 0.0, "Tone": 0.50, "HighCut": 0.0,
            "Mix": 0.40, "AutoGain": 0.0, "LowThump": 0.0,
            "HighSlope": 0.0, "OutputTrim": 0.95,
        }
        result = normalize_params(physical)
        assert result == physical

    def test_normalize_clamp_out_of_range(self):
        """越界值被 clamp。"""
        result = normalize_params({"Drive": 1.5, "Mix": -0.1})
        assert result["Drive"] == 1.0
        assert result["Mix"] == 0.0
