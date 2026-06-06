"""Agent Protocol 层单元测试。

测试 agent_protocol 数据类的序列化/反序列化，
以及 HermesAgentAPI 对 MixingEngine 的封装和错误处理。
"""

from __future__ import annotations

import os
import tempfile
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from hermes_core.agent_protocol import (
    MixGenre,
    ReverbStyle,
    AdjustmentType,
    MixOptions,
    MixRequest,
    MixResult,
    AdjustRequest,
    AdjustResult,
    StatusResult,
    AuditResult,
    PreviewResult,
    HermesAgentAPI,
    _ADJUST_MAP,
    _GENRE_ENGINE_MAP,
    _to_mix_error,
)
from hermes_core.engine import PipelineState
from hermes_core.exceptions import (
    BridgeConnectionError,
    InvalidStateError,
    HermesError,
)


# ════════════════════════════════════════════════════════════════
# 枚举测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMixGenre:
    """MixGenre 枚举值测试。"""

    def test_all_values_are_lowercase_strings(self):
        for genre in MixGenre:
            assert isinstance(genre.value, str)
            assert genre.value == genre.value.lower()

    def test_has_chinese_folk_bel_canto(self):
        assert MixGenre.CHINESE_FOLK_BEL_CANTO.value == "chinese_folk_bel_canto"

    def test_default_is_pop(self):
        assert MixGenre.POP.value == "pop"


@pytest.mark.unit
class TestReverbStyle:
    """ReverbStyle 枚举值测试。"""

    def test_all_values_are_valid(self):
        for style in ReverbStyle:
            assert style.value in ("plate", "hall", "room", "chamber", "spring")


@pytest.mark.unit
class TestAdjustmentType:
    """AdjustmentType 枚举值测试。"""

    def test_all_have_descriptive_values(self):
        for adj in AdjustmentType:
            assert len(adj.value) > 0
            assert "_" in adj.value or adj.value.isalpha()

    def test_every_type_has_mapping(self):
        for adj in AdjustmentType:
            assert adj in _ADJUST_MAP, (
                f"AdjustmentType.{adj.name} 缺少 _ADJUST_MAP 映射"
            )
            mapping = _ADJUST_MAP[adj]
            assert "description" in mapping
            assert "gain_target" in mapping
            assert "gain_delta" in mapping


# ════════════════════════════════════════════════════════════════
# 数据类测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestMixOptions:
    """MixOptions 数据类测试。"""

    def test_default_all_none(self):
        opts = MixOptions()
        assert opts.target_lufs is None
        assert opts.reverb_style is None
        assert opts.eq_brightness is None
        assert opts.compression_amount is None
        assert opts.stem_gain_db is None

    def test_partial_override(self):
        opts = MixOptions(target_lufs=-14.0, eq_brightness=0.8)
        assert opts.target_lufs == -14.0
        assert opts.eq_brightness == 0.8
        assert opts.reverb_style is None

    def test_reverb_style_enum(self):
        opts = MixOptions(reverb_style=ReverbStyle.HALL)
        assert opts.reverb_style == ReverbStyle.HALL
        assert opts.reverb_style.value == "hall"


