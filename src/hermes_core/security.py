"""
安全层：路径沙箱、文件保护、操作限流、磁盘检查、临时文件管理。

提供跨模块共享的安全工具，防止路径穿越、文件误修改、资源耗尽等问题。
"""

from __future__ import annotations

import atexit
import logging
import os
import shutil
import stat
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

from hermes_core.exceptions import SecurityError

log = logging.getLogger(__name__)

# ════════════════════════════════════════════════════════════════
# 全局配置常量
# ════════════════════════════════════════════════════════════════

# 允许的根目录列表 — 路径沙箱默认信任的根
ALLOWED_ROOTS: list[str] = [
    os.path.expanduser("~"),
    os.path.expanduser("~/REAPER_Projects"),
    tempfile.gettempdir(),
]

# 每分钟最大操作次数
MAX_OPS_PER_MINUTE: int = 60

# 最大并发渲染数
MAX_RENDER_CONCURRENT: int = 1

# 磁盘最大使用量 (GB)，超过此值停止写入
MAX_DISK_USAGE_GB: float = 50.0


# ════════════════════════════════════════════════════════════════
# PathSandbox：路径沙箱
# ════════════════════════════════════════════════════════════════


@dataclass
class PathSandbox:
    """路径沙箱：防止路径穿越攻击。

    所有路径操作必须经过沙箱验证，确保不访问允许范围之外的文件。
    支持符号链接解析和 ``..`` 遍历检测。

    Examples
    --------
    >>> sandbox = PathSandbox(allowed_roots=["/tmp"])
    >>> sandbox.validate_path("/tmp/stems")
    PosixPath('/tmp/stems')
    """

    allowed_roots: list[str] = field(default_factory=lambda: ALLOWED_ROOTS.copy())

    def validate_path(self, path: str | Path) -> Path:
        """验证路径是否在沙箱内。

        将路径展开 ``~``、解析符号链接并转换为绝对路径后，
        检查是否在 allowed_roots 中的任一目录下。

        Parameters
        ----------
        path : str | Path
            待验证的路径。

        Returns
        -------
        Path
            展开并 resolve 后的绝对路径。

        Raises
        ------
        SecurityError
            路径解析失败或在沙箱之外。
        """
        try:
            resolved = Path(path).expanduser().resolve()
        except (OSError, RuntimeError) as exc:
            raise SecurityError(f"路径解析失败: {path} — {exc}") from exc

        resolved_str = str(resolved)
        for root in self.allowed_roots:
            root_path = Path(root).expanduser().resolve()
            root_str = str(root_path)
            if resolved_str.startswith(root_str + os.sep) or resolved_str == root_str:
                log.debug("路径验证通过: %s (根: %s)", resolved, root_path)
                return resolved

        raise SecurityError(
            f"路径穿越检测: '{resolved}' 不在允许的根目录 {self.allowed_roots} 内"
        )


# ════════════════════════════════════════════════════════════════
# FileProtector：原始文件保护
# ════════════════════════════════════════════════════════════════


@dataclass
class FileProtector:
    """原始文件保护：防止误修改重要文件。

    将文件标记为只读（chmod 0o444），操作时复制到临时目录处理，
    操作完成后可恢复原始权限。

    Examples
    --------
    >>> protector = FileProtector()
    >>> protector.protect_original("/path/to/original.wav")
    >>> tmp = protector.copy_to_temp("/path/to/original.wav")
    >>> # 在 temp 副本上操作 ...
    >>> protector.restore_original("/path/to/original.wav")
    """

    _original_perms: dict[str, int] = field(default_factory=dict)

    def protect_original(self, path: str | Path) -> Path:
        """将文件标记为只读（0o444）。

        Parameters
        ----------
        path : str | Path
            要保护的文件路径。

        Returns
        -------
        Path
            受保护文件的绝对路径。文件不存在时静默返回路径。
        """
        file_path = Path(path).expanduser().resolve()
        if file_path.exists():
            current_mode = file_path.stat().st_mode
            self._original_perms[str(file_path)] = current_mode
            # 只保留读权限：user/group/other 均为只读
            file_path.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
            log.debug("文件已标记为只读: %s", file_path)
        return file_path

    def restore_original(self, path: str | Path) -> Path:
        """恢复文件的原始权限。

        Parameters
        ----------
        path : str | Path
            要恢复的文件路径。

        Returns
        -------
        Path
            恢复后文件的绝对路径。
        """
        file_path = Path(path).expanduser().resolve()
        file_path_str = str(file_path)
        if file_path_str in self._original_perms:
            file_path.chmod(self._original_perms.pop(file_path_str))
            log.debug("文件权限已恢复: %s", file_path)
        return file_path

    def copy_to_temp(self, path: str | Path) -> Path:
        """将原始文件复制到临时目录供处理。

        在 tempfile.gettempdir() 下创建前缀为 ``hermes_protected_`` 的副本。

        Parameters
        ----------
        path : str | Path
            原始文件路径。

        Returns
        -------
        Path
            临时副本的路径。
        """
        src = Path(path).expanduser().resolve()
        dst = Path(
            tempfile.mktemp(
                suffix=src.suffix, prefix=f"hermes_protected_{src.stem}_"
            )
        )
        shutil.copy2(src, dst)
        log.debug("原始文件已复制到临时目录: %s -> %s", src, dst)
        return dst


