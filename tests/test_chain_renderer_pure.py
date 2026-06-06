"""chain_renderer 纯函数测试 — 无需 REAPER。"""
import tempfile
from unittest.mock import MagicMock, patch
from hermes_core.dag import AudioNode
from hermes_core.chain_renderer import (
    _init_translators, _make_chain_executor, execute_chain,
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
