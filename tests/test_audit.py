"""测试操作审计日志系统。"""

import json
import tempfile
import threading
from pathlib import Path

import pytest

from hermes_core.audit import AuditEntry, AuditLogger


@pytest.mark.unit
class TestAuditEntry:
    """验证 AuditEntry 数据类和序列化。"""

    def test_to_dict(self):
        """to_dict() 返回正确的字典。"""
        entry = AuditEntry(
            timestamp="2026-06-04T14:30:22",
            operation="prepare_stems",
            params={"vocal": "vocal.wav", "backing": "backing.wav"},
            result_summary="人声 -18.2 dBFS，伴奏 -21.3 dBFS",
            duration_ms=1234.5,
            success=True,
        )
        d = entry.to_dict()
        assert d["timestamp"] == "2026-06-04T14:30:22"
        assert d["operation"] == "prepare_stems"
        assert d["params"]["vocal"] == "vocal.wav"
        assert d["duration_ms"] == 1234.5
        assert d["success"] is True

    def test_to_human_readable_success(self):
        """to_human_readable() 成功记录包含正确格式。"""
        entry = AuditEntry(
            timestamp="2026-06-04T14:30:22",
            operation="增益分级",
            params={},
            result_summary="人声 -18.2 dBFS",
            duration_ms=1200.0,
            success=True,
        )
        readable = entry.to_human_readable()
        assert "14:30:22" in readable
        assert "增益分级" in readable
        assert "成功" in readable
        assert "1.2s" in readable

    def test_to_human_readable_failure(self):
        """失败记录显示"失败"。"""
        entry = AuditEntry(
            timestamp="2026-06-04T15:00:00",
            operation="渲染",
            params={},
            result_summary="REAPER 返回错误",
            duration_ms=500.0,
            success=False,
        )
        readable = entry.to_human_readable()
        assert "失败" in readable
        assert "500ms" in readable

    def test_to_human_readable_ms_format(self):
        """小于 1 秒的耗时以 ms 显示。"""
        entry = AuditEntry(
            timestamp="2026-06-04T14:00:00",
            operation="check",
            params={},
            result_summary="ok",
            duration_ms=350.0,
        )
        readable = entry.to_human_readable()
        assert "350ms" in readable


@pytest.mark.unit
class TestAuditLoggerBasic:
    """验证 AuditLogger 基本操作。"""

    def test_record_adds_entry(self):
        """record() 添加条目到内部列表。"""
        logger = AuditLogger()
        entry = logger.record("test_op", {"key": "val"}, "ok", duration_ms=100.0)
        entries = logger.get_entries()
        assert len(entries) == 1
        assert entries[0] is entry
        assert entries[0].operation == "test_op"

    def test_record_params_are_copied(self):
        """传入的 params 字典被防御性拷贝，外部修改不影响记录。"""
        logger = AuditLogger()
        params = {"level": 1}
        logger.record("op", params, "ok")
        params["level"] = 999  # 外部修改
        entries = logger.get_entries()
        assert entries[0].params["level"] == 1  # 记录不变

    def test_multiple_records(self):
        """多次 record 顺序正确。"""
        logger = AuditLogger()
        logger.record("step1", {}, "第一步完成", 100)
        logger.record("step2", {}, "第二步完成", 200)
        logger.record("step3", {}, "第三步完成", 300)
        entries = logger.get_entries()
        assert len(entries) == 3
        ops = [e.operation for e in entries]
        assert ops == ["step1", "step2", "step3"]

    def test_get_entries_returns_copy(self):
        """get_entries() 返回副本，外部修改不影响内部状态。"""
        logger = AuditLogger()
        logger.record("op1", {}, "ok")
        entries = logger.get_entries()
        entries.clear()  # 修改返回的列表
        assert len(logger.get_entries()) == 1

    def test_project_dir_property(self):
        """project_dir 属性正确返回构造时传入的路径。"""
        logger = AuditLogger(project_dir="/tmp/test_project")
        assert logger.project_dir == Path("/tmp/test_project")

    def test_project_dir_none(self):
        """不传 project_dir 时返回 None。"""
        logger = AuditLogger()
        assert logger.project_dir is None


@pytest.mark.unit
class TestAuditLoggerSummary:
    """验证 generate_summary 生成摘要。"""

    def test_empty_summary(self):
        """无记录时返回提示信息。"""
        logger = AuditLogger(project_dir="/tmp/test")
        summary = logger.generate_summary()
        assert "暂无操作记录" in summary

    def test_summary_with_entries(self):
        """有记录时摘要包含所有关键字段。"""
        logger = AuditLogger(project_dir="/tmp/test_project")
        logger.record("导入分轨", {"tracks": 2}, "人声+伴奏", 2100.0)
        logger.record("增益分级", {"target": "-18 dBFS"}, "人声 -18.2 dBFS", 1200.0)
        logger.record("空间效果器", {"bus": "plate"}, "Little Plate -12.0 dB", 800.0)

        summary = logger.generate_summary()
        assert "test_project" in summary
        assert "导入分轨" in summary
        assert "增益分级" in summary
        assert "空间效果器" in summary
        assert "总计: 3 步" in summary
        assert "成功: 3" in summary
        assert "失败: 0" in summary

    def test_summary_with_failure(self):
        """摘要正确统计失败数。"""
        logger = AuditLogger(project_dir="/tmp/test")
        logger.record("step1", {}, "ok", 100, success=True)
        logger.record("step2", {}, "error", 50, success=False)

        summary = logger.generate_summary()
        assert "成功: 1" in summary
        assert "失败: 1" in summary
        assert "✗ 失败" in summary
        assert "✓ 成功" in summary


