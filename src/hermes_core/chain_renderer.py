"""
微渲染管线 — 将 DAG AudioNode 链渲染为独立缓存 WAV 文件。

每个节点被渲染到临时 REAPER 轨道，应用 FX 和参数，独奏渲染，
然后清理临时轨道。干净的节点（is_dirty == False）在有有效缓存时被跳过。
"""

from __future__ import annotations

import logging
import os
import shutil
import tempfile
from typing import TYPE_CHECKING, Callable

from hermes_core.dag import AudioNode, ChainExecutor
from hermes_core.normalize import normalize_params

if TYPE_CHECKING:
    from hermes_core.bridge import ReaperBridge
    from hermes_core.track import TrackManager
    from hermes_core.fx import FxManager

log = logging.getLogger(__name__)

# ── 压缩器翻译器字典（模块级，与 engine.py 中的相同）──
# 注意：rvox 和 cla-76 被排除，因为它们需要特殊处理。
_TRANSLATORS = {
    "vca":  None,  # 占位 — 实际翻译函数在 comp_engine 中
    "fet":  None,
    "opto": None,
}


def _init_translators() -> None:
    """惰性导入压缩器翻译函数，避免循环导入。"""
    from hermes_core.comp_engine import (
        _apply_vca_params,
        _apply_fet_params,
        _apply_opto_params,
    )
    _TRANSLATORS["vca"] = _apply_vca_params
    _TRANSLATORS["fet"] = _apply_fet_params
    _TRANSLATORS["opto"] = _apply_opto_params


def _micro_render_node(
    bridge: "ReaperBridge",
    tracks: "TrackManager",
    fx: "FxManager",
    solo_render_fn: Callable[[list[int], str, str], dict],
    node: AudioNode,
    input_wav: str | None,
    cache_dir: str,
) -> str | None:
    """渲染单个 :class:`AudioNode` 到缓存的 WAV。

    创建临时轨道，导入 *input_wav*，添加 FX，设置参数，
    独奏渲染，然后清理。

    返回输出 WAV 路径，失败时返回 ``None``。
    """
    # ── 缓存命中：干净的节点且有有效输出 ──
    if not node.is_dirty and node.output_audio_path:
        if os.path.exists(node.output_audio_path):
            log.debug("[micro] %s cache hit → %s", node.name,
                      node.output_audio_path)
            return node.output_audio_path

    if input_wav is None or not os.path.exists(input_wav):
        log.warning("[micro] %s: no input WAV — skipping", node.name)
        return None

    os.makedirs(cache_dir, exist_ok=True)
    out_path = os.path.join(cache_dir, f"{node.name}.wav")

    # ── 清理过期输出 ──
    if os.path.exists(out_path):
        os.remove(out_path)

    api = bridge.api
    n_before = api.CountTracks(0)

    # ── 创建临时轨道 ──
    api.InsertTrackAtIndex(n_before, True)
    temp_track_idx = n_before
    temp_track = api.GetTrack(0, temp_track_idx)

    try:
        # ── 导入媒体 ──
        tracks.import_media(temp_track_idx, input_wav, position=0.0)

        # ── 添加 FX + 设置参数 ──
        fx_idx = fx.add(temp_track_idx, node.params.get("_fx_name", ""))
        if fx_idx < 0:
            log.warning("[micro] %s: failed to add FX", node.name)
            return None

        fx_type = node.fx_type
        if fx_type in _TRANSLATORS and _TRANSLATORS[fx_type] is not None:
            # 重新推导物理参数（自构建以来可能已更改）
            normalized = normalize_params(
                node.params.get("_fx_name", ""),
                {k: v for k, v in node.params.items()
                 if not k.startswith("_")},
            )
            for pname, pval in normalized.items():
                fx.set_param(temp_track_idx, fx_idx, pname, pval)

        # ── 独奏渲染 ──
        render_result = solo_render_fn(
            [temp_track_idx], cache_dir, node.name,
        )
        rendered = render_result.get("output_path")
        if rendered and os.path.exists(rendered):
            shutil.move(rendered, out_path)

        if os.path.exists(out_path):
            node.mark_clean(out_path)
            log.info("[micro] %s rendered → %s", node.name, out_path)
            return out_path

        return None

    finally:
        # ── 清理临时轨道 ──
        try:
            api.DeleteTrack(temp_track)
        except Exception as e:
            log.debug("Failed to clean up temp track: %s", e)


def _make_chain_executor(
    micro_render_fn: Callable[..., str | None],
    cache_dir: str,
) -> ChainExecutor:
    """返回一个绑定到 *micro_render_fn* 的 :class:`ChainExecutor`。"""
    return ChainExecutor(
        lambda node, inp: micro_render_fn(node, inp, cache_dir)
    )


def execute_chain(
    micro_render_factory: Callable[[str], Callable[..., str | None]],
    nodes: list[AudioNode],
    cache_dir: str | None = None,
) -> list[AudioNode]:
    """通过微渲染执行 *nodes*，复用缓存输出。

    *micro_render_factory* 是一个回调，接受 ``cache_dir`` 返回
    ``(node, input_wav) -> output_path`` 的渲染函数。

    脏节点会被重新渲染；有有效缓存的干净节点会被跳过。
    返回（已变异的）节点列表。
    """
    cdir = cache_dir or tempfile.mkdtemp(prefix="hermes_chain_")
    render_fn = micro_render_factory(cdir)
    executor = _make_chain_executor(render_fn, cdir)
    first = executor.first_dirty(nodes)
    if first < 0:
        log.info("[chain] All %d nodes clean — nothing to render", len(nodes))
        return nodes
    log.info("[chain] Executing from node %d/%d (%s)", first,
             len(nodes), nodes[first].name)
    return executor.execute(nodes)
