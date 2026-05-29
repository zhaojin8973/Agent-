# Hermes-Core 代码审查报告

> **审查范围**: 全部 9 个源码模块 + 10 个测试文件 (~4,500 行源码, ~12,000 行测试)
> **日期**: 2026-05-29
> **审查方法**: 4 个专项审查并行进行（Bridge/Bus/Send、Engine/Track、FX/Signal/Render/LoudnessOptimizer、测试套件）

---

## 总体评价

Hermes-Core 的三层架构设计清晰，职责划分合理，测试覆盖率不错（287 测试 / 82% 覆盖率）。项目已经具备了可工作的混音自动化能力。以下是我发现的优化和设计提升空间，按 **严重程度** 排列。

---

## 🔴 Critical — 必须修复

### 1. LUFS 计算仅对 48kHz 准确

`src/hermes_core/signal.py` 中 `_compute_lufs` 方法的 K-weighting 滤波器系数是 **硬编码的 48kHz 系数**：

```python
# K-weighting pre-filter (high shelf) — 仅适用于 48kHz!
b_pre = [1.53512485958697, -2.69169618940638, 1.19839281085285]
a_pre = [1.0, -1.69065929318241, 0.73248077421585]
```

> [!CAUTION]
> 如果输入音频是 44.1kHz（CD 标准，非常常见）或 96kHz，LUFS 测量将 **不准确**。这直接影响 `finalize_master()` 的母带增益计算，可能导致成品响度偏差 1-3 LUFS。

**建议**:
- 方案 A: 在滤波前将音频重采样到 48kHz
- 方案 B: 根据 BS.1770-4 的双线性变换公式，按实际采样率重新计算滤波器系数
- 无论选哪个方案，必须加 **采样率校验**，如果不支持的采样率至少要抛出异常

### 2. `read_wav` 自造轮子，应使用 soundfile

`src/hermes_core/signal.py` 中的 `read_wav` 手写了 WAV 解析器，仅支持 PCM 16/24-bit 和 32-bit float：

```python
def read_wav(self, path: str) -> tuple[np.ndarray, int]:
    with open(path, 'rb') as f:
        # 手动解析 RIFF/fmt/data chunks...
```

> [!WARNING]
> `soundfile` 已经是项目依赖（`track.py` 中使用了），但 `signal.py` 却自己造了一个更弱的 WAV 解析器。不支持 RF64/BW64、32-bit PCM、64-bit float、AIFF 等格式。

**建议**: 直接用 `soundfile.read()`：
```python
import soundfile as sf

def read_wav(self, path: str) -> tuple[np.ndarray, int]:
    data, sr = sf.read(path, dtype='float64')
    return data, sr
```

### 3. True Peak 实现极慢 — Python 循环逐样本处理

`src/hermes_core/signal.py` 的 `true_peak_dbtp` 使用 Python for 循环做 4x 过采样：

```python
upsampled = np.zeros(len(mono) * 4)
for i, sample in enumerate(mono):        # ← Python 逐样本循环！
    upsampled[i * 4] = sample
```

> [!WARNING]
> 一首 3 分钟 48kHz 的歌约 860 万样本，这个循环在 Python 中需要 **数秒**。NumPy 向量化或 `scipy.signal.resample_poly` 可以快 100x+。

**建议**:
```python
from scipy.signal import resample_poly
upsampled = resample_poly(mono, 4, 1)
peak = np.max(np.abs(upsampled))
```

---

### 4. `send.py` 的 `create()` 传错了 REAPER API 参数（潜在 Bug）

`src/hermes_core/send.py` 的 `create()` 方法将 `send_mode` 当作 `category` 传给了 `SetTrackSendInfo_Value`：

```python
# send_mode: 0=post-fader, 1=pre-fx, 3=pre-fader
# 但 API 的 category 参数: 0=sends, 1=receives, -1=hardware
self.api.SetTrackSendInfo_Value(src_track, send_mode, idx, "D_VOL", vol_norm)
#                                         ^^^^^^^^^ 这应该是 0（sends）！
```

> [!CAUTION]
> 当 `mode="pre-fx"` 时，`send_mode=1`，API 会误操作 **receives** 而非 **sends**。应始终用 `category=0`，然后单独设置 `I_SENDMODE`。

### 5. `engine.py` 的 `finalize_master` 存在 `NameError` 风险