@pytest.mark.unit
class TestAuditLoggerPersistence:
    """验证 save_to_file / load_from_file 持久化。"""

    def test_round_trip(self):
        """保存后加载，数据一致。"""
        with tempfile.TemporaryDirectory() as td:
            logger = AuditLogger(project_dir=td)
            logger.record("step1", {"a": 1}, "结果1", 100.0)
            logger.record("step2", {"b": 2}, "结果2", 200.0, success=False)

            saved_path = logger.save_to_file()
            assert saved_path.exists()
            assert saved_path.suffix == ".json"

            loaded = AuditLogger.load_from_file(saved_path)
            entries = loaded.get_entries()
            assert len(entries) == 2
            assert entries[0].operation == "step1"
            assert entries[0].success is True
            assert entries[1].operation == "step2"
            assert entries[1].success is False

    def test_save_to_custom_path(self):
        """save_to_file 支持自定义路径。"""
        with tempfile.TemporaryDirectory() as td:
            logger = AuditLogger()
            custom_path = Path(td) / "custom_audit.json"
            result = logger.save_to_file(custom_path)
            assert result == custom_path
            assert custom_path.exists()
            data = json.loads(custom_path.read_text(encoding="utf-8"))
            assert data["entries"] == []

    def test_save_without_project_dir_or_path_raises(self):
        """未指定 project_dir 且未传 path 时抛出 ValueError。"""
        logger = AuditLogger()
        with pytest.raises(ValueError, match="必须提供 path 或在构造时指定 project_dir"):
            logger.save_to_file()

    def test_load_from_nonexistent_file_raises(self):
        """加载不存在的文件抛出 FileNotFoundError。"""
        with pytest.raises(FileNotFoundError):
            AuditLogger.load_from_file("/nonexistent/audit.json")

    def test_saved_json_is_valid(self):
        """保存的文件是合法 JSON 且包含必要字段。"""
        with tempfile.TemporaryDirectory() as td:
            logger = AuditLogger(project_dir=td)
            logger.record("test_op", {"key": "val"}, "ok", 500.0)
            path = logger.save_to_file()
            data = json.loads(path.read_text(encoding="utf-8"))
            assert data["version"] == 1
            assert data["project_dir"] == td
            assert len(data["entries"]) == 1
            assert data["entries"][0]["operation"] == "test_op"


@pytest.mark.unit
class TestAuditLoggerThreadSafety:
    """验证线程安全。"""

    def test_concurrent_records(self):
        """多线程同时 record 不会丢失记录。"""
        logger = AuditLogger()
        count = 100

        def worker(start: int) -> None:
            for i in range(count):
                logger.record(
                    f"op_{start}_{i}",
                    {"index": i},
                    f"结果 {start}_{i}",
                    duration_ms=float(i),
                )

        threads = [
            threading.Thread(target=worker, args=(j,))
            for j in range(3)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        entries = logger.get_entries()
        assert len(entries) == 3 * count

    def test_concurrent_read_write(self):
        """读写并发不崩溃。"""
        logger = AuditLogger()

        def writer() -> None:
            for i in range(50):
                logger.record(f"op_{i}", {}, f"结果 {i}")

        def reader() -> None:
            for _ in range(50):
                _ = logger.get_entries()
                _ = logger.generate_summary()

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=reader),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        # 不应崩溃或数据损坏
        assert len(logger.get_entries()) == 50


@pytest.mark.unit
class TestAuditLoggerElapsed:
    """验证耗时相关功能。"""

    def test_elapsed_seconds_positive(self):
        """elapsed_seconds 为正数。"""
        import time
        logger = AuditLogger()
        time.sleep(0.01)
        assert logger.elapsed_seconds > 0

    def test_duration_preserved_in_entry(self):
        """record() 中的 duration_ms 正确保存。"""
        logger = AuditLogger()
        logger.record("op", {}, "ok", duration_ms=3456.7)
        assert logger.get_entries()[0].duration_ms == 3456.7


@pytest.mark.unit
class TestAuditEntryEdgeCases:
    """边界情况测试。"""

    def test_empty_params(self):
        """空参数字典正常处理。"""
        entry = AuditEntry(
            timestamp="2026-06-04T14:00:00",
            operation="empty_op",
            params={},
            result_summary="",
            duration_ms=0.0,
        )
        assert entry.to_dict()["params"] == {}

    def test_to_human_readable_no_timestamp(self):
        """无时间戳时使用默认值。"""
        entry = AuditEntry(
            timestamp="",
            operation="op",
            params={},
            result_summary="ok",
            duration_ms=100.0,
        )
        readable = entry.to_human_readable()
        assert "??:??:??" in readable

    def test_to_human_readable_invalid_timestamp(self):
        """无效时间戳格式使用原值。"""
        entry = AuditEntry(
            timestamp="not-a-date",
            operation="op",
            params={},
            result_summary="ok",
            duration_ms=100.0,
        )
        readable = entry.to_human_readable()
        assert "not-a-date" in readable