@pytest.mark.unit
class TestMixRequest:
    """MixRequest 数据类测试。"""

    def test_minimal_construction(self):
        req = MixRequest(
            project_name="测试工程",
            vocal_stem="/path/vocal.wav",
            backing_stem="/path/backing.wav",
        )
        assert req.project_name == "测试工程"
        assert req.genre == MixGenre.POP
        assert req.producer == "Hermes"
        assert req.category == "music"

    def test_full_construction(self):
        opts = MixOptions(target_lufs=-12.0, reverb_style=ReverbStyle.PLATE)
        req = MixRequest(
            project_name="张三_新歌_Mix",
            vocal_stem="/stems/vocal.wav",
            backing_stem="/stems/backing.wav",
            genre=MixGenre.ROCK,
            producer="张三",
            category="rock",
            options=opts,
        )
        assert req.genre == MixGenre.ROCK
        assert req.options.target_lufs == -12.0

    def test_to_dict_full(self):
        req = MixRequest(
            project_name="测试",
            vocal_stem="/v.wav",
            backing_stem="/b.wav",
            genre=MixGenre.FOLK,
            options=MixOptions(target_lufs=-14.0),
        )
        d = req.to_dict()
        assert d["project_name"] == "测试"
        assert d["genre"] == "folk"
        assert d["options"]["target_lufs"] == -14.0
        assert d["options"]["reverb_style"] is None

    def test_to_dict_with_reverb_style(self):
        req = MixRequest(
            project_name="测试",
            vocal_stem="/v.wav",
            backing_stem="/b.wav",
            options=MixOptions(reverb_style=ReverbStyle.HALL),
        )
        d = req.to_dict()
        assert d["options"]["reverb_style"] == "hall"

    def test_from_dict_minimal(self):
        data = {
            "project_name": "Minimal",
            "vocal_stem": "/v.wav",
            "backing_stem": "/b.wav",
        }
        req = MixRequest.from_dict(data)
        assert req.project_name == "Minimal"
        assert req.genre == MixGenre.POP

    def test_from_dict_full(self):
        data = {
            "project_name": "Full",
            "vocal_stem": "/v.wav",
            "backing_stem": "/b.wav",
            "genre": "ballad",
            "producer": "李四",
            "category": "ballad_mix",
            "options": {
                "target_lufs": -13.0,
                "reverb_style": "plate",
                "eq_brightness": 0.7,
                "compression_amount": 0.5,
                "stem_gain_db": 1.0,
            },
        }
        req = MixRequest.from_dict(data)
        assert req.genre == MixGenre.BALLAD
        assert req.options.target_lufs == -13.0
        assert req.options.reverb_style == ReverbStyle.PLATE
        assert req.options.eq_brightness == 0.7

    def test_roundtrip(self):
        original = MixRequest(
            project_name="往返测试",
            vocal_stem="/v.wav",
            backing_stem="/b.wav",
            genre=MixGenre.CHINESE_FOLK_BEL_CANTO,
            options=MixOptions(target_lufs=-11.0, eq_brightness=0.6),
        )
        d = original.to_dict()
        restored = MixRequest.from_dict(d)
        assert restored.project_name == original.project_name
        assert restored.genre == original.genre
        assert restored.options.target_lufs == original.options.target_lufs


@pytest.mark.unit
class TestMixResult:
    """MixResult 数据类测试。"""

    def test_ok_creates_success(self):
        result = MixResult.ok(
            project_path="/proj/test.rpp",
            render_path="/proj/render/output.wav",
            lufs_integrated=-13.5,
        )
        assert result.success is True
        assert result.error is None
        assert result.error_hint is None
        assert result.lufs_integrated == -13.5

    def test_fail_creates_error(self):
        result = MixResult.fail(
            error="连接超时",
            hint="检查 REAPER 是否运行",
        )
        assert result.success is False
        assert result.error == "连接超时"
        assert result.error_hint == "检查 REAPER 是否运行"
        assert result.render_path is None

    def test_fail_preserves_extra_kwargs(self):
        result = MixResult.fail(
            error="失败",
            operations_log=[{"stage": "test"}],
        )
        assert result.operations_log == [{"stage": "test"}]


@pytest.mark.unit
class TestAdjustRequest:
    """AdjustRequest 数据类测试。"""

    def test_default_intensity(self):
        req = AdjustRequest(
            adjustment_type=AdjustmentType.EQ_BRIGHTER,
        )
        assert req.intensity == 1.0
        assert req.description == ""

    def test_custom_intensity(self):
        req = AdjustRequest(
            adjustment_type=AdjustmentType.REVERB_MORE,
            intensity=2.0,
            description="混响再大一点",
        )
        assert req.intensity == 2.0
        assert req.description == "混响再大一点"


@pytest.mark.unit
class TestAdjustResult:
    """AdjustResult 数据类测试。"""

    def test_ok_with_changes(self):
        result = AdjustResult.ok(
            changes_applied=["EQ brightness +0.15"],
            before_preview="/tmp/before.wav",
            after_preview="/tmp/after.wav",
        )
        assert result.success is True
        assert len(result.changes_applied) == 1
        assert result.before_preview == "/tmp/before.wav"
        assert result.error is None

    def test_fail(self):
        result = AdjustResult.fail(
            error="不支持的调整类型",
            hint="查看 AdjustmentType 枚举",
        )
        assert result.success is False
        assert result.error_hint == "查看 AdjustmentType 枚举"


@pytest.mark.unit
class TestStatusResult:
    """StatusResult 数据类测试。"""

    def test_default_values(self):
        result = StatusResult(pipeline_state="created")
        assert result.pipeline_state == "created"
        assert result.track_count == 0
        assert result.fx_count == 0
        assert result.is_rendering is False