`src/hermes_core/engine.py` 中，如果 `output_path` 为 `None`，`verify` 变量未绑定但会被引用：

```python
log.info(
    "Master report:\n%s",
    generate_report(search, verify if output_path else None),  # verify 未定义！
)
```

`verify` 只在 `if output_path:` 代码块内赋值，如果条件不满足就会 `NameError`。

### 6. K-Weighting 滤波器可能不符合 ITU-R BS.1770-4 规范

`src/hermes_core/signal.py` 的 K-weighting 实现值得复查：BS.1770-4 要求 **高频搁架提升滤波器 + 高通滤波器** 两级级联。请确认当前系数确实对应这两个滤波器（而非两个高通滤波器）。如果系数正确但仅适用于 48kHz，则需要按 Critical #1 处理。

---

## 🟠 High — 强烈建议修复

### 7. Brickwall Limiter 仿真实为硬削波

`src/hermes_core/loudness_optimizer.py` 的 `_brickwall_limit()` 实际上是 `np.clip`（硬削波），不是 Brickwall Limiter：

```python
def _brickwall_limit(self, samples, ceiling_db=-0.5):
    ceiling_linear = 10 ** (ceiling_db / 20)
    return np.clip(samples, -ceiling_linear, ceiling_linear)  # 这是硬削波
```

真正的 Brickwall Limiter（如 Pro-L 2）有 lookahead、attack/release 包络、程序相关行为。这解释了为什么需要校准偏移（0.3-0.8 LUFS）。

**建议**:
- 短期: 将方法名改为 `_hard_clip()`，文档说明这是简化模型
- 中期: 实现一个简单的 lookahead limiter，可以将仿真精度提高到 ±0.2 LUFS

### 8. DialogKiller 线程安全问题

`src/hermes_core/bridge.py` 中 `DialogKiller` 的 `_dismissed` 列表从后台线程写入、主线程读取，没有锁保护：

```python
# 后台线程写入
self._dismissed.append(info)     # Line ~313

# 主线程读取
return list(self._dismissed)     # Line ~327
```

**建议**: 使用 `threading.Lock` 或 `collections.deque`（线程安全的 append/iterate）。

### 9. `import_media` 静默吞异常

`src/hermes_core/track.py` 的 `import_media` 中：

```python
try:
    RPR.InsertMedia(abs_path, 0)
except Exception:
    pass  # ARM64 workaround — 全部异常静默吞掉！
```

> [!IMPORTANT]
> 即使是 ARM64 workaround，也应该至少 `logger.warning` 记录异常，否则真正的错误（如文件不存在、格式不支持）会被永久隐藏。

### 10. 临时 PCM 文件不清理

`src/hermes_core/track.py` 的 `_convert_to_pcm` 创建 `*_pcm.wav` 临时文件但从不清理：

```python
def _convert_to_pcm(self, path: str) -> str:
    out = path.replace(".wav", "_pcm.wav")  # 累积的临时文件
    sf.write(out, data, sr, subtype="PCM_16")  # 24-bit 降级为 16-bit
    return out
```

**两个问题**:
- 临时文件永久残留
- 24-bit 源文件被降级为 16-bit（应用 PCM_24）

**建议**: 使用 `tempfile.NamedTemporaryFile(suffix='.wav', delete=False)` + 在导入完成后清理，或使用上下文管理器。

---

## 🟡 Medium — 值得改进

### 11. `loudness_optimizer` 二分搜索最终值不精确

`src/hermes_core/loudness_optimizer.py` 的 `find_optimal_gain` 在循环结束后重算 `(lo + hi) / 2` 而非使用最后验证过的 `mid`：

```python
# 循环中: mid 被验证过
for _ in range(max_iterations):
    mid = (lo + hi) / 2.0
    measured_lufs = ...  # 基于 mid 计算
    if converged: break
    # lo 或 hi 被更新 ← mid 与最终 (lo+hi)/2 不同！

final_gain = (lo + hi) / 2.0  # 这个值没被验证过！
```

**建议**: 使用 `final_gain = mid`（最后一次验证过的值）。

---

### 12. Engine 方法过长

`src/hermes_core/engine.py` 的核心方法太长：
- `prepare_stems()`: ~100 行
- `finalize_master()`: ~118 行

