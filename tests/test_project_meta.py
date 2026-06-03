"""测试工程元数据和索引管理。"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from hermes_core.project_meta import (
    ProjectMeta,
    ProjectIndex,
    extract_name_from_audio,
    make_project_path,
)


@pytest.mark.unit
class TestExtractName:
    """验证从音频文件名提取歌曲名。"""

    def test_vocal_file(self):
        assert extract_name_from_audio("望归 Vocal.wav") == "望归"

    def test_backing_file(self):
        assert extract_name_from_audio("望归 伴奏.wav") == "望归"

    def test_with_parens(self):
        assert extract_name_from_audio("望归 伴奏（测试）.wav") == "望归"

    def test_vocal_with_parens(self):
        assert extract_name_from_audio("望归 Vocal（测试）.wav") == "望归"

    def test_backing_with_english(self):
        assert extract_name_from_audio("Song Backing.wav") == "Song"

    def test_no_separator(self):
        assert extract_name_from_audio("Drum Kick.wav") == "Drum Kick"

    def test_multiple_parts(self):
        assert extract_name_from_audio("望归 人声 2026.wav") == "望归"


@pytest.mark.unit
class TestProjectMeta:
    """验证 ProjectMeta 的创建、保存、加载。"""

    def test_round_trip(self):
        """保存后加载，数据一致。"""
        with tempfile.TemporaryDirectory() as td:
            meta = ProjectMeta(
                name="望归", genre="chinese_folk_bel_canto",
                category="实际项目",
            )
            meta.mark_stage("prepare_stems")
            meta.mark_stage("apply_profile")
            meta.vocal_fx = ["Pro-Q 3", "CLA-76", "RVox"]
            meta.track_count = 3
            meta.spatial_buses = {
                "plate": {"plugin": "Little Plate", "level_db": -12.0},
            }

            meta.save(td)
            loaded = ProjectMeta.load(td)

            assert loaded is not None
            assert loaded.name == "望归"
            assert loaded.genre == "chinese_folk_bel_canto"
            assert loaded.category == "实际项目"
            assert loaded.pipeline_stage == "apply_profile"
            assert loaded.vocal_fx == ["Pro-Q 3", "CLA-76", "RVox"]
            assert loaded.track_count == 3
            assert loaded.spatial_buses["plate"]["plugin"] == "Little Plate"
            assert loaded.created_at != ""
            assert loaded.last_modified != ""

    def test_load_nonexistent_returns_none(self):
        """不存在的目录返回 None。"""
        meta = ProjectMeta.load("/nonexistent/path/xyz")
        assert meta is None

    def test_pending_stages(self):
        """pending_stages 正确计算未完成阶段。"""
        meta = ProjectMeta(name="test")
        assert len(meta.pending_stages()) == 6  # 全部未完成

        meta.mark_stage("prepare_stems")
        pending = meta.pending_stages()
        assert "prepare_stems" not in pending
        assert "apply_profile" in pending

    def test_duplicate_stage_not_added(self):
        """重复标记同一阶段不重复添加。"""
        meta = ProjectMeta(name="test")
        meta.mark_stage("prepare_stems")
        meta.mark_stage("prepare_stems")
        assert meta.pipeline_completed == ["prepare_stems"]

    def test_summary_contains_key_info(self):
        """summary() 包含所有关键信息。"""
        meta = ProjectMeta(name="望归", genre="pop", category="测试")
        meta.mark_stage("prepare_stems")
        meta.vocal_fx = ["Pro-Q 3", "RVox"]
        text = meta.summary()
        assert "望归" in text
        assert "pop" in text
        assert "测试" in text
        assert "Pro-Q 3 → RVox" in text
        assert "导入分轨" in text  # stage label

    def test_json_file_is_valid(self):
        """保存的文件是合法 JSON。"""
        with tempfile.TemporaryDirectory() as td:
            meta = ProjectMeta(name="test", genre="pop")
            meta.mark_stage("prepare_stems")
            meta.save(td)
            meta_path = Path(td) / ".hermes_meta.json"
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            assert data["name"] == "test"
            assert "pipeline" in data
            assert "state_snapshot" in data


@pytest.mark.unit
class TestProjectIndex:
    """验证 ProjectIndex 的扫描、增删、查询。"""

    def test_add_and_find(self):
        """添加后能通过名称搜到。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            meta = ProjectMeta(name="望归", genre="pop")
            idx.add_or_update("实际项目/望归", meta, root_dir=td)
            found = idx.find("望归")
            assert len(found) == 1
            assert found[0][1]["name"] == "望归"
            assert found[0][1]["genre"] == "pop"

    def test_find_case_insensitive(self):
        """搜索不区分大小写。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            meta = ProjectMeta(name="TestProject", genre="pop")
            idx.add_or_update("test/test", meta, root_dir=td)
            found = idx.find("testproject")
            assert len(found) == 1

    def test_remove(self):
        """移除后搜索不到。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            meta = ProjectMeta(name="望归", genre="pop")
            idx.add_or_update("实际项目/望归", meta, root_dir=td)
            assert idx.remove("实际项目/望归", root_dir=td)
            assert idx.find("望归") == []

    def test_scan_finds_meta_files(self):
        """扫描能找到所有 .hermes_meta.json。"""
        with tempfile.TemporaryDirectory() as td:
            # 创建两个工程
            for name, genre, cat in [
                ("test1", "pop", "测试"),
                ("test2", "folk", "风格混音/folk"),
            ]:
                proj_dir = Path(td) / cat / name
                proj_dir.mkdir(parents=True, exist_ok=True)
                meta = ProjectMeta(name=name, genre=genre, category=cat)
                meta.mark_stage("prepare_stems")
                meta.save(str(proj_dir))

            idx = ProjectIndex()
            count = idx.scan(root_dir=td)
            assert count == 2
            assert len(idx.projects) == 2

            # 验证索引文件被创建（注意 macOS /tmp → /private/tmp 的 resolve）
            index_path = Path(td).resolve() / ".hermes_index.json"
            assert index_path.exists()

    def test_filter_by_genre(self):
        """按流派筛选。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            idx.add_or_update("p1", ProjectMeta(name="p1", genre="pop"), root_dir=td)
            idx.add_or_update("p2", ProjectMeta(name="p2", genre="folk"), root_dir=td)
            pop = idx.filter_by(genre="pop")
            assert len(pop) == 1
            assert pop[0][1]["name"] == "p1"

    def test_filter_by_stage(self):
        """按管线阶段筛选。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            m1 = ProjectMeta(name="p1", genre="pop")
            m1.mark_stage("prepare_stems")
            m2 = ProjectMeta(name="p2", genre="pop")
            m2.mark_stage("apply_profile")
            idx.add_or_update("p1", m1, root_dir=td)
            idx.add_or_update("p2", m2, root_dir=td)

            result = idx.filter_by(stage="apply_profile")
            assert len(result) == 1
            assert result[0][1]["name"] == "p2"

    def test_list_all_sorted_by_date(self):
        """list_all 按修改时间倒序排列。"""
        with tempfile.TemporaryDirectory() as td:
            idx = ProjectIndex()
            m1 = ProjectMeta(name="old", genre="pop",
                             last_modified="2026-01-01")
            m2 = ProjectMeta(name="new", genre="pop",
                             last_modified="2026-06-04")
            idx.add_or_update("old", m1, root_dir=td)
            idx.add_or_update("new", m2, root_dir=td)
            items = idx.list_all()
            assert items[0][1]["name"] == "new"  # 最新的排最前


@pytest.mark.unit
class TestMakeProjectPath:
    """验证工程路径构建。"""

    def test_with_category(self):
        p = make_project_path("望归", "实际项目", root_dir="/tmp/hermes")
        assert p.name == "望归"
        assert "实际项目" in str(p)

    def test_without_category(self):
        p = make_project_path("test", root_dir="/tmp/hermes")
        assert "未分类" in str(p)
        assert p.name == "test"