@pytest.mark.unit
class TestAuditResult:
    """AuditResult 数据类测试。"""

    def test_from_audit_dict_all_clear(self):
        raw = {
            "passed": True,
            "checks": [
                {"check_name": "all_clear", "severity": "info",
                 "message": "No issues detected"},
            ],
            "diagnostics": {
                "integrated_lufs": -13.5,
                "true_peak_dbtp": -0.3,
                "rms_db": -22.0,
                "peak_db": -2.0,
                "duration_sec": 180.0,
            },
        }
        result = AuditResult.from_audit_dict(raw)
        assert result.lufs_integrated == -13.5
        assert result.true_peak_db == -0.3
        assert result.dynamic_range_db == 20.0
        assert len(result.warnings) == 0
        assert len(result.suggestions) == 0

    def test_from_audit_dict_with_warnings(self):
        raw = {
            "passed": False,
            "checks": [
                {"check_name": "clipping", "severity": "critical",
                 "message": "Mix has 42 clipped samples"},
                {"check_name": "true_peak", "severity": "warning",
                 "message": "True peak 1.2 dBTP exceeds 0 dBTP"},
                {"check_name": "true_peak", "severity": "info",
                 "message": "Within 1 dB of ceiling"},
            ],
            "diagnostics": {
                "integrated_lufs": -9.0,
                "true_peak_dbtp": 1.2,
                "rms_db": -18.0,
                "peak_db": -1.0,
            },
        }
        result = AuditResult.from_audit_dict(raw)
        assert len(result.warnings) == 2
        assert len(result.suggestions) == 1
        assert result.lufs_integrated == -9.0
        assert "42 clipped samples" in result.warnings[0]

    def test_from_audit_dict_empty_diagnostics(self):
        raw = {
            "passed": True,
            "checks": [],
            "diagnostics": {},
        }
        result = AuditResult.from_audit_dict(raw)
        assert result.lufs_integrated is None
        assert result.dynamic_range_db is None


@pytest.mark.unit
class TestPreviewResult:
    """PreviewResult 数据类测试。"""

    def test_ok(self):
        result = PreviewResult.ok(
            preview_path="/tmp/preview.wav",
            format="wav",
        )
        assert result.success is True
        assert result.error is None

    def test_fail(self):
        result = PreviewResult.fail(error="预览渲染失败")
        assert result.success is False
        assert result.preview_path is None


# ════════════════════════════════════════════════════════════════
# 流派映射测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestGenreMapping:
    """_GENRE_ENGINE_MAP 测试。"""

    def test_every_genre_has_mapping(self):
        for genre in MixGenre:
            assert genre in _GENRE_ENGINE_MAP, (
                f"MixGenre.{genre.name} 缺少 engine 映射"
            )

    def test_supported_genres_map_directly(self):
        assert _GENRE_ENGINE_MAP[MixGenre.POP] == "pop"
        assert _GENRE_ENGINE_MAP[MixGenre.ROCK] == "rock"
        assert _GENRE_ENGINE_MAP[MixGenre.FOLK] == "folk"
        assert _GENRE_ENGINE_MAP[MixGenre.BALLAD] == "ballad"
        assert _GENRE_ENGINE_MAP[MixGenre.ELECTRONIC] == "electronic"
        assert _GENRE_ENGINE_MAP[
            MixGenre.CHINESE_FOLK_BEL_CANTO] == "chinese_folk_bel_canto"

    def test_unsupported_genres_fallback_to_pop(self):
        assert _GENRE_ENGINE_MAP[MixGenre.HIPHOP] == "pop"
        assert _GENRE_ENGINE_MAP[MixGenre.RNB] == "pop"
        assert _GENRE_ENGINE_MAP[MixGenre.JAZZ] == "pop"


# ════════════════════════════════════════════════════════════════
# 错误转换测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestErrorConversion:
    """_to_mix_error 测试。"""

    def test_bridge_connection_error(self):
        msg, hint = _to_mix_error(
            BridgeConnectionError("Failed to connect")
        )
        assert "Failed to connect" in msg
        assert hint is not None
        assert "REAPER" in hint

    def test_invalid_state_error(self):
        msg, hint = _to_mix_error(
            InvalidStateError("Invalid state transition")
        )
        assert "Invalid state transition" in msg
        assert hint is not None
        assert "reset" in hint

    def test_generic_hermes_error(self):
        msg, hint = _to_mix_error(HermesError("Generic error"))
        assert "Generic error" in msg
        assert hint is not None

    def test_no_project_path_error(self):
        msg, hint = _to_mix_error(
            RuntimeError("No project path — call create_project first")
        )
        assert "No project path" in msg
        assert hint is not None
        assert "create_project" in hint.lower() or "create_and_mix" in hint.lower()

    def test_generic_exception(self):
        msg, hint = _to_mix_error(ValueError("Something went wrong"))
        assert "Something went wrong" in msg
        # 普通异常至少返回消息


