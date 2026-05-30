"""Tests for hermes_core.dag — AudioNode, SendNode, ChainExecutor."""

import pytest
from hermes_core.dag import AudioNode, SendNode, ChainExecutor


# ════════════════════════════════════════════════════════════
# AudioNode basics
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestAudioNode:
    def test_default_dirty(self):
        node = AudioNode(name="test", fx_type="eq")
        assert node.is_dirty is True

    def test_mark_clean(self):
        node = AudioNode(name="test", fx_type="eq")
        node.mark_clean("/tmp/out.wav")
        assert node.is_dirty is False
        assert node.output_audio_path == "/tmp/out.wav"

    def test_update_params_no_change(self):
        node = AudioNode(name="test", fx_type="eq", params={"gain": 1.0})
        node.mark_clean()
        changed = node.update_params({"gain": 1.0})
        assert changed is False
        assert node.is_dirty is False

    def test_update_params_triggers_dirty(self):
        node = AudioNode(name="test", fx_type="eq", params={"gain": 1.0})
        node.mark_clean()
        changed = node.update_params({"gain": 2.0})
        assert changed is True
        assert node.is_dirty is True
        assert node.output_audio_path is None


# ════════════════════════════════════════════════════════════
# Dirty flag cascade
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestCascadeInvalidation:
    def make_chain(self, n=3):
        nodes = [AudioNode(name=f"node_{i}", fx_type="comp")
                 for i in range(n)]
        for i in range(n - 1):
            nodes[i].add_downstream(nodes[i + 1])
        for n in nodes:
            n.mark_clean()
        return nodes

    def test_mid_chain_change_cascades_down(self):
        nodes = self.make_chain(4)
        # Change node 1 → nodes 1,2,3 should be dirty; node 0 should stay clean
        nodes[1].update_params({"threshold": -12.0})
        assert nodes[0].is_dirty is False
        assert nodes[1].is_dirty is True
        assert nodes[2].is_dirty is True
        assert nodes[3].is_dirty is True

    def test_first_node_change_cascades_all(self):
        nodes = self.make_chain(3)
        nodes[0].update_params({"gain": 1.0})
        assert nodes[0].is_dirty is True
        assert nodes[1].is_dirty is True
        assert nodes[2].is_dirty is True

    def test_last_node_change_no_downstream(self):
        nodes = self.make_chain(3)
        nodes[2].update_params({"release": 200})
        assert nodes[0].is_dirty is False
        assert nodes[1].is_dirty is False
        assert nodes[2].is_dirty is True

    def test_redundant_cascade_does_not_loop(self):
        """Dirtying a node cascades only downstream, not upstream."""
        nodes = self.make_chain(3)
        # nodes[1] dirty → cascades to nodes[2], NOT to nodes[0]
        nodes[1].invalidate()
        assert nodes[1].is_dirty is True
        assert nodes[2].is_dirty is True   # downstream
        assert nodes[0].is_dirty is False  # upstream unaffected


# ════════════════════════════════════════════════════════════
# SendNode (observer pattern)
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestSendNode:
    def test_observes_source(self):
        source = AudioNode(name="vocal_last", fx_type="comp")
        source.mark_clean()
        send = SendNode(name="verb", fx_type="reverb", source_node=source)
        send.mark_clean()
        assert send in source.observers
        assert send.source_node is source

    def test_source_dirty_dirts_send(self):
        source = AudioNode(name="vocal_last", fx_type="comp")
        source.mark_clean()
        send = SendNode(name="verb", fx_type="reverb", source_node=source)
        send.mark_clean()

        source.invalidate()
        assert send.is_dirty is True

    def test_send_dirty_does_not_dirt_source(self):
        """Asymmetric: changing reverb does NOT dirty the dry chain."""
        source = AudioNode(name="vocal_last", fx_type="comp")
        source.mark_clean()
        send = SendNode(name="verb", fx_type="reverb", source_node=source)
        send.mark_clean()

        send.invalidate()
        assert send.is_dirty is True
        assert source.is_dirty is False

    def test_send_does_not_dirty_other_chain_nodes(self):
        node_a = AudioNode(name="a", fx_type="eq")
        node_b = AudioNode(name="b", fx_type="comp")
        node_a.add_downstream(node_b)
        node_a.mark_clean()
        node_b.mark_clean()

        send = SendNode(name="verb", fx_type="reverb", source_node=node_b)
        send.mark_clean()
        send.invalidate()

        assert node_a.is_dirty is False
        assert node_b.is_dirty is False  # NOT dirtied by send


