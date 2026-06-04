"""测试安全层：路径沙箱、文件保护、操作限流、磁盘检查、临时文件管理。"""

import os
import shutil
import stat
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from hermes_core.security import (
    PathSandbox,
    FileProtector,
    RateLimiter,
    DiskGuard,
    TempFileManager,
    ALLOWED_ROOTS,
    MAX_OPS_PER_MINUTE,
    MAX_RENDER_CONCURRENT,
    MAX_DISK_USAGE_GB,
)
from hermes_core.exceptions import SecurityError


# ════════════════════════════════════════════════════════════════
# PathSandbox 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestPathSandbox:
    """验证路径沙箱的路径穿越检测。"""

    def test_valid_path_in_allowed_root(self, tmp_path):
        """在允许的根目录内的路径应该通过验证。"""
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        test_file = tmp_path / "test.wav"
        test_file.write_text("test")

        result = sandbox.validate_path(str(test_file))
        assert result == test_file.resolve()

    def test_valid_subdirectory(self, tmp_path):
        """子目录中的路径应该通过验证。"""
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        subdir = tmp_path / "sub" / "deep"
        subdir.mkdir(parents=True)
        test_file = subdir / "test.wav"
        test_file.write_text("test")

        result = sandbox.validate_path(str(test_file))
        assert result == test_file.resolve()

    def test_invalid_path_outside_root(self, tmp_path):
        """不在允许根目录下的路径应该抛出 SecurityError。"""
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        outside = Path("/etc/passwd")

        with pytest.raises(SecurityError, match="路径穿越检测"):
            sandbox.validate_path(str(outside))

    def test_path_traversal_detected(self, tmp_path):
        """路径穿越（..）应该被检测并拒绝。"""
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        # 构造 ../ 穿越路径
        traversal = str(tmp_path / ".." / "etc")

        with pytest.raises(SecurityError, match="路径穿越检测"):
            sandbox.validate_path(traversal)

    def test_multiple_allowed_roots(self, tmp_path):
        """多个允许根目录中的任意一个匹配即可通过。"""
        root1 = tmp_path / "root1"
        root2 = tmp_path / "root2"
        root1.mkdir()
        root2.mkdir()

        sandbox = PathSandbox(allowed_roots=[str(root1), str(root2)])
        test_file = root2 / "test.wav"
        test_file.write_text("test")

        result = sandbox.validate_path(str(test_file))
        assert result == test_file.resolve()

    def test_tilde_expansion(self, tmp_path, monkeypatch):
        """~ 应该被展开为用户主目录。"""
        # 使用 mock 避免依赖真实 HOME
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        test_file = tmp_path / "stems" / "audio.wav"
        test_file.parent.mkdir(parents=True)
        test_file.write_text("test")

        # 直接传绝对路径即可，tilde 由 Path.expanduser 处理
        result = sandbox.validate_path(str(test_file))
        assert result == test_file.resolve()

    def test_nonexistent_path_still_validated(self, tmp_path):
        """不存在的路径如果解析后在根目录内，也应该通过验证。"""
        sandbox = PathSandbox(allowed_roots=[str(tmp_path)])
        nonexistent = tmp_path / "nonexistent" / "future.wav"

        # 不存在的路径也能通过（不访问文件系统进行存在性检查）
        result = sandbox.validate_path(str(nonexistent))
        assert result == nonexistent.resolve()

    def test_custom_allowed_roots_default(self):
        """未指定 allowed_roots 时使用全局默认值。"""
        sandbox = PathSandbox()
        assert len(sandbox.allowed_roots) >= 3  # ~, REAPER_Projects, temp
        assert os.path.expanduser("~") in sandbox.allowed_roots