# ════════════════════════════════════════════════════════════════
# HermesAgentAPI 构造与属性测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestHermesAgentAPIConstruction:
    """HermesAgentAPI 构造测试。"""

    def test_no_engine_lazy_init(self):
        api = HermesAgentAPI()
        assert api._engine is None

        # 访问 engine 属性触发延迟创建
        # MixingEngine 在 HermesAgentAPI.engine property getter 中被懒加载导入
        with patch(
            "hermes_core.engine.MixingEngine",
            autospec=True,
        ) as MockEngine:
            mock_instance = MagicMock()
            MockEngine.return_value = mock_instance

            # 重置 _engine 以便测试延迟创建
            api._engine = None
            eng = api.engine
            MockEngine.assert_called_once()
            assert eng is mock_instance

    def test_with_explicit_engine(self):
        mock_engine = MagicMock()
        api = HermesAgentAPI(engine=mock_engine)
        assert api.engine is mock_engine

    def test_engine_setter(self):
        api = HermesAgentAPI()
        mock1 = MagicMock()
        api.engine = mock1
        assert api.engine is mock1

        mock2 = MagicMock()
        api.engine = mock2
        assert api.engine is mock2


@pytest.mark.unit
class TestHermesAgentAPIStatus:
    """HermesAgentAPI.get_status() 测试。"""

    def test_get_status_returns_status_result(self):
        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.CREATED
        mock_engine.get_project_info.return_value = {
            "name": "test_project",
            "path": "/tmp/test.rpp",
            "sample_rate": 48000,
            "track_count": 2,
        }
        mock_engine.list_tracks.return_value = [
            MagicMock(index=0),
            MagicMock(index=1),
        ]
        mock_engine.get_fx_chain.return_value = [
            {"name": "Pro-Q 3", "index": 0},
            {"name": "CLA-76", "index": 1},
        ]

        api = HermesAgentAPI(engine=mock_engine)
        result = api.get_status()

        assert isinstance(result, StatusResult)
        assert result.pipeline_state == "created"
        assert result.project_name == "test_project"
        assert result.track_count == 2
        assert result.fx_count == 2

    def test_get_status_handles_engine_exceptions(self):
        mock_engine = MagicMock()
        mock_engine._pipeline_state = None  # 没有 _pipeline_state 属性

        # get_project_info 抛出异常
        mock_engine.get_project_info.side_effect = RuntimeError("API error")
        mock_engine.list_tracks.side_effect = RuntimeError("API error")
        mock_engine.get_fx_chain.side_effect = RuntimeError("API error")

        api = HermesAgentAPI(engine=mock_engine)
        result = api.get_status()

        assert isinstance(result, StatusResult)
        # 容错，即使全部失败也应返回 StatusResult
        assert result.track_count == 0
        assert result.fx_count == 0

    def test_get_status_gracefully_handles_missing_attrs(self):
        # 模拟引擎所有方法都抛异常的场景，验证容错性
        mock_engine = MagicMock()
        mock_engine._pipeline_state = None  # falsy → 走 "unknown"
        mock_engine.get_project_info.side_effect = RuntimeError("broken")
        mock_engine.list_tracks.side_effect = RuntimeError("broken")
        mock_engine.get_fx_chain.side_effect = RuntimeError("broken")

        api = HermesAgentAPI(engine=mock_engine)
        result = api.get_status()

        assert isinstance(result, StatusResult)
        assert result.pipeline_state == "unknown"


@pytest.mark.unit
class TestHermesAgentAPIAudit:
    """HermesAgentAPI.get_audit() 测试。"""

    def test_get_audit_with_render_path(self):
        mock_engine = MagicMock()
        mock_engine.audit_mix.return_value = {
            "passed": True,
            "checks": [
                {"check_name": "all_clear", "severity": "info",
                 "message": "No issues detected"},
            ],
            "diagnostics": {
                "integrated_lufs": -13.5,
                "true_peak_dbtp": -0.5,
                "rms_db": -23.0,
                "peak_db": -3.0,
                "duration_sec": 180.0,
            },
        }

        api = HermesAgentAPI(engine=mock_engine)
        api._ops_log = [
            {"stage": "render_mix", "output_path": "/tmp/output.wav"},
        ]

        with patch("os.path.exists", return_value=True):
            result = api.get_audit()
        assert isinstance(result, AuditResult)
        assert result.lufs_integrated == -13.5
        assert result.true_peak_db == -0.5
        mock_engine.audit_mix.assert_called_once_with("/tmp/output.wav")

    def test_get_audit_no_render_path_yet(self):
        mock_engine = MagicMock()
        mock_engine.get_project_info.return_value = {
            "name": "test",
            "track_count": 2,
        }

        api = HermesAgentAPI(engine=mock_engine)
        api._ops_log = []  # 无 render_mix 阶段

        result = api.get_audit()
        assert isinstance(result, AuditResult)
        assert len(result.warnings) > 0
        assert len(result.suggestions) > 0

    def test_get_audit_handles_engine_exception(self):
        mock_engine = MagicMock()
        mock_engine.get_project_info.side_effect = RuntimeError("broken")

        api = HermesAgentAPI(engine=mock_engine)
        result = api.get_audit()

        assert isinstance(result, AuditResult)
        assert len(result.warnings) > 0