**建议**: 拆分为更小的私有方法：

```python
# Before: finalize_master() 做了 5 件事
def finalize_master(self, ...):
    # 1. probe render (~20行)
    # 2. simulation (~15行)
    # 3. gain application (~20行)
    # 4. final render (~20行)
    # 5. verification (~20行)

# After: 每步一个方法
def finalize_master(self, ...):
    probe_wav = self._probe_render(output_dir, timeout)
    optimal_gain = self._find_optimal_gain(probe_wav, target_lufs, ceiling_db)
    self._apply_master_gain(optimal_gain, ceiling_db)
    final_path = self._render_final(output_dir, fmt, timeout)
    return self._verify_final(final_path, target_lufs)
```

### 13. Magic Numbers 散落各处

多个文件中存在未命名的魔法数字：

| 文件 | 值 | 含义 |
|------|------|------|
| engine.py | `-18` | Clip gain RMS 参考（0 VU）|
| engine.py | `6`, `12` | 伴奏压低量 (LU) |
| render.py | `42230` | REAPER 渲染 Action ID |
| signal.py | `-60` | 静音检测阈值 |
| loudness_optimizer.py | `-20, 20` | 二分搜索 Gain 范围 |

**建议**: 提取为模块级常量：
```python
CLIP_GAIN_REF_DB = -18.0       # 0 VU reference
SILENCE_THRESHOLD_DB = -60.0   # dBFS
REAPER_RENDER_ACTION = 42230   # Main_OnCommand action ID
```

### 14. Send Mode 和 Render Format 应使用 Enum

`src/hermes_core/send.py` 和 `src/hermes_core/render.py` 中的模式映射使用局部字典，无效值默认静默处理：

```python
# send.py — 无效 mode 默认为 0，不报错
MODES = {"post-fader": 0, "pre-fx": 1, "pre-fader": 3}
mode_val = MODES.get(mode, 0)

# render.py — 同样的问题
FORMATS = {"WAV": 0, "FLAC": 1, "MP3": 2}
```

**建议**:
```python
from enum import IntEnum

class SendMode(IntEnum):
    POST_FADER = 0
    PRE_FX = 1
    PRE_FADER = 3

class RenderFormat(IntEnum):
    WAV = 0
    FLAC = 1
    MP3 = 2
```

### 15. 异常类型不一致

不同模块对类似错误使用了不同的异常类型：

| 操作 | 异常类型 | 文件 |
|------|---------|------|
| 创建 send 失败 | `RuntimeError` | send.py |
| 删除 send 失败 | `ValueError` | send.py |
| 连接失败 | `ConnectionError` | bridge.py |
| 导入失败 | `RuntimeError` | engine.py |
| 参数无效 | `ValueError` | fx.py |

**建议**: 定义项目专用异常层次：
```python
class HermesError(Exception): ...
class ConnectionError(HermesError): ...
class TrackError(HermesError): ...
class RenderError(HermesError): ...
class AnalysisError(HermesError): ...
```

### 16. 渲染完成检测不可靠

`src/hermes_core/render.py` 通过轮询文件大小判断渲染是否完成：

```python
while time.time() < deadline:
    if os.path.exists(output_path):
        size1 = os.path.getsize(output_path)
        time.sleep(0.5)
        size2 = os.path.getsize(output_path)
        if size1 == size2 and size1 > 0:  # 大小不变 → 认为完成
            return output_path
    time.sleep(1.0)
```

**风险**: 磁盘写入暂停（如 buffer flush）可能导致误判完成。

**建议**: 增加验证步骤 — 用 `soundfile` 尝试读取文件头验证完整性。

### 17. Track 属性访问重复模式

`src/hermes_core/track.py` 中 `get_volume/set_volume`、`get_pan/set_pan` 等方法高度重复：

```python
def set_volume(self, track, value):
    tr = self._resolve(track)
    RPR.SetMediaTrackInfo_Value(tr, "D_VOL", value)

def get_volume(self, track) -> float:
    tr = self._resolve(track)
    return RPR.GetMediaTrackInfo_Value(tr, "D_VOL")
# ... pan, mute, solo 完全相同模式
```