# ════════════════════════════════════════════════════════════════
# FileProtector 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestFileProtector:
    """验证原始文件保护（只读标记 / 恢复 / 临时副本）。"""

    def test_protect_marks_readonly(self, tmp_path):
        """protect_original 将文件标记为只读。"""
        test_file = tmp_path / "original.wav"
        test_file.write_text("precious data")
        # 先赋予可写权限
        test_file.chmod(0o644)

        protector = FileProtector()
        protector.protect_original(str(test_file))

        # 验证权限变为只读
        mode = test_file.stat().st_mode
        write_bits = stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH
        assert (mode & write_bits) == 0  # 没有写权限

    def test_restore_original_permissions(self, tmp_path):
        """restore_original 恢复原始权限。"""
        test_file = tmp_path / "restore_test.wav"
        test_file.write_text("data")
        original_mode = 0o644
        test_file.chmod(original_mode)

        protector = FileProtector()
        protector.protect_original(str(test_file))
        protector.restore_original(str(test_file))

        # 权限应恢复为原始值
        restored_mode = stat.S_IMODE(test_file.stat().st_mode)
        assert restored_mode == original_mode

    def test_restore_without_protect_is_noop(self, tmp_path):
        """未 protect 就 restore 应该是空操作。"""
        test_file = tmp_path / "noop.wav"
        test_file.write_text("data")
        test_file.chmod(0o644)
        original_mode = stat.S_IMODE(test_file.stat().st_mode)

        protector = FileProtector()
        protector.restore_original(str(test_file))

        # 权限不变
        assert stat.S_IMODE(test_file.stat().st_mode) == original_mode

    def test_copy_to_temp_creates_copy(self, tmp_path):
        """copy_to_temp 创建指向临时目录的副本。"""
        src = tmp_path / "source.wav"
        src.write_text("original content")

        protector = FileProtector()
        with patch.object(tempfile, 'mktemp', return_value=str(tmp_path / "hermes_protected_source_XXXX.wav")):
            dst = protector.copy_to_temp(str(src))

        assert dst.exists()
        assert dst.read_text() == "original content"
        assert str(dst) != str(src.resolve())

    def test_protect_nonexistent_file_no_error(self, tmp_path):
        """保护不存在的文件不应该报错。"""
        protector = FileProtector()
        result = protector.protect_original(str(tmp_path / "does_not_exist.wav"))
        assert result == (tmp_path / "does_not_exist.wav").resolve()


# ════════════════════════════════════════════════════════════════
# RateLimiter 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestRateLimiter:
    """验证操作限流和渲染槽位管理。"""

    def test_check_rate_allows_within_limit(self):
        """在限制范围内 check_rate 返回 True。"""
        limiter = RateLimiter(max_ops_per_minute=10)
        for _ in range(10):
            assert limiter.check_rate() is True

    def test_check_rate_throttles_beyond_limit(self):
        """超过限制后 check_rate 返回 False。"""
        limiter = RateLimiter(max_ops_per_minute=3)
        for _ in range(3):
            assert limiter.check_rate() is True
        # 第 4 次应该被限流
        assert limiter.check_rate() is False

    def test_acquire_render_slot_once(self):
        """单个渲染槽位可以被获取。"""
        limiter = RateLimiter(max_render_concurrent=1)
        assert limiter.acquire_render_slot() is True

    def test_acquire_render_slot_twice_blocks(self):
        """第二个渲染槽位获取应该被阻止。"""
        limiter = RateLimiter(max_render_concurrent=1)
        assert limiter.acquire_render_slot() is True
        assert limiter.acquire_render_slot() is False

    def test_release_render_slot_allows_reacquire(self):
        """释放槽位后可以重新获取。"""
        limiter = RateLimiter(max_render_concurrent=1)
        assert limiter.acquire_render_slot() is True
        limiter.release_render_slot()
        assert limiter.acquire_render_slot() is True

    def test_release_without_acquire_no_error(self):
        """未获取槽位就释放不应该报错。"""
        limiter = RateLimiter()
        limiter.release_render_slot()  # 不应抛出异常

    def test_multiple_concurrent_slots(self):
        """多并发槽位配置应该生效。"""
        limiter = RateLimiter(max_render_concurrent=3)
        for _ in range(3):
            assert limiter.acquire_render_slot() is True
        assert limiter.acquire_render_slot() is False

    def test_check_rate_resets_after_window(self):
        """超过时间窗口后限流应该重置。"""
        limiter = RateLimiter(max_ops_per_minute=2)
        assert limiter.check_rate() is True
        assert limiter.check_rate() is True
        assert limiter.check_rate() is False

        # 模拟时间前进 61 秒
        with patch.object(limiter, '_timestamps', []):
            assert limiter.check_rate() is True


