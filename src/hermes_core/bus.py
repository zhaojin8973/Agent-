"""
Folder bus management — create, dissolve, and validate folder track groups.

REAPER I_FOLDERDEPTH semantics:
  0  = normal track (no folder relationship)
  1  = folder PARENT — contains all tracks below until depth < 0
  -1 = last child in folder — closes the nearest parent folder above
  -2 = last child in TWO nested folders (closes two levels, etc.)
"""

import logging
from dataclasses import dataclass
from typing import Optional

from hermes_core.bridge import ReaperBridge

log = logging.getLogger(__name__)


@dataclass
class FolderInfo:
    index: int
    name: str
    depth: int
    children: list["FolderInfo"]

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "name": self.name,
            "depth": self.depth,
            "children": [c.to_dict() for c in self.children],
        }


class BusManager:
    """Create, dissolve, and validate REAPER folder track groups."""

    def __init__(self, bridge: ReaperBridge):
        self._bridge = bridge

    # ── Create / Dissolve ────────────────────────────────────

    def create_bus(self, name: str, child_indices: list[int],
                   position: Optional[int] = None) -> int:
        """Create a folder bus containing the given child tracks.

        The bus is inserted just before the first child. Returns the bus track index.
        Children must be adjacent and in order — this method sets folder depth flags
        but does NOT reorder tracks.

        同时设置 VCA 分组：bus track 为 VCA Master，子轨道为 VCA Slave，
        使用 ReaScript API ``GetSetTrackGroupMembership`` 配置。
        """
        api = self._bridge.api
        num = api.CountTracks(0)

        if not child_indices:
            pos = position if position is not None else num
            api.InsertTrackAtIndex(pos, True)
            tr = api.GetTrack(0, pos)
            if tr:
                api.GetSetMediaTrackInfo_String(tr, "P_NAME", name, True)
                api.SetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH", 1)
            log.info("Bus created (no children): %s at %d", name, pos)
            return pos

        sorted_children = sorted(child_indices)
        for idx in sorted_children:
            if idx < 0 or idx >= num:
                raise ValueError(f"Child track {idx} out of range (0-{num - 1})")

        insert_at = sorted_children[0]
        api.InsertTrackAtIndex(insert_at, True)
        bus_tr = api.GetTrack(0, insert_at)
        if not bus_tr:
            raise RuntimeError("Failed to create bus track")

        api.GetSetMediaTrackInfo_String(bus_tr, "P_NAME", name, True)
        api.SetMediaTrackInfo_Value(bus_tr, "I_FOLDERDEPTH", 1)

        # Children shift by 1 after bus insertion
        shifted = [c + 1 for c in sorted_children]
        for child_idx in shifted:
            child_tr = api.GetTrack(0, child_idx)
            if child_tr:
                api.SetMediaTrackInfo_Value(child_tr, "I_FOLDERDEPTH", 0)
        last_idx = shifted[-1]
        last_tr = api.GetTrack(0, last_idx)
        if last_tr:
            api.SetMediaTrackInfo_Value(last_tr, "I_FOLDERDEPTH", -1)

        # ── VCA 分组 ──────────────────────────────────────────
        # 使用 GetSetTrackGroupMembership 设置 VCA Master/Slave 关系。
        # group_low 是 32 位位掩码，bit 0 = group 1, bit 31 = group 32。
        # group_high 对应 group 33-64。
        # 组号使用 insert_at + 1（1-indexed），限制在 1-64 范围内。
        vca_group = min(insert_at + 1, 64)
        try:
            if hasattr(api, "GetSetTrackGroupMembership"):
                # 计算位掩码：group_low 对应 group 1-32
                if vca_group <= 32:
                    group_mask_low = 1 << (vca_group - 1)
                    group_mask_high = 0
                else:
                    group_mask_low = 0
                    group_mask_high = 1 << (vca_group - 33)

                # Bus track = VCA Master
                api.GetSetTrackGroupMembership(
                    bus_tr, "VOLUME_VCA_MASTER",
                    group_mask_high, group_mask_low,
                    group_mask_high, group_mask_low,
                )

                # 子轨道 = VCA Slave
                for child_idx in shifted:
                    child_tr = api.GetTrack(0, child_idx)
                    if child_tr:
                        api.GetSetTrackGroupMembership(
                            child_tr, "VOLUME_VCA_SLAVE",
                            group_mask_high, group_mask_low,
                            group_mask_high, group_mask_low,
                        )

                log.info(
                    "VCA group %d: bus '%s' (master) + %d children (slaves)",
                    vca_group, name, len(shifted),
                )
            else:
                log.debug(
                    "VCA API not available (GetSetTrackGroupMembership missing) "
                    "— folder bus '%s' created without VCA grouping", name,
                )
        except Exception as exc:
            log.debug(
                "VCA group setup failed for bus '%s': %s — "
                "folder bus works without VCA grouping",
                name, exc,
            )

        log.info("Bus '%s' created at %d with %d children", name, insert_at, len(shifted))
        return insert_at

    def dissolve_bus(self, bus_index: int):
        """Reset all FOLDERDEPTH flags in a bus group to 0 (flat tracks)."""
        api = self._bridge.api
        bus_tr = api.GetTrack(0, bus_index)
        if not bus_tr:
            raise ValueError(f"Bus track {bus_index} not found")

        depth = int(api.GetMediaTrackInfo_Value(bus_tr, "I_FOLDERDEPTH"))
        if depth != 1:
            log.warning("Track %d is not a folder parent (depth=%d)", bus_index, depth)

        api.SetMediaTrackInfo_Value(bus_tr, "I_FOLDERDEPTH", 0)
        num = api.CountTracks(0)
        nested = 0
        for i in range(bus_index + 1, num):
            tr = api.GetTrack(0, i)
            if not tr:
                continue
            d = int(api.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"))
            if d == 1:
                nested += 1
            elif d < 0:
                if nested == 0:
                    api.SetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH", 0)
                    break
                nested += d
        log.info("Bus at %d dissolved", bus_index)

    # ── Query ────────────────────────────────────────────────

    def get_structure(self) -> list[FolderInfo]:
        """Return the complete folder tree of the project."""
        api = self._bridge.api
        num = api.CountTracks(0)

        def _walk(start, stop_at_depth_lt):
            result = []
            i = start
            while i < num:
                tr = api.GetTrack(0, i)
                if not tr:
                    i += 1
                    continue
                depth = int(api.GetTrackDepth(tr))
                if depth < stop_at_depth_lt:
                    return result, i
                name_r = api.GetTrackName(tr, "", 256)
                name = name_r[2] if isinstance(name_r, (tuple, list)) and len(name_r) > 2 else str(name_r or "")
                folder_depth = int(api.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"))
                if folder_depth == 1:
                    children, next_i = _walk(i + 1, depth + 1)
                    result.append(FolderInfo(index=i, name=name or "", depth=depth, children=children))
                    i = next_i
                else:
                    result.append(FolderInfo(index=i, name=name or "", depth=depth, children=[]))
                    i += 1
            return result, num

        tree, _ = _walk(0, 0)
        return tree

    def validate(self, bus_index: int) -> dict:
        """Validate folder structure integrity for a bus group."""
        api = self._bridge.api
        bus_tr = api.GetTrack(0, bus_index)
        if not bus_tr:
            return {"valid": False, "error": f"Track {bus_index} not found"}

        issues = []
        depth = int(api.GetMediaTrackInfo_Value(bus_tr, "I_FOLDERDEPTH"))
        if depth != 1:
            issues.append(f"Bus track {bus_index} has I_FOLDERDEPTH={depth}, expected 1")

        children = self._get_child_indices(bus_index)
        if not children:
            issues.append(f"Bus {bus_index} has no children — not a valid folder")

        if children:
            last_tr = api.GetTrack(0, children[-1])
            if last_tr:
                last_depth = int(api.GetMediaTrackInfo_Value(last_tr, "I_FOLDERDEPTH"))
                if last_depth >= 0:
                    issues.append(
                        f"Last child {children[-1]} has I_FOLDERDEPTH={last_depth}, expected -1"
                    )

        return {
            "valid": len(issues) == 0,
            "bus_index": bus_index,
            "child_count": len(children),
            "children": children,
            "issues": issues,
        }

    # ── Internal ──────────────────────────────────────────────

    def _get_child_indices(self, bus_index: int) -> list[int]:
        api = self._bridge.api
        num = api.CountTracks(0)
        children = []
        nested = 0
        for i in range(bus_index + 1, num):
            tr = api.GetTrack(0, i)
            if not tr:
                continue
            d = int(api.GetMediaTrackInfo_Value(tr, "I_FOLDERDEPTH"))
            if d == 1:
                nested += 1
                children.append(i)
            elif d < 0:
                nested += d
                if nested < 0:
                    children.append(i)
                    break
                children.append(i)
            else:
                children.append(i)
        return children
