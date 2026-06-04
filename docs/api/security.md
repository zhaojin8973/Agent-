# 安全层 API

`security` 模块提供路径沙箱、文件保护、操作限流、磁盘检查和临时文件管理等安全工具。

## 导入

```python
from hermes_core.security import (
    PathSandbox,
    FileProtector,
    RateLimiter,
    DiskChecker,
    TempFileManager,
    ALLOWED_ROOTS,
    MAX_OPS_PER_MINUTE,
    MAX_DISK_USAGE_GB,
)
```

## 全局配置常量

| 常量 | 默认值 | 说明 |
|------|--------|------|
| `ALLOWED_ROOTS` | `["~", "~/REAPER_Projects", tmpdir]` | 路径沙箱允许的根目录 |
| `MAX_OPS_PER_MINUTE` | `60` | 每分钟最大操作数 |
| `MAX_RENDER_CONCURRENT` | `1` | 最大并发渲染数 |
| `MAX_DISK_USAGE_GB` | `50.0` | 磁盘最大使用量（GB） |

---

## PathSandbox

路径沙箱：防止路径穿越攻击。所有路径操作必须经过沙箱验证。

```python
from hermes_core.security import PathSandbox

sandbox = PathSandbox(allowed_roots=["/tmp", "/Users/projects"])

# 验证路径
safe_path = sandbox.validate_path("/tmp/stems")
safe_path = sandbox.validate_path("~/REAPER_Projects/song")

# 以下调用会抛出 SecurityError：
# sandbox.validate_path("/etc/passwd")
# sandbox.validate_path("../outside")
```

### 方法

| 方法 | 说明 |
|------|------|
| `validate_path(path)` | 验证路径是否在沙箱内，返回 resolve 后的绝对路径 |
| `validate_write(path)` | 验证路径是否可写入 |
| `validate_read(path)` | 验证路径是否可读取 |

---

## FileProtector

文件保护器：防止意外修改或删除受保护的文件。

```python
from hermes_core.security import FileProtector

protector = FileProtector()

# 设置为只读
protector.protect("/path/to/important.wav")

# 检查状态
is_protected = protector.is_protected("/path/to/important.wav")

# 解除保护
protector.unprotect("/path/to/important.wav")
```

---

## RateLimiter

操作限流器：防止过快的操作导致 REAPER 不稳定。

```python
from hermes_core.security import RateLimiter

limiter = RateLimiter(max_ops_per_minute=30)

with limiter.throttle("add_fx"):
    engine.add_fx(0, "FabFilter Pro-Q 3")
```

---

## DiskChecker

磁盘检查器：监控磁盘使用量，防止写满磁盘。

```python
from hermes_core.security import DiskChecker

checker = DiskChecker(max_usage_gb=50.0)

# 检查是否超过限制
if checker.can_write(additional_mb=500):
    engine.render_mix("./output")

# 获取当前使用量
usage = checker.get_usage()  # 返回 (used_gb, total_gb)
```

---

## TempFileManager

临时文件管理器：创建和管理临时文件，确保退出时清理。

```python
from hermes_core.security import TempFileManager

manager = TempFileManager()

# 创建临时文件
tmp_path = manager.create_temp_file(suffix=".wav")

# 创建临时目录
tmp_dir = manager.create_temp_dir()

# 显式清理（也会注册 atexit 清理）
manager.cleanup()
```

---

## 使用示例

### 完整安全检查链

```python
from hermes_core.security import PathSandbox, DiskChecker, RateLimiter

sandbox = PathSandbox()
disk = DiskChecker()
limiter = RateLimiter()

def safe_render(engine, output_dir: str) -> str:
    """经安全层校验的渲染操作。"""

    # 1. 路径验证
    safe_dir = sandbox.validate_write(output_dir)

    # 2. 磁盘检查
    if not disk.can_write(additional_mb=1000):
        raise SecurityError("磁盘空间不足")

    # 3. 限流
    with limiter.throttle("render"):
        return engine.render_mix(str(safe_dir))
```

---

## 异常

所有安全相关异常都继承自 `SecurityError`，后者继承自 `HermesError`：

```python
from hermes_core.exceptions import SecurityError

try:
    sandbox.validate_path("/etc/passwd")
except SecurityError as e:
    print(f"安全校验失败: {e}")
```