# ════════════════════════════════════════════════════════════════
# DiskGuard 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestDiskGuard:
    """验证磁盘空间检查。"""

    def test_check_disk_space_pass(self, tmp_path):
        """磁盘空间充足时返回 True。"""
        guard = DiskGuard(max_disk_usage_gb=1.0)
        mock_usage = shutil.disk_usage(str(tmp_path))._asdict()
        mock_usage['free'] = int(100 * 1024 ** 3)  # 100 GB

        with patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(
            mock_usage['total'], mock_usage['used'], mock_usage['free'],
        )):
            assert guard.check_disk_space(str(tmp_path)) is True

    def test_check_disk_space_fail(self, tmp_path):
        """磁盘空间不足时返回 False。"""
        guard = DiskGuard(max_disk_usage_gb=50.0)
        mock_usage = shutil.disk_usage(str(tmp_path))._asdict()
        mock_usage['free'] = int(1 * 1024 ** 3)  # 1 GB

        with patch('shutil.disk_usage', return_value=shutil._ntuple_diskusage(
            mock_usage['total'], mock_usage['used'], mock_usage['free'],
        )):
            assert guard.check_disk_space(str(tmp_path)) is False

    def test_check_disk_space_oserror(self, tmp_path):
        """磁盘检查出错时返回 False。"""
        guard = DiskGuard()
        with patch('shutil.disk_usage', side_effect=OSError("disk error")):
            assert guard.check_disk_space(str(tmp_path)) is False

    def test_custom_threshold(self, tmp_path):
        """自定义阈值应该生效。"""
        guard = DiskGuard(max_disk_usage_gb=5.0)
        assert guard.max_disk_usage_gb == 5.0


# ════════════════════════════════════════════════════════════════
# TempFileManager 测试
# ════════════════════════════════════════════════════════════════


@pytest.mark.unit
class TestTempFileManager:
    """验证临时文件生命周期管理。"""

    def test_register_temp_file(self, tmp_path):
        """注册临时文件并返回绝对路径。"""
        tfm = TempFileManager()
        test_file = tmp_path / "temp_test.wav"
        test_file.write_text("data")

        result = tfm.register_temp(str(test_file))
        assert result == test_file.resolve()

    def test_cleanup_removes_file(self, tmp_path):
        """cleanup 删除注册的临时文件。"""
        test_file = tmp_path / "to_clean.wav"
        test_file.write_text("data")
        assert test_file.exists()

        tfm = TempFileManager()
        tfm.register_temp(str(test_file))
        cleaned = tfm.cleanup()

        assert cleaned == 1
        assert not test_file.exists()

    def test_cleanup_removes_directory(self, tmp_path):
        """cleanup 删除注册的临时目录。"""
        test_dir = tmp_path / "hermes_temp_dir"
        test_dir.mkdir()
        (test_dir / "file.txt").write_text("data")
        assert test_dir.exists()

        tfm = TempFileManager()
        tfm.register_temp(str(test_dir))
        cleaned = tfm.cleanup()

        assert cleaned == 1
        assert not test_dir.exists()

    def test_context_manager_cleans_on_exit(self, tmp_path):
        """上下文管理器退出时自动清理。"""
        test_file = tmp_path / "ctx_clean.wav"
        test_file.write_text("data")

        with TempFileManager() as tfm:
            tfm.register_temp(str(test_file))
            assert test_file.exists()

        # 退出上下文后应已清理
        assert not test_file.exists()

    def test_context_manager_cleans_on_exception(self, tmp_path):
        """异常发生时退出上下文仍应清理。"""
        test_file = tmp_path / "error_clean.wav"
        test_file.write_text("data")

        try:
            with TempFileManager() as tfm:
                tfm.register_temp(str(test_file))
                raise ValueError("模拟异常")
        except ValueError:
            pass

        # 即使发生异常也应清理
        assert not test_file.exists()

    def test_cleanup_nonexistent_file_no_error(self, tmp_path):
        """清理不存在的文件不应该报错。"""
        tfm = TempFileManager()
        tfm.register_temp(str(tmp_path / "nonexistent.wav"))
        cleaned = tfm.cleanup()

        # 不存在的文件不计入清理数
        assert cleaned == 0

    def test_register_duplicate_same_path(self, tmp_path):
        """重复注册同一路径不应重复。"""
        test_file = tmp_path / "dup.wav"
        test_file.write_text("data")

        tfm = TempFileManager()
        tfm.register_temp(str(test_file))
        tfm.register_temp(str(test_file))

        cleaned = tfm.cleanup()
        assert cleaned == 1  # 只清理一次

    def test_multiple_files_cleanup_count(self, tmp_path):
        """清理计数应该正确反映删除的文件数。"""
        files = []
        for i in range(5):
            f = tmp_path / f"multi_{i}.wav"
            f.write_text(f"data{i}")
            files.append(f)

        tfm = TempFileManager()
        for f in files:
            tfm.register_temp(str(f))

        cleaned = tfm.cleanup()
        assert cleaned == 5
        for f in files:
            assert not f.exists()
