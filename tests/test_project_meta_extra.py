"""project_meta 纯函数测试 — 路径操作和元数据。"""
import os
from unittest.mock import MagicMock, patch
from hermes_core.project_meta import (
    ProjectMeta, ProjectIndex, make_project_path, create_project_dirs,
)


class TestProjectMetaCore:
    def test_default_values(self):
        meta = ProjectMeta(name="test")
        assert meta.name == "test"
        assert meta.pipeline_stage == ""

    def test_full_meta(self):
        meta = ProjectMeta(
            name="song1", genre="rock", category="demo",
            producer="test", pipeline_stage="mastered",
            lifecycle_state="completed",
        )
        assert meta.genre == "rock"
        assert meta.lifecycle_state == "completed"

    def test_summary(self):
        meta = ProjectMeta(name="song1", genre="pop", category="demo")
        s = meta.summary()
        assert "song1" in s

    def test_update_lifecycle(self):
        meta = ProjectMeta(name="test")
        meta.update_lifecycle()
        assert meta.lifecycle_state is not None

    def test_make_project_path_default(self):
        path = make_project_path("my_song")
        assert "my_song" in str(path)

    def test_create_dirs_and_check(self, tmp_path):
        result = create_project_dirs(str(tmp_path / "proj"))
        assert "Audio" in result
        assert os.path.isdir(result["Audio"])


class TestProjectIndex:
    def test_empty_index(self):
        idx = ProjectIndex()
        assert len(idx.projects) == 0

    def test_add_and_find(self):
        idx = ProjectIndex()
        idx.projects["song1"] = {"name": "song1", "genre": "pop"}
        found = idx.find("song1")
        assert len(found) == 1
        assert found[0][1]["genre"] == "pop"

    def test_find_not_found(self):
        idx = ProjectIndex()
        assert idx.find("nonexistent") == []

    def test_filter_by_genre(self):
        idx = ProjectIndex()
        idx.projects = {
            "a": {"name": "a", "genre": "rock"},
            "b": {"name": "b", "genre": "pop"},
        }
        result = idx.filter_by(genre="rock")
        assert len(result) == 1

    def test_list_all(self):
        idx = ProjectIndex()
        idx.projects = {"a": {"name": "a"}, "b": {"name": "b"}}
        assert len(idx.list_all()) == 2

    def test_scan_creates_index(self, tmp_path):
        import json
        idx = ProjectIndex()
        root = tmp_path / "projects"
        root.mkdir()
        proj_dir = root / "song1"
        proj_dir.mkdir()
        # scan 需要 .hermes_meta.json 文件
        (proj_dir / ".hermes_meta.json").write_text(json.dumps({
            "name": "song1", "genre": "pop", "stage": "created",
        }))
        n = idx.scan(str(root))
        assert n >= 1