**建议**: 通用 property accessor：
```python
def _get_prop(self, track, key: str) -> float:
    return RPR.GetMediaTrackInfo_Value(self._resolve(track), key)

def _set_prop(self, track, key: str, value: float):
    RPR.SetMediaTrackInfo_Value(self._resolve(track), key, value)

def set_volume(self, track, value: float):
    self._set_prop(track, "D_VOL", value)
```

### 18. FX 参数名搜索效率低

`src/hermes_core/fx.py` 的 `set_param_by_name` 每次调用都线性扫描所有参数：

```python
for i in range(param_count):  # Pro-Q 3 有 ~300 个参数
    name = self.get_param_name(track, fx_index, i)
    if name and param_name.lower() in name.lower():  # 子串匹配，易误匹配
        ...
```

**建议**:
- 构建 param name → index 缓存（per FX instance）
- 优先精确匹配，其次子串匹配

### 19. `bus.py` 的 `validate_structure` 只返回 bool

`src/hermes_core/bus.py` 的验证方法：

```python
def validate_structure(self) -> bool:
    # ... 只告诉你 "坏了"，不告诉你 "哪里坏了"
    return depth == 0
```

**建议**: 返回包含错误详情的结构：
```python
@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]  # 如 "Track 5: depth mismatch, expected 0 got -1"
```

---

## 🟢 Low — 锦上添花

### 20. Type Hints 不完整

多个方法缺少参数和返回值类型注解：
- `track.py`: `_resolve(track)` — `track` 应标注为 `int | MediaTrack`
- `engine.py`: `prepare_stems() -> list[dict]` — 应使用 `TypedDict` 或 `dataclass`
- `engine.py`: `get_project_info() -> dict` — 应标注为 `dict[str, str | None]`
- `bridge.py`: `_run_applescript` 返回值未标注

### 21. Logging 级别不当

`src/hermes_core/engine.py` 中大量操作细节使用 `logger.info`：

```python
logger.info(f"Clip gain for '{name}': raw={raw_rms:.1f}, gain={gain:.1f} dB")
logger.info(f"Fader: '{name}' → {fader_db:+.1f} dB")
```

**建议**: 操作细节用 `logger.debug`，只在关键节点用 `logger.info`（如"渲染开始"、"渲染完成"）。

### 22. `__init__.py` 缺少 `__all__`

`src/hermes_core/__init__.py` 导出了所有类但没有 `__all__` 列表，用户无法区分公共 API 和内部实现。

### 23. 连接重试无指数退避

`src/hermes_core/bridge.py` 的重试逻辑使用固定间隔：

```python
for attempt in range(1, max_retries + 1):
    try: ...
    except Exception:
        time.sleep(retry_delay)  # 固定 2 秒
```

**建议**: 指数退避 — `time.sleep(retry_delay * (2 ** (attempt - 1)))`

### 24. `pyproject.toml` 缺少 `soundfile` 和 `pyloudnorm` 依赖

```toml
dependencies = ["python-reapy>=0.10", "numpy>=1.24"]
```

但 `track.py` 使用了 `soundfile`，`PROJECT_STATUS.md` 提到 `pyloudnorm` 和 `soundfile` 是依赖。

### 25. Engine 直接访问 Bridge 私有成员

`src/hermes_core/engine.py` 中：
```python
self._bridge._dialog_killer.is_running   # 跨层访问私有属性
self._bridge._dialog_killer.stop()
```

**建议**: 在 `ReaperBridge` 上添加公共方法 `stop_dialog_killer()` 和 `get_recent_dialog_events()`。

### 26. `track.py` 的 `_wav_duration_fallback` 变量可能未初始化

如果 WAV 文件的 `data` chunk 出现在 `fmt ` chunk 之前（WAV 规范允许），`channels`、`sr`、`bits` 变量未初始化 → `NameError`。

### 27. `track.py` 硬编码 300 秒回退时长

当 WAV 文件无法识别时，返回任意 `300.0` 秒。这会在时间线上创建错误长度的媒体项。应抛出异常而非静默返回错误值。

### 28. `fx.py` 和 `bridge.py` 存在重复的 `_extract_string` 函数

两个文件各自实现了几乎相同的字符串提取函数，应合并为共用工具函数。

---

## 📊 测试质量评估