# ════════════════════════════════════════════════════════════════
# HermesAgentAPI adjust 测试（mocked engine）
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestHermesAgentAPIAdjust:
    """HermesAgentAPI.adjust() 测试。"""

    def test_adjust_with_internal_apply_error(self):
        """_apply_adjustment 内部异常被捕获并加入 changes_applied 警告。"""
        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.FX_APPLIED

        api = HermesAgentAPI(engine=mock_engine)
        # 直接 mock _apply_adjustment 让它抛出异常
        # adjust() 会捕获并转为 changes_applied 中的警告
        with patch.object(
            api, "_apply_adjustment",
            side_effect=RuntimeError("内部调整错误"),
        ):
            result = api.adjust(AdjustRequest(
                adjustment_type=AdjustmentType.EQ_BRIGHTER,
                intensity=1.0,
                description="亮一点",
            ))
        # 异常被容错处理，仍返回 success=True 但 changes_applied 中有警告
        assert result.success is True
        assert any(
            "警告" in c or "内部调整错误" in c
            for c in result.changes_applied
        )

    def test_adjust_vocal_louder(self):
        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.FX_APPLIED

        api = HermesAgentAPI(engine=mock_engine)
        result = api.adjust(AdjustRequest(
            adjustment_type=AdjustmentType.VOCAL_LOUDER,
            intensity=1.0,
        ))

        assert isinstance(result, AdjustResult)
        assert result.success is True
        assert len(result.changes_applied) > 0

    def test_adjust_with_nullsafe_description(self):
        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.FX_APPLIED

        api = HermesAgentAPI(engine=mock_engine)
        result = api.adjust(AdjustRequest(
            adjustment_type=AdjustmentType.COMPRESS_MORE,
            intensity=2.0,
            description="压得更狠一些",
        ))

        assert result.success is True
        assert any(
            "压得更狠一些" in c for c in result.changes_applied
        )

    def test_adjust_with_no_pipeline_state(self):
        mock_engine = MagicMock(spec=["_pipeline_state"])
        mock_engine._pipeline_state = None

        api = HermesAgentAPI(engine=mock_engine)
        result = api.adjust(AdjustRequest(
            adjustment_type=AdjustmentType.EQ_WARMER,
        ))

        assert isinstance(result, AdjustResult)
        assert result.success is False
        assert result.error is not None
        assert "创建" in result.error or "create_and_mix" in result.error


# ════════════════════════════════════════════════════════════════
# HermesAgentAPI create_and_mix 端到端测试（mocked engine）
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCreateAndMixHappyPath:
    """create_and_mix 正常管线测试。"""

    @patch("hermes_core.config.HermesConfig")
    def test_create_and_mix_full_pipeline_success(
        self, mock_config_cls,
    ):
        """模拟完整管线成功场景。"""
        from hermes_core.engine import PipelineState

        # Mock config
        mock_cfg = MagicMock()
        mock_cfg.project_root_expanded = "/tmp/hermes_projects"
        mock_config_cls.load.return_value = mock_cfg

        # Mock engine
        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.CREATED

        # Mock create_project
        mock_engine.create_project.return_value = {
            "name": "测试工程_Mix",
            "path": "/tmp/test.rpp",
            "meta_dir": "/tmp/hermes_projects/music/测试工程_Mix",
        }

        # Mock prepare_stems
        mock_engine.prepare_stems.return_value = {
            "stems": [
                {"file_path": "/stems/vocal.wav", "role": "vocal",
                 "track_index": 0, "raw_rms_db": -22.0,
                 "raw_peak_db": -2.0, "clip_gain_db": 4.0},
                {"file_path": "/stems/backing.wav", "role": "backing",
                 "track_index": 1, "raw_rms_db": -15.0,
                 "raw_peak_db": -1.0, "clip_gain_db": 3.0},
            ],
            "genre": "pop",
        }

        # Mock post_fx_balance
        mock_engine.post_fx_balance.return_value = {
            "ratio_lu": 3.0,
            "vocal_lufs": -20.0,
            "backing_lufs": -23.0,
        }

        # Mock finalize_master
        mock_engine.finalize_master.return_value = {
            "passed": True,
            "target_lufs": -10.0,
            "achieved_lufs": -10.2,
            "gain_db": 3.5,
            "ceiling_db": -0.5,
        }

        # Mock render_mix
        mock_engine.render_mix.return_value = {
            "output_path": "/tmp/output.wav",
            "signal_check": {
                "integrated_lufs": -10.2,
                "true_peak_dbtp": -0.3,
                "duration_sec": 180.0,
            },
        }

        # Mock audit_mix
        mock_engine.audit_mix.return_value = {
            "passed": True,
            "checks": [
                {"check_name": "all_clear", "severity": "info",
                 "message": "No issues detected"},
            ],
            "diagnostics": {
                "integrated_lufs": -10.2,
                "true_peak_dbtp": -0.3,
                "rms_db": -25.0,
                "peak_db": -5.0,
            },
        }

        # Mock profiles
        with patch(
            "hermes_core.profiles.MixingProfile"
        ) as MockProfile:
            mock_profile = MagicMock()
            mock_profile.vocal_chain = [MagicMock(name="Pro-Q 3"),
                                         MagicMock(name="CLA-76")]
            mock_profile.backing_chain = [MagicMock(name="Pro-Q 3")]
            mock_profile.reverb_style = "plate"
            MockProfile.for_genre.return_value = mock_profile

            api = HermesAgentAPI(engine=mock_engine)
            request = MixRequest(
                project_name="测试工程_Mix",
                vocal_stem="/stems/vocal.wav",
                backing_stem="/stems/backing.wav",
                genre=MixGenre.POP,
            )

            with patch("os.path.exists", return_value=True):
                result = api.create_and_mix(request)

        assert isinstance(result, MixResult)
        assert result.success is True
        assert result.render_path == "/tmp/output.wav"
        assert result.lufs_integrated == -10.2
        assert result.true_peak_db == -0.3
        assert result.duration_sec == 180.0
        assert len(result.operations_log) >= 7  # 至少 7 个管线阶段
        assert result.audit_report is not None
        assert result.audit_report["passed"] is True

        # 验证引擎调用顺序
        assert mock_engine.create_project.called
        assert mock_engine.prepare_stems.called
        assert mock_engine.finalize_master.called
        assert mock_engine.render_mix.called
        assert mock_engine.audit_mix.called
        assert mock_engine.save_project.called


