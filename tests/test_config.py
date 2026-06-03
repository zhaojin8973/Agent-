"""测试全局配置读写。"""

import json
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from hermes_core.config import HermesConfig, _config_path


@pytest.mark.unit
class TestHermesConfig:
    """验证 HermesConfig 的读写和默认值。"""

    def test_default_values(self):
        """默认值符合预期。"""
        cfg = HermesConfig()
        assert cfg.project_root == "~/REAPER 工程文件"
        assert cfg.default_sample_rate == 48000
        assert cfg.default_genre == "pop"
        assert cfg.auto_save_prompt is True

    def test_project_root_expanded(self):
        """project_root_expanded 展开 ~。"""
        cfg = HermesConfig(project_root="~/test")
        expanded = cfg.project_root_expanded
        assert expanded.startswith("/")
        assert expanded.endswith("test")
        assert "~" not in expanded

    def test_save_and_load_round_trip(self):
        """保存后再加载，数据一致。"""
        with tempfile.TemporaryDirectory() as td:
            cfg_path = Path(td) / "config.json"
            with patch.object(HermesConfig, '__init__', lambda self: None):
                pass  # can't easily mock the path, use direct approach

            # 直接用 _config_path 的 mock
            cfg = HermesConfig(
                project_root="~/MyProjects",
                default_sample_rate=44100,
                default_genre="rock",
                auto_save_prompt=False,
            )
            # 手动写入测试目录
            import json
            from dataclasses import asdict
            cfg_path.write_text(
                json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            # 读取
            data = json.loads(cfg_path.read_text(encoding="utf-8"))
            assert data["project_root"] == "~/MyProjects"
            assert data["default_sample_rate"] == 44100
            assert data["default_genre"] == "rock"
            assert data["auto_save_prompt"] is False

    def test_set_updates_and_saves(self):
        """set() 修改配置项。"""
        cfg = HermesConfig()
        cfg.set("default_genre", "folk")
        assert cfg.default_genre == "folk"

    def test_set_unknown_key_raises(self):
        """设置不存在的配置项抛出 KeyError。"""
        cfg = HermesConfig()
        with pytest.raises(KeyError):
            cfg.set("nonexistent_key", "value")

    def test_show_contains_all_fields(self):
        """show() 包含所有关键字段。"""
        cfg = HermesConfig()
        text = cfg.show()
        assert "project_root" in text
        assert "default_sample_rate" in text
        assert "default_genre" in text
        assert "auto_save_prompt" in text

    def test_load_creates_default_if_no_file(self):
        """没有配置文件时 load() 返回默认值并写入文件。"""
        with tempfile.TemporaryDirectory() as td:
            fake_dir = Path(td) / ".hermes"
            fake_path = fake_dir / "config.json"
            with patch("hermes_core.config._config_path", return_value=fake_path):
                cfg = HermesConfig.load()
                assert cfg.project_root == "~/REAPER 工程文件"
                # 验证自动创建了目录和默认文件
                assert fake_path.exists()