| 维度 | 评分 | 说明 |
|------|------|------|
| 覆盖广度 | ⭐⭐⭐⭐ 7/10 | Happy path 覆盖好，错误路径弱 |
| 断言质量 | ⭐⭐⭐ 5/10 | 很多测试只验证"方法被调用"，不验证参数 |
| 可维护性 | ⭐⭐⭐ 5/10 | setUp 方法过长（90+ 行 mock），无共享测试数据 |
| 边界用例 | ⭐⭐ 4/10 | 缺少空输入、边界值、异常恢复测试 |
| Mock 质量 | ⭐⭐⭐ 6/10 | Mock 可用但缺 `spec` 参数，拼写错误无法检测 |

**关键缺失的测试场景**:

1. **LUFS 多采样率测试** — 44.1kHz 的 LUFS 准确性（对应 Critical #1）
2. **`loudness_optimizer.py` 完全没有测试文件** — 纯函数，非常适合单元测试
3. **`prepare_stems()` 和 `finalize_master()` 无单元测试** — 仅有集成测试覆盖
4. **空 stems 列表** — `prepare_stems([])` 的行为
5. **渲染失败回滚** — `finalize_master` 中间步骤失败后的状态
6. **信号边界值** — 空音频、单样本、全静音、Mono WAV
7. **Mock spec** — 所有 mock 应加 `spec=RealClass` 防止属性拼写错误
8. **集成测试硬编码 `/tmp` 路径** — 应使用 `tmp_path` fixture
9. **断言过于宽松** — LUFS 容差 ±3 LUFS 可能掩盖 bug

---

## 🏗️ 架构建议

### A. 依赖注入改进

当前 `MixingEngine.__init__` 直接实例化所有 L2 模块。考虑允许注入：

```python
class MixingEngine:
    def __init__(self, bridge=None, track_manager=None, ...):
        self.bridge = bridge or ReaperBridge()
        self.track = track_manager or TrackManager(self.bridge)
        ...
```

这样测试时可以注入 mock L2 模块，而不用 mock 底层 RPR 调用。

### B. 事件/回调系统

当前没有进度反馈机制。建议增加回调支持：

```python
def finalize_master(self, ..., on_progress=None):
    if on_progress: on_progress("probe_render", 0.2)
    probe_wav = self._probe_render(...)
    if on_progress: on_progress("simulation", 0.5)
    ...
```

### C. 配置对象化

散落在各方法参数中的配置（target LUFS、ceiling dB、tolerance 等）可以收敛为配置对象：

```python
@dataclass
class MasteringConfig:
    target_lufs: float = -12.0
    ceiling_db: float = -0.5
    tolerance_lufs: float = 0.3
    max_search_iterations: int = 50
```

---

## 📋 优先级行动清单

| 优先级 | 项目 | 预估工时 |
|--------|------|----------|
| 🔴 P0 | 修复 LUFS 采样率问题（#1） | 2-3h |
| 🔴 P0 | 用 soundfile 替换自造 WAV 解析器（#2） | 1h |
| 🔴 P0 | 向量化 True Peak + IIR 滤波器计算（#3） | 1-2h |
| 🔴 P0 | 修复 send.py API category bug（#4） | 30min |
| 🔴 P0 | 修复 engine.py verify NameError（#5） | 15min |
| 🟠 P1 | pyproject.toml 补全依赖 | 15min |
| 🟠 P1 | import_media 异常日志 | 15min |
| 🟠 P1 | PCM 转换文件清理 + 保留 24-bit | 30min |
| 🟠 P1 | DialogKiller 线程安全 | 30min |
| 🟠 P1 | 修复二分搜索最终值（#11） | 15min |
| 🟡 P2 | Engine 方法拆分 | 2h |
| 🟡 P2 | 自定义异常层次 | 1h |
| 🟡 P2 | Enum 替换魔法数字/字符串 | 1h |
| 🟡 P2 | 新增 loudness_optimizer 测试文件 | 2h |
| 🟡 P2 | prepare_stems / finalize_master 单元测试 | 2-3h |
| 🟡 P2 | 其他缺失测试用例 | 2h |
| 🟢 P3 | 完善 Type Hints | 2h |
| 🟢 P3 | Logging 级别调整 | 30min |
| 🟢 P3 | 架构改进（DI、回调、配置对象） | 4-6h |
| 🟢 P3 | 合并重复 `_extract_string` 函数 | 30min |
| 🟢 P3 | 添加 `__all__` 和 `__repr__` | 1h |