# ════════════════════════════════════════════════════════════
# ChainExecutor
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestChainExecutor:
    def test_skips_clean_nodes(self):
        nodes = [AudioNode(name=f"n{i}", fx_type="eq") for i in range(3)]
        for n in nodes:
            n.mark_clean(f"/tmp/{n.name}.wav")

        called = []
        executor = ChainExecutor(lambda node, inp: called.append(node.name) or node.output_audio_path)
        executor.execute(nodes)
        assert called == []  # all clean — nothing processed

    def test_processes_from_first_dirty(self):
        nodes = [AudioNode(name=f"n{i}", fx_type="eq") for i in range(3)]
        # Link them: n0 → n1 → n2
        nodes[0].add_downstream(nodes[1])
        nodes[1].add_downstream(nodes[2])

        nodes[0].mark_clean("/tmp/n0.wav")
        nodes[1].mark_clean("/tmp/n1.wav")
        nodes[2].mark_clean("/tmp/n2.wav")
        # dirty n1 → cascades to n2
        nodes[1].invalidate()

        called = []
        def proc(node, inp):
            called.append(node.name)
            return f"/tmp/{node.name}_new.wav"

        executor = ChainExecutor(proc)
        executor.execute(nodes)

        assert "n0" not in called  # clean, skipped
        assert "n1" in called
        assert "n2" in called  # cascaded dirty from n1

    def test_first_dirty_index(self):
        nodes = [AudioNode(name=f"n{i}", fx_type="eq") for i in range(3)]
        for n in nodes:
            n.mark_clean()
        assert ChainExecutor.first_dirty(nodes) == -1

        nodes[1].invalidate()
        assert ChainExecutor.first_dirty(nodes) == 1

        nodes[0].invalidate()
        assert ChainExecutor.first_dirty(nodes) == 0

    def test_send_node_reads_from_source(self):
        source = AudioNode(name="vocal", fx_type="comp")
        source.mark_clean("/tmp/vocal.wav")
        send = SendNode(name="verb", fx_type="reverb", source_node=source)
        send.invalidate()

        called = []
        def proc(node, inp):
            called.append((node.name, inp))
            return f"/tmp/{node.name}.wav"

        executor = ChainExecutor(proc)
        executor.execute([source, send])

        # source is clean → skipped
        # send reads from source.output_audio_path
        assert len(called) == 1
        assert called[0][1] == "/tmp/vocal.wav"


# ════════════════════════════════════════════════════════════
# Integration: chain builder in engine
# ════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestEngineChainIntegration:
    def test_update_node_param_cascades(self):
        """update_node_param on EQ triggers dirty cascade downstream."""
        from hermes_core.engine import MixingEngine

        eng = MixingEngine()
        n1 = AudioNode(name="EQ1", fx_type="eq")
        n2 = AudioNode(name="CMP1", fx_type="comp")
        n1.add_downstream(n2)
        n1.mark_clean()
        n2.mark_clean()

        eng.update_node_param(n1, "freq", 2000.0)
        assert n1.is_dirty is True
        assert n2.is_dirty is True  # cascade
        assert n1.params["freq"] == 2000.0

    def test_update_send_level_does_not_dirty_source(self):
        """Changing reverb send level doesn't dirty the main chain."""
        from hermes_core.engine import MixingEngine

        source = AudioNode(name="vocal_cmp", fx_type="comp")
        source.mark_clean()
        send = SendNode(name="verb", fx_type="reverb", source_node=source)
        send.mark_clean()

        eng = MixingEngine()
        eng.update_node_param(send, "level_db", -6.0)
        assert send.is_dirty is True
        assert source.is_dirty is False