@pytest.mark.unit
class TestCreateAndMixErrorHandling:
    """create_and_mix 错误处理测试。"""

    @patch("hermes_core.config.HermesConfig")
    def test_create_and_mix_engine_connection_error(
        self, mock_config_cls,
    ):
        """引擎连接失败时应返回错误结果。"""
        mock_cfg = MagicMock()
        mock_cfg.project_root_expanded = "/tmp/hermes_projects"
        mock_config_cls.load.return_value = mock_cfg

        mock_engine = MagicMock()
        mock_engine.__enter__ = MagicMock(return_value=mock_engine)
        mock_engine.__exit__ = MagicMock(return_value=False)
        mock_engine.create_project.side_effect = BridgeConnectionError(
            "Failed to connect to REAPER bridge"
        )

        api = HermesAgentAPI(engine=mock_engine)
        request = MixRequest(
            project_name="测试工程",
            vocal_stem="/v.wav",
            backing_stem="/b.wav",
        )

        result = api.create_and_mix(request)

        assert isinstance(result, MixResult)
        assert result.success is False
        assert "Failed to connect" in (result.error or "")
        assert result.error_hint is not None
        assert "REAPER" in (result.error_hint or "")

    @patch("hermes_core.config.HermesConfig")
    def test_create_and_mix_master_failed(
        self, mock_config_cls,
    ):
        """母带失败时应返回错误结果。"""
        mock_cfg = MagicMock()
        mock_cfg.project_root_expanded = "/tmp/hermes_projects"
        mock_config_cls.load.return_value = mock_cfg

        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.CREATED
        mock_engine.create_project.return_value = {
            "name": "test", "path": "/tmp/test.rpp",
            "meta_dir": "/tmp/dir",
        }
        mock_engine.prepare_stems.return_value = {
            "stems": [
                {"file_path": "/v.wav", "role": "vocal",
                 "track_index": 0, "raw_rms_db": -22.0,
                 "raw_peak_db": -2.0, "clip_gain_db": 4.0},
                {"file_path": "/b.wav", "role": "backing",
                 "track_index": 1, "raw_rms_db": -15.0,
                 "raw_peak_db": -1.0, "clip_gain_db": 3.0},
            ],
            "genre": "pop",
        }
        mock_engine.post_fx_balance.return_value = {
            "ratio_lu": 3.0,
        }
        mock_engine.finalize_master.return_value = {
            "passed": False,
            "error": "Probe render failed",
            "hint": "REAPER may be blocked by a modal dialog",
        }

        with patch(
            "hermes_core.profiles.MixingProfile"
        ) as MockProfile:
            mock_profile = MagicMock()
            mock_profile.vocal_chain = []
            mock_profile.backing_chain = []
            MockProfile.for_genre.return_value = mock_profile

            api = HermesAgentAPI(engine=mock_engine)
            request = MixRequest(
                project_name="测试工程",
                vocal_stem="/v.wav",
                backing_stem="/b.wav",
            )

            result = api.create_and_mix(request)

        assert result.success is False
        assert "Probe render failed" in (result.error or "")
        assert result.operations_log is not None
        assert len(result.operations_log) > 0


