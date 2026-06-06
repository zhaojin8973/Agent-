"""chain_renderer 纯函数测试 — 无需 REAPER。"""
import os
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.dag import AudioNode
from hermes_core.chain_renderer import (
    _init_translators, _make_chain_executor, execute_chain,
    _micro_render_node,
)


class TestChainRendererPure:
    def test_init_translators(self):
        """惰性导入翻译器应正常完成。"""
        _init_translators()

    def test_make_chain_executor(self):
        """_make_chain_executor 返回 ChainExecutor。"""
        def dummy_render(node, inp, cache_dir):
            return f"/tmp/{node.name}.wav"

        node = AudioNode(name="test", fx_type="eq", params={})
        node.is_dirty = True
        node.output_audio_path = None

        with tempfile.TemporaryDirectory() as td:
            executor = _make_chain_executor(dummy_render, td)
            result = executor.execute([node])
            assert len(result) == 1

    def test_execute_chain_all_clean(self):
        """全部干净节点 → 跳过渲染。"""
        def dummy_render(node, inp, cache_dir):
            return f"/tmp/{node.name}.wav"

        node = AudioNode(name="test", fx_type="eq", params={})
        node.is_dirty = False
        node.output_audio_path = "/tmp/exists.wav"
        with patch("os.path.exists", return_value=True):
            with tempfile.TemporaryDirectory() as td:
                result = execute_chain(
                    lambda cdir: lambda n, i: dummy_render(n, i, cdir),
                    [node], cache_dir=td,
                )
                assert len(result) == 1


# ════════════════════════════════════════════════════════════════
# _micro_render_node 路径测试
# ════════════════════════════════════════════════════════════════


class TestMicroRenderNode:
    """覆盖 _micro_render_node 的缓存命中和异常路径。"""

    def test_cache_hit_clean_node(self, tmp_path):
        """is_dirty=False + 文件存在 → 直接返回缓存路径。"""
        node = AudioNode(name="cache_node", fx_type="eq", params={})
        node.is_dirty = False
        cache_file = tmp_path / "cache_node.wav"
        cache_file.write_text("dummy")
        node.output_audio_path = str(cache_file)

        mock_bridge = MagicMock()
        mock_tracks = MagicMock()
        mock_fx = MagicMock()
        solo_render_fn = MagicMock()

        result = _micro_render_node(
            mock_bridge, mock_tracks, mock_fx,
            solo_render_fn, node, "/some/input.wav",
            str(tmp_path),
        )
        assert result == str(cache_file)
        # 不应调用 REAPER 创建轨道
        mock_bridge.api.CountTracks.assert_not_called()

    def test_cache_hit_skips_missing_file(self, tmp_path):
        """is_dirty=False 但缓存文件不存在 + 无输入 → 返回 None。"""
        node = AudioNode(name="gone_node", fx_type="eq", params={})
        node.is_dirty = False
        node.output_audio_path = "/nonexistent/path.wav"

        mock_bridge = MagicMock()
        mock_tracks = MagicMock()
        mock_fx = MagicMock()

        # 缓存文件不存在 + input_wav=None → 返回 None
        result = _micro_render_node(
            mock_bridge, mock_tracks, mock_fx,
            MagicMock(), node, None, str(tmp_path),
        )
        assert result is None
        # 不应尝试创建轨道
        mock_bridge.api.CountTracks.assert_not_called()

    def test_no_input_wav_returns_none(self, tmp_path):
        """input_wav=None → 返回 None。"""
        node = AudioNode(name="no_input", fx_type="eq", params={})
        node.is_dirty = True

        mock_bridge = MagicMock()
        mock_tracks = MagicMock()
        mock_fx = MagicMock()

        result = _micro_render_node(
            mock_bridge, mock_tracks, mock_fx,
            MagicMock(), node, None, str(tmp_path),
        )
        assert result is None

    def test_input_wav_not_exists_returns_none(self, tmp_path):
        """input_wav 路径不存在 → 返回 None。"""
        node = AudioNode(name="no_file", fx_type="eq", params={})
        node.is_dirty = True

        mock_bridge = MagicMock()
        mock_tracks = MagicMock()
        mock_fx = MagicMock()

        result = _micro_render_node(
            mock_bridge, mock_tracks, mock_fx,
            MagicMock(), node, "/not/a/real/file.wav", str(tmp_path),
        )
        assert result is None

    def test_fx_add_fails_returns_none(self, tmp_path):
        """FX 添加失败（返回 -1）→ 返回 None。"""
        node = AudioNode(name="fx_fail", fx_type="eq", params={})
        node.is_dirty = True

        input_wav = tmp_path / "input.wav"
        input_wav.write_text("fake audio")

        mock_bridge = MagicMock()
        mock_bridge.api.CountTracks.return_value = 0
        mock_bridge.api.GetTrack.return_value = "mock_track_ptr"
        mock_tracks = MagicMock()
        mock_fx = MagicMock()
        mock_fx.add.return_value = -1

        result = _micro_render_node(
            mock_bridge, mock_tracks, mock_fx,
            MagicMock(), node, str(input_wav), str(tmp_path),
        )
        assert result is None

    def test_finally_cleanup_on_error(self, tmp_path):
        """渲染异常时 finally 块应清理临时轨道。"""
        node = AudioNode(name="error_node", fx_type="eq", params={})
        node.is_dirty = True

        input_wav = tmp_path / "input2.wav"
        input_wav.write_text("fake audio")

        mock_bridge = MagicMock()
        mock_bridge.api.CountTracks.return_value = 0
        mock_bridge.api.GetTrack.return_value = "mock_ptr"
        mock_tracks = MagicMock()
        # import_media 抛出异常
        mock_tracks.import_media.side_effect = RuntimeError("导入失败")
        mock_fx = MagicMock()
        mock_fx.add.return_value = 0

        # 异常通过 finally 传播，但 finally 先清理
        with pytest.raises(RuntimeError, match="导入失败"):
            _micro_render_node(
                mock_bridge, mock_tracks, mock_fx,
                MagicMock(), node, str(input_wav), str(tmp_path),
            )
        # 验证 DeleteTrack 被调用（finally 清理发生在异常传播之前）
        mock_bridge.api.DeleteTrack.assert_called_once_with("mock_ptr")
