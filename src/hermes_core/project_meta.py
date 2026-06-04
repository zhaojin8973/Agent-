"""工程元数据与索引管理。

每个工程目录下有一份 ``.hermes_meta.json``，记录该工程的完整状态快照。
工程根目录下有一份 ``.hermes_index.json``，汇总所有工程的摘要信息。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from hermes_core.config import HermesConfig

log = logging.getLogger(__name__)

# ── 文件名常量 ─────────────────────────────────────────────────

_META_FILENAME = ".hermes_meta.json"
_INDEX_FILENAME = ".hermes_index.json"

# ── 管线阶段定义 ──────────────────────────────────────────────

# 有序的管线阶段列表（用于判断完成进度）
_PIPELINE_STAGES = [
    "prepare_stems",
    "apply_profile",
    "post_fx_balance",
    "build_spatial_chain",
    "apply_bus_compressor",
    "finalize_master",
]

_STAGE_LABELS: dict[str, str] = {
    "prepare_stems":          "导入分轨",
    "apply_profile":          "效果器链",
    "post_fx_balance":        "推子平衡",
    "build_spatial_chain":    "空间效果器",
    "apply_bus_compressor":   "总线压缩",
    "finalize_master":        "母带响度",
}


# ── Dataclass ─────────────────────────────────────────────────


@dataclass
class ProjectMeta:
    """单个工程的元数据，对应 ``.hermes_meta.json``。"""

    name: str
    category: str = ""
    producer: Optional[str] = None
    genre: str = "pop"
    created_at: str = ""
    last_modified: str = ""

    # 管线状态
    pipeline_stage: str = ""
    pipeline_completed: list[str] = field(default_factory=list)

    # 工程生命周期（规范 §二）
    # created → saved → prepared → imported → mixed → rendered → archived
    lifecycle_state: str = "created"

    # 轨道快照
    track_count: int = 0
    vocal_fx: list[str] = field(default_factory=list)
    backing_fx: list[str] = field(default_factory=list)
    spatial_buses: dict[str, dict] = field(default_factory=dict)
    bus_compressor: dict[str, Any] = field(default_factory=dict)

    # 文件
    audio_files: list[str] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)

    # ── I/O ───────────────────────────────────────────────────

    @classmethod
    def load(cls, project_dir: str | Path) -> Optional[ProjectMeta]:
        """从工程目录加载 ``.hermes_meta.json``。"""
        path = Path(project_dir) / _META_FILENAME
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            # save() 将管线信息写入嵌套的 "pipeline" 键
            pipeline = data.get("pipeline", {})
            # save() 将轨道快照写入嵌套的 "state_snapshot" 键
            snapshot = data.get("state_snapshot", {})
            return cls(
                name=data.get("name", ""),
                category=data.get("category", ""),
                producer=data.get("producer"),
                genre=data.get("genre", "pop"),
                created_at=data.get("created_at", ""),
                last_modified=data.get("last_modified", ""),
                pipeline_stage=pipeline.get("stage", data.get("pipeline_stage", "")),
                pipeline_completed=pipeline.get("completed", data.get("pipeline_completed", [])),
                track_count=snapshot.get("track_count", data.get("track_count", 0)),
                vocal_fx=snapshot.get("vocal_fx", data.get("vocal_fx", [])),
                backing_fx=snapshot.get("backing_fx", data.get("backing_fx", [])),
                spatial_buses=snapshot.get("spatial_buses", data.get("spatial_buses", {})),
                bus_compressor=snapshot.get("bus_compressor", data.get("bus_compressor", {})),
                audio_files=data.get("audio_files", []),
                checkpoints=data.get("checkpoints", []),
                lifecycle_state=data.get("lifecycle_state", "created"),
            )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load meta from %s: %s", path, exc)
            return None

    def save(self, project_dir: str | Path) -> None:
        """将元数据写入工程目录下的 ``.hermes_meta.json``。"""
        now = datetime.now().isoformat(timespec="seconds")
        if not self.created_at:
            self.created_at = now
        self.last_modified = now

        path = Path(project_dir) / _META_FILENAME
        data = {
            "name": self.name,
            "category": self.category,
            "producer": self.producer,
            "genre": self.genre,
            "created_at": self.created_at,
            "last_modified": self.last_modified,
            "lifecycle_state": self.lifecycle_state,
            "pipeline": {
                "stage": self.pipeline_stage,
                "completed": self.pipeline_completed,
                "pending": [
                    s for s in _PIPELINE_STAGES
                    if s not in self.pipeline_completed
                ],
            },
            "state_snapshot": {
                "track_count": self.track_count,
                "vocal_fx": self.vocal_fx,
                "backing_fx": self.backing_fx,
                "spatial_buses": self.spatial_buses,
                "bus_compressor": self.bus_compressor,
            },
            "audio_files": self.audio_files,
            "checkpoints": self.checkpoints,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.debug("Meta saved to %s", path)

    def mark_stage(self, stage: str) -> None:
        """标记一个管线阶段为已完成。"""
        if stage not in self.pipeline_completed:
            self.pipeline_completed.append(stage)
        self.pipeline_stage = stage

    def pending_stages(self) -> list[str]:
        """返回尚未完成的管线阶段。"""
        return [s for s in _PIPELINE_STAGES if s not in self.pipeline_completed]

    def summary(self) -> str:
        """返回人类可读的工程摘要。"""
        lines = [
            f"工程: {self.name}",
            f"流派: {self.genre}",
            f"分类: {self.category or '未分类'}",
        ]
        if self.producer:
            lines.append(f"制作人: {self.producer}")
        lines.append(f"创建: {self.created_at}")
        lines.append(f"修改: {self.last_modified}")
        lines.append(f"轨道数: {self.track_count}")
        if self.vocal_fx:
            lines.append(f"人声FX: {' → '.join(self.vocal_fx)}")
        if self.backing_fx:
            lines.append(f"伴奏FX: {' → '.join(self.backing_fx)}")
        lines.append(f"管线阶段: {_STAGE_LABELS.get(self.pipeline_stage, self.pipeline_stage or '未开始')}")
        completed_labels = [
            _STAGE_LABELS.get(s, s) for s in self.pipeline_completed
        ]
        lines.append(f"已完成: {', '.join(completed_labels) if completed_labels else '无'}")
        pending_labels = [
            _STAGE_LABELS.get(s, s) for s in self.pending_stages()
        ]
        lines.append(f"待完成: {', '.join(pending_labels) if pending_labels else '无'}")
        if self.spatial_buses:
            lines.append("空间总线:")
            for bus, info in self.spatial_buses.items():
                plugin = info.get("plugin", "?")
                level = info.get("level_db", "?")
                lines.append(f"  {bus}: {plugin} @ {level} dB")
        lines.append(f"生命周期: {_LIFECYCLE_LABELS.get(self.lifecycle_state, self.lifecycle_state)}")
        return "\n".join(lines)

    def update_lifecycle(self) -> str:
        """根据管线完成情况自动推断生命周期状态。

        规范 §二 映射：
            created   — 目录已创建
            saved     — RPP 已保存
            prepared  — prepare_stems 完成
            imported  — 音频已导入轨道
            mixed     — 全部 6 段管线完成
            rendered  — finalize_master 完成
            archived  — 手动标记
        """
        completed = set(self.pipeline_completed)
        if "finalize_master" in completed:
            self.lifecycle_state = "rendered"
        elif len(completed) >= 5:  # 5/6 以上 = mixed
            self.lifecycle_state = "mixed"
        elif "prepare_stems" in completed:
            self.lifecycle_state = "imported"
        elif self.track_count > 0:
            self.lifecycle_state = "prepared"
        elif self.last_modified:
            self.lifecycle_state = "saved"
        else:
            self.lifecycle_state = "created"
        return self.lifecycle_state


# ── 生命周期标签 ──────────────────────────────────────────────

_LIFECYCLE_LABELS: dict[str, str] = {
    "created":   "目录已创建",
    "saved":     "RPP 已保存",
    "prepared":  "素材就绪",
    "imported":  "音频已导入",
    "mixed":     "混音完成",
    "rendered":  "已渲染",
    "archived":  "已归档",
}


# ── 索引管理 ──────────────────────────────────────────────────


@dataclass
class ProjectIndex:
    """工程总索引，对应 ``.hermes_index.json``。

    提供快速扫描：不打开每个 ``.hermes_meta.json`` 就能看到
    所有工程的概况。
    """

    projects: dict[str, dict] = field(default_factory=dict)
    last_scanned: str = ""

    # ── I/O ───────────────────────────────────────────────────

    @classmethod
    def load(cls, root_dir: str | Path | None = None) -> ProjectIndex:
        """从工程根目录加载索引。"""
        root = _resolve_root(root_dir)
        path = root / _INDEX_FILENAME
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                projects=data.get("projects", {}),
                last_scanned=data.get("last_scanned", ""),
            )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load index: %s", exc)
            return cls()

    def save(self, root_dir: str | Path | None = None) -> None:
        """将索引写入工程根目录。"""
        root = _resolve_root(root_dir)
        root.mkdir(parents=True, exist_ok=True)
        self.last_scanned = datetime.now().isoformat(timespec="seconds")
        path = root / _INDEX_FILENAME
        data = {
            "version": 1,
            "last_scanned": self.last_scanned,
            "projects": self.projects,
        }
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.debug("Index saved to %s", path)

    # ── 操作 ──────────────────────────────────────────────────

    def scan(self, root_dir: str | Path | None = None) -> int:
        """全量扫描工程根目录，重建索引。

        遍历所有子目录，寻找 ``.hermes_meta.json``，
        提取摘要信息更新索引。返回发现的工程数。
        """
        root = _resolve_root(root_dir)
        if not root.exists():
            log.warning("Project root does not exist: %s", root)
            return 0

        found = 0
        self.projects.clear()

        for meta_path in root.rglob(_META_FILENAME):
            project_dir = meta_path.parent
            meta = ProjectMeta.load(project_dir)
            if meta is None:
                continue

            # 相对于根目录的路径作为 key
            rel_path = str(project_dir.relative_to(root))
            self.projects[rel_path] = {
                "name": meta.name,
                "genre": meta.genre,
                "category": meta.category or "",
                "producer": meta.producer,
                "created": meta.created_at[:10] if meta.created_at else "",
                "last_modified": meta.last_modified[:10] if meta.last_modified else "",
                "stage": meta.pipeline_stage,
                "track_count": meta.track_count,
            }
            found += 1

        self.save(root_dir=root)
        log.info("Index scan complete: %d projects found in %s", found, root)
        return found

    def add_or_update(self, rel_path: str, meta: ProjectMeta,
                      root_dir: str | Path | None = None) -> None:
        """添加或更新索引中的一个工程条目。"""
        self.projects[rel_path] = {
            "name": meta.name,
            "genre": meta.genre,
            "category": meta.category or "",
            "producer": meta.producer,
            "created": meta.created_at[:10] if meta.created_at else "",
            "last_modified": meta.last_modified[:10] if meta.last_modified else "",
            "stage": meta.pipeline_stage,
            "track_count": meta.track_count,
        }
        self.save(root_dir=root_dir)

    def remove(self, rel_path: str, root_dir: str | Path | None = None) -> bool:
        """从索引中移除一个工程。返回是否确实有东西被移除。"""
        if rel_path in self.projects:
            del self.projects[rel_path]
            self.save(root_dir=root_dir)
            return True
        return False

    def find(self, name: str) -> list[tuple[str, dict]]:
        """按工程名模糊搜索。返回 [(rel_path, entry), ...]。"""
        name_lower = name.lower()
        return [
            (p, e) for p, e in self.projects.items()
            if name_lower in e.get("name", "").lower()
        ]

    def list_all(self) -> list[tuple[str, dict]]:
        """返回所有工程，按最后修改时间倒序。"""
        items = list(self.projects.items())
        items.sort(key=lambda x: x[1].get("last_modified", ""), reverse=True)
        return items

    def filter_by(self, genre: str | None = None,
                  stage: str | None = None,
                  category: str | None = None) -> list[tuple[str, dict]]:
        """按条件筛选工程。"""
        result = []
        for path, entry in self.projects.items():
            if genre and entry.get("genre") != genre:
                continue
            if stage and entry.get("stage") != stage:
                continue
            if category and category.lower() not in entry.get("category", "").lower():
                continue
            result.append((path, entry))
        result.sort(key=lambda x: x[1].get("last_modified", ""), reverse=True)
        return result


# ── 工具函数 ──────────────────────────────────────────────────


def _resolve_root(root_dir: str | Path | None = None) -> Path:
    """解析工程根目录路径。"""
    if root_dir is None:
        cfg = HermesConfig.load()
        root_dir = cfg.project_root
    return Path(root_dir).expanduser().resolve()


def extract_name_from_audio(filepath: str) -> str:
    """从音频文件名提取可能的歌曲名。

    "望归 Vocal.wav"    → "望归"
    "望归 伴奏（测试）.wav"  → "望归"
    "Drum Kick.wav"      → "Drum Kick"

    规则：取第一个空格或括号之前的部分作为候选名称。
    如果候选名称少于 2 个中文字符，返回完整文件名（不含扩展名）。
    """
    name = os.path.splitext(os.path.basename(filepath))[0]
    # 按常见分隔符拆分
    for sep in [" Vocal", " 人声", " 伴奏", " Backing", "（", "("]:
        if sep in name:
            name = name.split(sep)[0]
            break
    return name.strip()


def make_project_path(name: str, category: str = "",
                      root_dir: str | Path | None = None) -> Path:
    """构建工程目录路径。

    ``~/REAPER 工程文件/{category}/{name}/``
    """
    root = _resolve_root(root_dir)
    if category:
        return root / category / name
    return root / "未分类" / name


# ── 标准子目录 ──────────────────────────────────────────────

# 每个工程必须创建的标准子目录（规范 §三）
STANDARD_SUBDIRS = [
    "Audio",       # 音频素材（本地化）
    "Renders",     # 渲染输出
    "Stems",       # 分轨文件
    "References",  # 参考音频
    "Backups",     # .rpp-bak 自动备份
    "Notes",       # 混音笔记
]


def create_project_dirs(project_path: str | Path) -> dict[str, Path]:
    """在工程目录下创建所有标准子目录。

    幂等 — 已存在的目录不会报错。

    Returns
    -------
    dict
        ``{"Audio": Path, "Renders": Path, ...}``
    """
    p = Path(project_path)
    p.mkdir(parents=True, exist_ok=True)
    created = {}
    for sub in STANDARD_SUBDIRS:
        d = p / sub
        d.mkdir(exist_ok=True)
        created[sub] = d
    return created