@pytest.mark.unit
class TestCreateAndMixProgressCallback:
    """create_and_mix 进度回调测试。"""

    @patch("hermes_core.config.HermesConfig")
    def test_progress_callback_is_called(self, mock_config_cls):
        """进度回调应在各阶段被调用。"""
        mock_cfg = MagicMock()
        mock_cfg.project_root_expanded = "/tmp/hermes_projects"
        mock_config_cls.load.return_value = mock_cfg

        mock_engine = MagicMock()
        mock_engine._pipeline_state = PipelineState.CREATED
        mock_engine.create_project.return_value = {
            "name": "test", "path": "/tmp/test.rpp",
            "meta_dir": "/tmp/dir",
        }
        mock_engine.prepare_stems.return_value = {
            "stems": [
                {"file_path": "/v.wav", "role": "vocal",
                 "track_index": 0, "raw_rms_db": -22.0,
                 "raw_peak_db": -2.0, "clip_gain_db": 4.0},
                {"file_path": "/b.wav", "role": "backing",
                 "track_index": 1, "raw_rms_db": -15.0,
                 "raw_peak_db": -1.0, "clip_gain_db": 3.0},
            ],
            "genre": "pop",
        }
        mock_engine.post_fx_balance.return_value = {"ratio_lu": 3.0}
        mock_engine.finalize_master.return_value = {
            "passed": True,
            "target_lufs": -10.0,
            "achieved_lufs": -10.0,
            "gain_db": 3.0,
        }
        mock_engine.render_mix.return_value = {
            "output_path": "/tmp/output.wav",
            "signal_check": {
                "integrated_lufs": -10.0,
                "true_peak_dbtp": -0.5,
                "duration_sec": 180.0,
            },
        }
        mock_engine.audit_mix.return_value = {
            "passed": True,
            "checks": [],
            "diagnostics": {},
        }

        progress_calls = []

        with patch(
            "hermes_core.profiles.MixingProfile"
        ) as MockProfile:
            mock_profile = MagicMock()
            mock_profile.vocal_chain = []
            mock_profile.backing_chain = []
            MockProfile.for_genre.return_value = mock_profile

            api = HermesAgentAPI(engine=mock_engine)
            request = MixRequest(
                project_name="测试工程",
                vocal_stem="/v.wav",
                backing_stem="/b.wav",
            )

            result = api.create_and_mix(
                request,
                on_progress=lambda s, p: progress_calls.append((s, p)),
            )

        assert result.success is True
        assert len(progress_calls) > 0
        # 最后一个回调应为 "done" 阶段，pct=1.0
        assert progress_calls[-1][0] == "done"
        assert progress_calls[-1][1] == 1.0


@pytest.mark.unit
class TestHermesAgentAPIPreview:
    """HermesAgentAPI.preview() 测试。"""

    def test_preview_success(self):
        mock_engine = MagicMock()
        mock_engine.render_preview.return_value = {
            "output_path": "/tmp/preview.wav",
            "mode": "preview",
            "estimated_lufs": -12.0,
        }

        api = HermesAgentAPI(engine=mock_engine)
        with patch("os.path.exists", return_value=True):
            result = api.preview(duration_sec=15.0)

        assert result.success is True

    def test_preview_render_failure(self):
        mock_engine = MagicMock()
        mock_engine.render_preview.return_value = {
            "output_path": None,
            "error": "Dry render failed",
            "mode": "preview",
        }

        api = HermesAgentAPI(engine=mock_engine)
        result = api.preview(duration_sec=15.0)

        assert isinstance(result, PreviewResult)
        assert result.success is False

    def test_preview_engine_exception(self):
        mock_engine = MagicMock()
        mock_engine.render_preview.side_effect = RuntimeError("REAPER gone")

        api = HermesAgentAPI(engine=mock_engine)
        result = api.preview()

        assert isinstance(result, PreviewResult)
        assert result.success is False
        assert result.error is not None