# ════════════════════════════════════════════════════════════════
# RateLimiter：操作限流
# ════════════════════════════════════════════════════════════════


@dataclass
class RateLimiter:
    """操作限流器：防止 API 调用过于频繁。

    使用滑动窗口记录操作时间戳，一分钟内超过 max_ops_per_minute 次
    调用 ``check_rate()`` 将返回 False。

    ``acquire_render_slot()`` / ``release_render_slot()`` 确保同时只有一个
    渲染任务在运行。

    Examples
    --------
    >>> limiter = RateLimiter(max_ops_per_minute=60)
    >>> limiter.check_rate()
    True
    >>> limiter.acquire_render_slot()
    True
    >>> limiter.release_render_slot()
    """

    max_ops_per_minute: int = MAX_OPS_PER_MINUTE
    max_render_concurrent: int = MAX_RENDER_CONCURRENT

    _timestamps: list[float] = field(default_factory=list, init=False, repr=False)
    _render_active: int = field(default=0, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def check_rate(self) -> bool:
        """检查是否超过操作频率限制。

        基于一分钟滑动窗口：清理超过 60 秒的时间戳后，
        检查剩余次数是否超过上限。

        Returns
        -------
        bool
            允许操作为 True，超出限制为 False。
        """
        with self._lock:
            now = time.monotonic()
            # 清理超过一分钟的时间戳
            self._timestamps = [ts for ts in self._timestamps if now - ts < 60.0]

            if len(self._timestamps) >= self.max_ops_per_minute:
                log.warning(
                    "操作频率超限: %d 次/分钟（限制 %d）",
                    len(self._timestamps),
                    self.max_ops_per_minute,
                )
                return False

            self._timestamps.append(now)
            return True

    def acquire_render_slot(self) -> bool:
        """获取渲染槽位。

        确保同时不超过 max_render_concurrent 个渲染任务。

        Returns
        -------
        bool
            获取成功为 True，槽位已满则为 False。
        """
        with self._lock:
            if self._render_active >= self.max_render_concurrent:
                log.warning(
                    "渲染槽位已满: %d/%d",
                    self._render_active,
                    self.max_render_concurrent,
                )
                return False
            self._render_active += 1
            log.debug(
                "获取渲染槽位: %d/%d",
                self._render_active,
                self.max_render_concurrent,
            )
            return True

    def release_render_slot(self) -> None:
        """释放渲染槽位。"""
        with self._lock:
            if self._render_active > 0:
                self._render_active -= 1
                log.debug(
                    "释放渲染槽位: %d/%d",
                    self._render_active,
                    self.max_render_concurrent,
                )


# ════════════════════════════════════════════════════════════════
# DiskGuard：磁盘空间检查
# ════════════════════════════════════════════════════════════════


@dataclass
class DiskGuard:
    """磁盘空间检查：防止磁盘写满导致系统不稳定。

    在写入大量数据前使用 ``check_disk_space()`` 检查目标路径
    所在磁盘的剩余空间。

    Examples
    --------
    >>> guard = DiskGuard(max_disk_usage_gb=50.0)
    >>> guard.check_disk_space("/tmp")
    True
    """

    max_disk_usage_gb: float = MAX_DISK_USAGE_GB

    def check_disk_space(self, path: str | Path) -> bool:
        """检查磁盘剩余空间是否足够。

        Parameters
        ----------
        path : str | Path
            要检查的路径（取其所在磁盘分区）。

        Returns
        -------
        bool
            剩余空间 >= max_disk_usage_gb 为 True，不足为 False。
        """
        check_path = Path(path).expanduser().resolve()
        try:
            usage = shutil.disk_usage(str(check_path))
            free_gb = usage.free / (1024 ** 3)
            if free_gb < self.max_disk_usage_gb:
                log.warning(
                    "磁盘空间不足: %.1f GB 可用（需要至少 %.1f GB），路径: %s",
                    free_gb,
                    self.max_disk_usage_gb,
                    check_path,
                )
                return False
            log.debug("磁盘空间充足: %.1f GB 可用", free_gb)
            return True
        except OSError as exc:
            log.error("无法检查磁盘空间: %s — %s", check_path, exc)
            return False


# ════════════════════════════════════════════════════════════════
# TempFileManager：临时文件生命周期管理
# ════════════════════════════════════════════════════════════════


@dataclass
class TempFileManager:
    """临时文件生命周期管理器。

    上下文管理器，``__exit__`` 时自动清理所有注册的临时文件/目录，
    并通过 ``atexit`` 注册兜底清理防止进程异常退出时残留。

    Examples
    --------
    >>> with TempFileManager() as tfm:
    ...     tmp = tfm.register_temp("/tmp/hermes_test.wav")
    ...     # 使用临时文件 ...
    ... # 退出上下文时自动清理
    """

    _temp_paths: list[str] = field(default_factory=list, init=False, repr=False)
    _lock: threading.Lock = field(
        default_factory=threading.Lock, init=False, repr=False
    )

    def __post_init__(self) -> None:
        """初始化时注册 atexit 兜底清理（仅首次）。"""
        if not TempFileManager._registered_atexit:
            atexit.register(TempFileManager._atexit_cleanup)
            TempFileManager._registered_atexit = True

    def register_temp(self, path: str | Path) -> Path:
        """注册临时文件路径，退出时自动清理。

        Parameters
        ----------
        path : str | Path
            临时文件路径。

        Returns
        -------
        Path
            注册后的绝对路径。
        """
        path_str = str(Path(path).expanduser().resolve())
        with self._lock:
            if path_str not in self._temp_paths:
                self._temp_paths.append(path_str)
                log.debug("注册临时文件: %s", path_str)
        return Path(path_str)

    def cleanup(self) -> int:
        """清理所有注册的临时文件和目录。

        Returns
        -------
        int
            成功清理的文件数。
        """
        cleaned = 0
        with self._lock:
            paths = list(self._temp_paths)
            self._temp_paths.clear()

        for path in paths:
            try:
                if os.path.isfile(path):
                    os.unlink(path)
                    cleaned += 1
                    log.debug("已清理临时文件: %s", path)
                elif os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
                    cleaned += 1
                    log.debug("已清理临时目录: %s", path)
            except OSError as exc:
                log.warning("清理临时文件失败: %s — %s", path, exc)

        if cleaned:
            log.info("已清理 %d 个临时文件", cleaned)
        return cleaned

    def __enter__(self) -> TempFileManager:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """退出上下文时自动清理（即使发生异常也会执行）。"""
        self.cleanup()

    @classmethod
    def _atexit_cleanup(cls) -> None:
        """atexit 兜底清理：进程退出时清理所有全局注册的临时文件。"""
        for path in list(cls._global_temp_paths):
            try:
                if os.path.isfile(path):
                    os.unlink(path)
                elif os.path.isdir(path):
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                pass
        cls._global_temp_paths.clear()


# TempFileManager 类级别共享状态
# 注意：不能放在 dataclass 内部，因为 from __future__ import annotations
# 会导致 ClassVar 类型注解变成字符串，dataclass 无法识别。
TempFileManager._global_temp_paths: list[str] = []
TempFileManager._registered_atexit: bool = False
