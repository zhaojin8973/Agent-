"""MasteringEngine 单元测试。"""

import pytest
from unittest.mock import MagicMock, patch

from hermes_core.mastering import (
    MasteringEngine, _get_genre_target_lufs,
    _master_error, _friendly_hint,
)


class TestGenreTargetLufs:
    def test_known_genres(self):
        assert _get_genre_target_lufs("pop") == -10.0
        assert _get_genre_target_lufs("rock") == -10.0
        assert _get_genre_target_lufs("ballad") == -13.0

    def test_unknown_genre_defaults(self):
        assert _get_genre_target_lufs("unknown_genre") == -10.0


class TestMasterError:
    def test_returns_structured_error(self):
        result = _master_error(-10.0, -0.5, "test failure")
        assert result["target_lufs"] == -10.0
        assert result["passed"] is False
        assert result["error"] == "test failure"
        assert result["hint"] is not None


class TestFriendlyHint:
    def test_known_error_matched(self):
        hint = _friendly_hint("WAV data chunk not found")
        assert "corrupted" in hint.lower()

    def test_unknown_error_generic(self):
        hint = _friendly_hint("some random error")
        assert "check the log" in hint.lower()


@pytest.fixture
def mock_bridge():
    return MagicMock()


@pytest.fixture
def mock_fx():
    fx = MagicMock()
    fx.add_master.return_value = 0
    fx.set_param.return_value = True
    return fx


@pytest.fixture
def mock_render():
    render = MagicMock()
    render.render_mix.return_value = {
        "output_path": "/tmp/test_probe.wav",
        "signal_check": {"peak_db": -4.8, "integrated_lufs": -12.0},
    }
    return render


@pytest.fixture
def mastering(mock_bridge, mock_fx, mock_render):
    return MasteringEngine(mock_bridge, mock_fx, mock_render)


class TestMasteringEngine:
    def test_init(self, mastering):
        assert mastering._bridge is not None
        assert mastering._fx is not None

    def test_finalize_limiter_failure(self, mastering):
        """Pro-L 2 添加失败应返回错误。"""
        mastering._fx.add_master.return_value = -1
        result = mastering.finalize(target_lufs=-10.0)
        assert result["passed"] is False
        assert "failed to add" in result.get("error", "").lower()

    def test_finalize_param_not_found(self, mastering):
        """Pro-L 2 参数名不匹配应返回错误。"""
        mastering._fx.set_param.return_value = False
        result = mastering.finalize(target_lufs=-10.0)
        assert result["passed"] is False

    def test_finalize_probe_near_silent(self, mastering):
        """探测音频接近静音时返回错误。"""
        with patch("hermes_core.mastering.find_optimal_gain") as mock_search:
            mock_search.return_value = MagicMock(
                converged=False, gain_db=0.0, probe_lufs=-80.0,
            )
            result = mastering.finalize(target_lufs=-10.0)
            assert result["passed"] is False
            assert "near-silent" in result.get("error", "").lower()

    def test_finalize_success_path(self, mastering):
        """正常母带流程：二分搜索收敛，验证通过。"""
        with (
            patch("hermes_core.mastering.find_optimal_gain") as mock_search,
            patch("hermes_core.mastering.verify_output") as mock_verify,
            patch("hermes_core.mastering.generate_report"),
            patch("hermes_core.mastering.load_calibration", return_value=0.0),
        ):
            mock_search.return_value = MagicMock(
                converged=True, gain_db=8.5, probe_lufs=-18.0,
            )
            mock_verify.return_value = MagicMock(
                passed=True, actual_lufs=-10.1,
            )

            result = mastering.finalize(target_lufs=-10.0)
            assert result["passed"] is True
            assert result["converged"] is True
            assert result["gain_db"] == 8.5
            assert result["achieved_lufs"] == -10.1

    def test_finalize_gain_negative_when_loud_probe(self, mastering):
        """探测音频已经很响时，增益应为负值。"""
        with (
            patch("hermes_core.mastering.find_optimal_gain") as mock_search,
            patch("hermes_core.mastering.verify_output") as mock_verify,
            patch("hermes_core.mastering.generate_report"),
            patch("hermes_core.mastering.load_calibration", return_value=0.0),
        ):
            mock_search.return_value = MagicMock(
                converged=True, gain_db=-2.0, probe_lufs=-8.0,
            )
            mock_verify.return_value = MagicMock(
                passed=True, actual_lufs=-10.3,
            )

            result = mastering.finalize(target_lufs=-10.0)
            assert result["passed"] is True
            assert result["gain_db"] == -2.0  # 负增益合法

    def test_finalize_sets_pro_l2_params(self, mastering):
        """验证 Pro-L 2 参数被正确设置。"""
        with (
            patch("hermes_core.mastering.find_optimal_gain") as mock_search,
            patch("hermes_core.mastering.verify_output") as mock_verify,
            patch("hermes_core.mastering.generate_report"),
            patch("hermes_core.mastering.load_calibration", return_value=0.0),
        ):
            mock_search.return_value = MagicMock(
                converged=True, gain_db=6.0, probe_lufs=-19.0,
            )
            mock_verify.return_value = MagicMock(
                passed=True, actual_lufs=-10.0,
            )

            mastering.finalize(target_lufs=-10.0)
            # 至少调用了 Output Level、Gain=0、Gain=searched
            assert mastering._fx.set_param.call_count >= 3
            mastering._fx.add_master.assert_called_once()