# ════════════════════════════════════════════════════════════════
# HermesAgentAPI _apply_adjustment 详细测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestApplyAdjustmentDetail:
    """_apply_adjustment 各调整类型的详细测试。"""

    def test_vocal_louder_adjusts_fader(self):
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False  # 无 vocal_chain_nodes

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.VOCAL_LOUDER,
            intensity=1.0,
        )
        mock_engine.apply_gain.assert_called_once_with(0, 1.5)

    def test_vocal_quieter_adjusts_fader(self):
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.VOCAL_QUIETER,
            intensity=2.0,
        )
        mock_engine.apply_gain.assert_called_once_with(0, 3.0)

    def test_reverb_more_without_send_node(self):
        """无 reverb send node 时不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False

        api = HermesAgentAPI(engine=mock_engine)
        # 不应抛出异常
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.REVERB_MORE,
            intensity=1.0,
        )

    def test_eq_adjustment_without_eq_nodes(self):
        """无 EQ 节点时不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False
        mock_engine.get_fx_chain.return_value = []

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.EQ_BRIGHTER,
            intensity=1.0,
        )

    def test_compress_more_without_comp_nodes(self):
        """无压缩节点时不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False
        mock_engine.get_fx_chain.return_value = []

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.COMPRESS_MORE,
            intensity=1.0,
        )

    def test_reverb_more_without_nodes(self):
        """无混响节点时 reverb_more 不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False
        mock_engine.get_fx_chain.return_value = []

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.REVERB_MORE,
            intensity=0.5,
        )

    def test_delay_more_without_nodes(self):
        """无延迟节点时 delay_more 不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False
        mock_engine.get_fx_chain.return_value = []

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.DELAY_MORE,
            intensity=0.5,
        )

    def test_compress_less_without_nodes(self):
        """无压缩节点时 compress_less 不应崩溃。"""
        mock_engine = MagicMock()
        mock_engine.hasattr.return_value = False
        mock_engine.get_fx_chain.return_value = []

        api = HermesAgentAPI(engine=mock_engine)
        api._apply_adjustment(
            mock_engine,
            AdjustmentType.COMPRESS_LESS,
            intensity=1.0,
        )


# ════════════════════════════════════════════════════════════════
# _to_mix_error 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestToMixError:
    """验证 _to_mix_error 的各种异常映射。"""

    def test_value_error(self):
        from hermes_core.agent_protocol import _to_mix_error
        err, hint = _to_mix_error(ValueError("无效参数"))
        assert err is not None
        assert isinstance(hint, str) or hint is None

    def test_type_error(self):
        from hermes_core.agent_protocol import _to_mix_error
        err, hint = _to_mix_error(TypeError("类型不匹配"))
        assert err is not None

    def test_runtime_error(self):
        from hermes_core.agent_protocol import _to_mix_error
        err, hint = _to_mix_error(RuntimeError("运行时异常"))
        assert err is not None

    def test_generic_exception(self):
        from hermes_core.agent_protocol import _to_mix_error
        err, hint = _to_mix_error(Exception("通用异常"))
        assert err is not None

    def test_exception_with_custom_message(self):
        from hermes_core.agent_protocol import _to_mix_error
        err, hint = _to_mix_error(
            ConnectionError("无法连接到 REAPER")
        )
        assert err is not None


# ════════════════════════════════════════════════════════════════
# AuditResult 补充
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestAuditResult:
    """AuditResult 数据类测试。"""

    def test_from_audit_dict_with_warnings(self):
        from hermes_core.agent_protocol import AuditResult
        data = {
            "diagnostics": {
                "integrated_lufs": -14.0,
                "true_peak_dbtp": -1.0,
                "rms_db": -18.0,
                "peak_db": -6.0,
            },
            "checks": [
                {"check_name": "loudness", "message": "LUFS 过低", "severity": "warning"},
                {"check_name": "peak", "message": "峰值安全", "severity": "info"},
            ],
        }
        result = AuditResult.from_audit_dict(data)
        assert result.lufs_integrated == -14.0
        assert "LUFS 过低" in result.warnings

    def test_from_audit_dict_all_clear(self):
        from hermes_core.agent_protocol import AuditResult
        data = {
            "diagnostics": {"integrated_lufs": -16.0},
            "checks": [
                {"check_name": "all_clear", "message": "通过", "severity": "info"},
            ],
        }
        result = AuditResult.from_audit_dict(data)
        assert result.lufs_integrated == -16.0
        # all_clear 不计入 suggestions
        assert len(result.suggestions) == 0


# ════════════════════════════════════════════════════════════════
# PreviewResult 补充
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPreviewResultExtras:
    """PreviewResult 额外测试。"""

    def test_fail_with_empty_error(self):
        from hermes_core.agent_protocol import PreviewResult
        result = PreviewResult.fail(error="")
        assert result.success is False

    def test_ok_with_all_fields(self):
        from hermes_core.agent_protocol import PreviewResult
        result = PreviewResult.ok(
            preview_path="/tmp/preview.wav",
            before_path="/tmp/before.wav",
            after_path="/tmp/after.wav",
            format="wav",
        )
        assert result.success is True
        assert result.preview_path == "/tmp/preview.wav"
