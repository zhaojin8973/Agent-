"""
操作审计日志 — 记录所有混音操作的完整可追溯记录。

每条操作记录包含时间戳、方法名、参数、结果、耗时，
持久化为 JSON 格式存入工程目录，支持生成人类可读的操作摘要。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# ── 文件名常量 ─────────────────────────────────────────────────────

_AUDIT_FILENAME = ".hermes_audit.json"


# ── Dataclass ──────────────────────────────────────────────────────


@dataclass
class AuditEntry:
    """单条审计记录。"""

    timestamp: str              # ISO 8601 格式
    operation: str              # 方法名
    params: dict[str, Any]      # 参数快照
    result_summary: str         # 结果摘要（成功/失败 + 关键数据）
    duration_ms: float          # 操作耗时（毫秒）
    success: bool = True

    def to_dict(self) -> dict[str, Any]:
        """将记录转换为可序列化的字典。"""
        return {
            "timestamp": self.timestamp,
            "operation": self.operation,
            "params": self.params,
            "result_summary": self.result_summary,
            "duration_ms": self.duration_ms,
            "success": self.success,
        }

    def to_human_readable(self) -> str:
        """生成人类可读的单行描述。

        例如::

            "14:30:22 | 增益分级 | 成功 | 人声 -18.2 dBFS，伴奏 -21.3 dBFS | 耗时 1.2s"
        """
        try:
            dt = datetime.fromisoformat(self.timestamp)
            time_str = dt.strftime("%H:%M:%S")
        except (ValueError, TypeError):
            time_str = self.timestamp or "??:??:??"

        status = "成功" if self.success else "失败"
        duration_s = self.duration_ms / 1000.0
        duration_str = f"{duration_s:.1f}s" if duration_s >= 1.0 else f"{self.duration_ms:.0f}ms"

        return f"{time_str} | {self.operation} | {status} | {self.result_summary} | 耗时 {duration_str}"


# ── AuditLogger ────────────────────────────────────────────────────


class AuditLogger:
    """操作审计日志器。

    用法::

        logger = AuditLogger(project_dir="/path/to/project")
        logger.record("prepare_stems", {"vocal": "...", "backing": "..."},
                       "人声 -18.2 dBFS", 1234.5)
        # ...
        summary = logger.generate_summary()
        print(summary)
    """

    def __init__(self, project_dir: str | Path | None = None):
        self._entries: list[AuditEntry] = []
        self._project_dir: Path | None = Path(project_dir) if project_dir else None
        self._start_time = time.monotonic()
        self._lock = threading.Lock()

    # ── 属性 ──────────────────────────────────────────────────────

    @property
    def project_dir(self) -> Path | None:
        """工程目录路径。"""
        return self._project_dir

    @property
    def elapsed_seconds(self) -> float:
        """从 logger 创建到现在的总耗时（秒）。"""
        return time.monotonic() - self._start_time

    # ── 记录 ──────────────────────────────────────────────────────

    def record(
        self,
        operation: str,
        params: dict[str, Any],
        result_summary: str,
        duration_ms: float = 0.0,
        success: bool = True,
    ) -> AuditEntry:
        """记录一条操作审计日志。

        Parameters
        ----------
        operation : str
            方法名，如 "prepare_stems"。
        params : dict
            参数快照。
        result_summary : str
            结果摘要。
        duration_ms : float
            操作耗时（毫秒），默认 0。
        success : bool
            操作是否成功，默认 True。

        Returns
        -------
        AuditEntry
            新创建的审计记录。
        """
        timestamp = datetime.now(timezone.utc).isoformat(timespec="seconds")
        entry = AuditEntry(
            timestamp=timestamp,
            operation=operation,
            params=dict(params),  # 防御性拷贝
            result_summary=result_summary,
            duration_ms=duration_ms,
            success=success,
        )
        with self._lock:
            self._entries.append(entry)
        log.debug("审计记录: %s — %s", operation, result_summary)

        # 自动持久化 — 每次操作后同步写入磁盘，防止崩溃丢失
        # 在锁外调用 save_to_file()（它有自己的锁），避免死锁
        if self._project_dir is not None:
            try:
                self.save_to_file()
            except Exception as exc:
                log.debug("审计自动保存失败（非致命）: %s", exc)
        return entry

    def get_entries(self) -> list[AuditEntry]:
        """返回所有审计记录的副本。

        Returns
        -------
        list[AuditEntry]
            审计记录列表。
        """
        with self._lock:
            return list(self._entries)

    # ── 摘要 ──────────────────────────────────────────────────────

    def generate_summary(self) -> str:
        """生成人类可读的操作摘要。

        返回格式::

            === 混音操作审计报告 ===
            工程: xxx
            开始时间: xxx | 总耗时: xxx

            操作记录:
              1. [14:30:22] 创建工程          | 成功 | 耗时 0.8s
              2. [14:30:23] 导入分轨          | 成功 | 人声+伴奏 | 耗时 2.1s
              3. [14:30:26] 增益分级          | 成功 | 人声 -18.2 dBFS | 耗时 1.2s
              ...

            总计: 15 步 | 成功: 15 | 失败: 0 | 总耗时: 45.3s

        Returns
        -------
        str
            格式化的审计报告文本。
        """
        with self._lock:
            entries = list(self._entries)

        if not entries:
            return "=== 混音操作审计报告 ===\n暂无操作记录。"

        project_name = self._project_dir.name if self._project_dir else "未指定"
        start_dt = datetime.fromisoformat(entries[0].timestamp) if entries else datetime.now()
        total_elapsed = self.elapsed_seconds

        lines: list[str] = []
        lines.append("=== 混音操作审计报告 ===")
        lines.append(f"工程: {project_name}")
        lines.append(
            f"开始时间: {start_dt.strftime('%Y-%m-%d %H:%M:%S')} "
            f"| 总耗时: {total_elapsed:.1f}s"
        )
        lines.append("")

        success_count = 0
        failure_count = 0
        total_duration_ms = 0.0

        for i, entry in enumerate(entries, start=1):
            if entry.success:
                success_count += 1
            else:
                failure_count += 1
            total_duration_ms += entry.duration_ms

            try:
                dt = datetime.fromisoformat(entry.timestamp)
                time_str = dt.strftime("%H:%M:%S")
            except (ValueError, TypeError):
                time_str = entry.timestamp or "??:??:??"

            status_mark = "✓ 成功" if entry.success else "✗ 失败"
            duration_s = entry.duration_ms / 1000.0
            duration_str = f"{duration_s:.1f}s" if duration_s >= 1.0 else f"{entry.duration_ms:.0f}ms"

            result = entry.result_summary if entry.result_summary else "-"
            # 截断过长的结果摘要
            if len(result) > 60:
                result = result[:57] + "..."

            lines.append(
                f"  {i:2d}. [{time_str}] {entry.operation:20s} "
                f"| {status_mark} | {result} | 耗时 {duration_str}"
            )

        total_duration_s = total_duration_ms / 1000.0
        lines.append("")
        lines.append(
            f"总计: {len(entries)} 步 | 成功: {success_count} | "
            f"失败: {failure_count} | 总耗时: {total_duration_s:.1f}s"
        )

        return "\n".join(lines)

    # ── 持久化 ────────────────────────────────────────────────────

    def save_to_file(self, path: str | Path | None = None) -> Path:
        """将审计日志保存为 JSON 文件。

        Parameters
        ----------
        path : str | Path, optional
            目标文件路径，默认为 project_dir / .hermes_audit.json。

        Returns
        -------
        Path
            实际写入的文件路径。

        Raises
        ------
        ValueError
            未指定 project_dir 且未提供 path。
        """
        if path is not None:
            file_path = Path(path)
        elif self._project_dir is not None:
            file_path = self._project_dir / _AUDIT_FILENAME
        else:
            raise ValueError("必须提供 path 或在构造时指定 project_dir")

        with self._lock:
            entries_data = [entry.to_dict() for entry in self._entries]

        data = {
            "version": 1,
            "project_dir": str(self._project_dir) if self._project_dir else None,
            "total_elapsed_s": self.elapsed_seconds,
            "entries": entries_data,
        }

        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(
            json.dumps(data, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        log.info("审计日志已保存: %s (%d 条记录)", file_path, len(entries_data))
        return file_path

    @classmethod
    def load_from_file(cls, path: str | Path) -> AuditLogger:
        """从 JSON 文件加载审计日志。

        Parameters
        ----------
        path : str | Path
            源文件路径。

        Returns
        -------
        AuditLogger
            加载的审计日志器实例。

        Raises
        ------
        FileNotFoundError
            文件不存在。
        json.JSONDecodeError
            JSON 格式错误。
        """
        file_path = Path(path)
        raw = json.loads(file_path.read_text(encoding="utf-8"))

        project_dir = raw.get("project_dir")
        logger = cls(project_dir=project_dir)

        for entry_data in raw.get("entries", []):
            entry = AuditEntry(
                timestamp=entry_data.get("timestamp", ""),
                operation=entry_data.get("operation", ""),
                params=entry_data.get("params", {}),
                result_summary=entry_data.get("result_summary", ""),
                duration_ms=entry_data.get("duration_ms", 0.0),
                success=entry_data.get("success", True),
            )
            logger._entries.append(entry)

        log.info("审计日志已加载: %s (%d 条记录)", file_path, len(logger._entries))
        return logger
