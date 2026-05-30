"""
Audio-node pipeline — DAG-based FX chain with dirty-flag cascade invalidation.

Each effect plugin is modelled as an :class:`AudioNode`.  Nodes are linked in
insert order (linear chain for the main track) or as observers (SendNode for
parallel sends).  When a node's parameters change, dirty flags propagate
downstream so only the affected portion of the chain is re-executed.

Architecture
------------
::

    [EQ 1] → [Comp 1] → [EQ 2] → Master
       │
       └──→ [SendNode: Reverb] → Master  (observer, read-only)

- **EQ nodes**: auto RMS matching prevents unnecessary cascade invalidation.
- **SendNode**: source dirty → send dirty / send dirty → source **not** dirty
  (asymmetric dependency).
- **Lazy execution**: :meth:`ChainExecutor.execute` only re-processes nodes
  whose ``is_dirty`` flag is set, reusing cached outputs for clean nodes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

log = logging.getLogger(__name__)


# ════════════════════════════════════════════════════════════════
# AudioNode
# ════════════════════════════════════════════════════════════════


@dataclass
class AudioNode:
    """A single processing node in an FX chain.

    Parameters
    ----------
    name:
        Unique identifier, e.g. ``"Vocal_EQ_1"``.
    fx_type:
        Plugin category — ``"eq"``, ``"comp"``, ``"reverb"``, etc.
    params:
        Current physical parameter dict (dB, ms, ratio — not normalised).
    """

    name: str
    fx_type: str
    params: dict = field(default_factory=dict)

    # ── state ──
    is_dirty: bool = field(default=True, init=False)
    input_audio_path: Optional[str] = field(default=None, init=False)
    output_audio_path: Optional[str] = field(default=None, init=False)

    # ── topology ──
    downstream: list[AudioNode] = field(default_factory=list, init=False)
    observers: list[SendNode] = field(default_factory=list, init=False)

    # ── lazy RMS snapshot (for EQ gain compensation) ──
    _rms_snapshot_db: Optional[float] = field(default=None, init=False)

    def add_downstream(self, node: AudioNode) -> None:
        """Link *node* as a downstream dependency."""
        if node not in self.downstream:
            self.downstream.append(node)

    def add_observer(self, observer: SendNode) -> None:
        """Register a :class:`SendNode` that observes this node's output."""
        if observer not in self.observers:
            self.observers.append(observer)

    def update_params(self, new_params: dict) -> bool:
        """Update *params* and trigger cascade invalidation if changed.

        Returns ``True`` when the params actually changed (dirty cascade
        was triggered), ``False`` otherwise.
        """
        if self.params == new_params:
            return False
        log.debug("[DAG] %s params changed — invalidating", self.name)
        self.params = dict(new_params)
        self.invalidate()
        return True

    def invalidate(self) -> None:
        """Mark self and all downstream nodes dirty; clear output caches.

        Observers (SendNode) are also dirtied — the source signal changed
        so the send must re-render.  The *reverse* is not true: dirtying a
        SendNode does **not** dirty its source.
        """
        if not self.is_dirty:
            self.is_dirty = True
            self.output_audio_path = None

        for observer in self.observers:
            if not observer.is_dirty:
                observer.invalidate()

        for node in self.downstream:
            if not node.is_dirty:
                node.invalidate()

    def mark_clean(self, output_path: Optional[str] = None) -> None:
        """Clear the dirty flag and optionally set the output cache path."""
        self.is_dirty = False
        if output_path is not None:
            self.output_audio_path = output_path


# ════════════════════════════════════════════════════════════════
# SendNode (observer)
# ════════════════════════════════════════════════════════════════


class SendNode(AudioNode):
    """A parallel send / aux node that observes a main-chain source.

    Inherits all :class:`AudioNode` behaviour with one critical difference:
    when *this* node is invalidated the **source node is NOT dirtied**.
    Changing reverb level does not require re-analysing the dry vocal chain.

    Parameters
    ----------
    source_node:
        The main-chain :class:`AudioNode` whose output feeds this send.
    """

    def __init__(self, name: str, fx_type: str, source_node: AudioNode, **kwargs):
        super().__init__(name=name, fx_type=fx_type, **kwargs)
        self.source_node = source_node
        source_node.add_observer(self)

    def invalidate(self) -> None:
        """Dirty self and downstream (but NOT the source — asymmetric)."""
        if not self.is_dirty:
            self.is_dirty = True
            self.output_audio_path = None

        for observer in self.observers:
            if not observer.is_dirty:
                observer.invalidate()

        for node in self.downstream:
            if not node.is_dirty:
                node.invalidate()


# ════════════════════════════════════════════════════════════════
# ChainExecutor
# ════════════════════════════════════════════════════════════════


class ChainExecutor:
    """Lazy-execution runner for an ordered list of :class:`AudioNode`.

    ``execute(nodes)`` walks the list from left to right.  Clean nodes
    whose cached output is still valid are **skipped**.  Dirty nodes
    are handed to a user-supplied ``process_fn`` callback which is
    responsible for running REAPER micro-renders and updating the
    node's ``output_audio_path``.
    """

    def __init__(self, process_fn):
        """
        Parameters
        ----------
        process_fn:
            ``Callable[[AudioNode, str | None], str | None]`` —
            called as ``process_fn(node, input_path)`` and must return
            the output WAV path (or ``None`` on failure).  *input_path*
            is the previous node's ``output_audio_path`` (or ``None``
            for the first node).
        """
        self._process = process_fn

    def execute(self, nodes: list[AudioNode]) -> list[AudioNode]:
        """Process dirty nodes; clean nodes with cached output are skipped.

        Returns *nodes* (mutated in-place — dirty flags cleared on success).
        """
        input_path: Optional[str] = None

        for node in nodes:
            if isinstance(node, SendNode):
                # SendNode reads from its source, not the previous chain node
                src_output = node.source_node.output_audio_path
                if node.is_dirty or node.output_audio_path is None:
                    node.input_audio_path = src_output
                    out = self._process(node, src_output)
                    node.mark_clean(out)
                continue

            if not node.is_dirty and node.output_audio_path is not None:
                # Cache hit — reuse
                input_path = node.output_audio_path
                continue

            node.input_audio_path = input_path
            out = self._process(node, input_path)
            node.mark_clean(out)
            input_path = out

        return nodes

    @staticmethod
    def first_dirty(nodes: list[AudioNode]) -> int:
        """Return the index of the first dirty node, or -1 if all clean."""
        for i, node in enumerate(nodes):
            if node.is_dirty:
                return i
        return -1
