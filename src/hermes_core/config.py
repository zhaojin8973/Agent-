"""全局配置读写。

配置文件位置：``~/.hermes/config.json``
所有路径支持 ``~`` 展开为当前用户主目录。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── 默认配置 ──────────────────────────────────────────────────

_DEFAULT_CONFIG: dict[str, Any] = {
    "project_root": "~/REAPER 工程文件",
    "default_sample_rate": 48000,
    "default_genre": "pop",
    "auto_save_prompt": True,
}


def _config_dir() -> Path:
    """返回配置目录 ``~/.hermes/``，不存在则创建。"""
    d = Path.home() / ".hermes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _config_path() -> Path:
    """返回配置文件完整路径 ``~/.hermes/config.json``。"""
    return _config_dir() / "config.json"


# ── Dataclass ─────────────────────────────────────────────────


@dataclass
class HermesConfig:
    """全局配置，对应 ``~/.hermes/config.json``。"""

    project_root: str = "~/REAPER 工程文件"
    default_sample_rate: int = 48000
    default_genre: str = "pop"
    auto_save_prompt: bool = True

    # ── computed ──────────────────────────────────────────────

    @property
    def project_root_expanded(self) -> str:
        """返回展开 ``~`` 之后的工程根目录。"""
        return str(Path(self.project_root).expanduser().resolve())

    # ── I/O ───────────────────────────────────────────────────

    @classmethod
    def load(cls) -> HermesConfig:
        """从 ``~/.hermes/config.json`` 加载配置。

        如果文件不存在则返回默认配置并写入磁盘。
        """
        path = _config_path()
        if not path.exists():
            cfg = cls()
            cfg.save()
            return cfg
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return cls(
                project_root=data.get("project_root", _DEFAULT_CONFIG["project_root"]),
                default_sample_rate=data.get(
                    "default_sample_rate", _DEFAULT_CONFIG["default_sample_rate"],
                ),
                default_genre=data.get(
                    "default_genre", _DEFAULT_CONFIG["default_genre"],
                ),
                auto_save_prompt=data.get(
                    "auto_save_prompt", _DEFAULT_CONFIG["auto_save_prompt"],
                ),
            )
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Failed to load config: %s — using defaults", exc)
            return cls()

    def save(self) -> None:
        """将当前配置写入 ``~/.hermes/config.json``。"""
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        data = asdict(self)
        path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.debug("Config saved to %s", path)

    def set(self, key: str, value: Any) -> None:
        """设置单个配置项并保存。"""
        if not hasattr(self, key):
            raise KeyError(f"Unknown config key: {key}")
        setattr(self, key, value)
        self.save()

    def show(self) -> str:
        """返回人类可读的配置摘要。"""
        lines = [
            f"project_root         = {self.project_root}",
            f"  (expanded)         = {self.project_root_expanded}",
            f"default_sample_rate  = {self.default_sample_rate}",
            f"default_genre        = {self.default_genre}",
            f"auto_save_prompt     = {self.auto_save_prompt}",
        ]
        return "\n".join(lines)
