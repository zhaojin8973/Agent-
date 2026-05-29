# Hermes-Core 落地差距分析

> CODE_REVIEW.md 关注的是代码质量。这份文档关注的是：**即使代码全部修好，距离真正能用还差什么？**
>
> 日期: 2026-05-29

---

## 一句话结论

代码骨架已经成型，但当前本质上是一个 **只能由作者本人在特定机器上运行的原型**。要成为别人（或未来的你）能可靠使用的工具，还需要补全以下几个维度。

---

## 🔴 一、操作安全性 — 当前最大风险

### 1. 不支持幂等（重复执行会出错）

这是落地的头号阻碍。当前核心方法 **都不是幂等的**：

| 方法 | 重复执行后果 |
|------|------------|
| `prepare_stems()` | clip gain 叠加施加，第二次 = 双倍增益 |
| `finalize_master()` | 再挂一个 Pro-L 2，master 上出现两个 limiter |
| `import_stems()` | 重复导入，产生双份音轨 |

**实际场景**：用户跑了一遍觉得参数不对想重跑 → 工程报废，只能从头来。

**建议**：
```python
def prepare_stems(self, ...):
    # 在最前面检查是否已经执行过
    if self._stems_prepared:
        raise RuntimeError("Stems already prepared. Create a new project or call reset().")
    # 或者：检测 clip gain 是否已经不是 0 dB，如果是则跳过
```
每个主要操作都需要一个"是否已执行"的守卫，或者提供 `reset()` 方法。

### 2. 完全没有使用 REAPER 的 Undo 系统

REAPER 有完整的 Undo/Redo 机制（`Undo_BeginBlock` / `Undo_EndBlock`），但项目 **一次都没调用过**。

**后果**：
- 所有操作都是不可逆的（用户在 REAPER 里按 Ctrl+Z 无效）
- `prepare_stems` 施加了错误增益 → 只能手动逐轨回退或加载 checkpoint
- 唯一的回退手段是 `save_checkpoint()` → 关闭工程 → 重新打开 .rpp 文件

**建议**：给每个关键操作包裹 undo block：
```python
def prepare_stems(self, ...):
    RPR.Undo_BeginBlock()
    try:
        # ... 所有操作 ...
        RPR.Undo_EndBlock("Hermes: Prepare Stems", -1)
    except:
        RPR.Undo_DoUndo2(0)  # 回滚
        raise
```

### 3. 操作中断无恢复

| 中断场景 | 当前行为 | 应有行为 |
|---------|---------|---------|
| REAPER 在 render 中崩溃 | 轮询超时，返回 error dict | 检测崩溃 → 自动重启 REAPER → 加载 checkpoint → 重试 |
| REAPER 在 FX 操作中挂起 | Python 进程永久阻塞 | 超时机制 → 断线重连 |
| Python 进程 crash（depth > 0）| REAPER UI 冻结 | `atexit` 钩子 → `PreventUIRefresh(-depth)` |
| 网络断开（reapy IPC）| RPR 调用直接抛异常 | 自动重连 + 重试 |

**最低限度**：加 `atexit` 清理钩子：
```python
import atexit

class ReaperBridge:
    def connect(self):
        ...
        atexit.register(self._emergency_cleanup)

    def _emergency_cleanup(self):
        try:
            while self._ui_refresh_depth > 0:
                RPR.PreventUIRefresh(-1)
                self._ui_refresh_depth -= 1
        except:
            pass
```

---

## 🔴 二、插件依赖 — 硬编码的致命假设

### 当前硬编码的插件链

```
人声:    FabFilter Pro-Q 3 → Waves RVox
混响:    ValhallaVintageVerb
Master:  FabFilter Pro-L 2
```

**问题**：
1. **插件名称精确匹配**：`"FabFilter Pro-L 2"` 必须和 REAPER 里的名称完全一致。不同安装方式（VST2/VST3/AU）可能导致名称不同（如 `"FabFilter Pro-L 2 (x86_64)"` vs `"Pro-L 2"`）
2. **无安装检测**：如果插件缺失，`add_fx` 返回 -1，后续 `set_param` 操作在错误的 FX 上执行 → 静默错误
3. **参数归一化公式硬编码**：`gain / 30.0`、`(output + 30) / 30` 只适用于 Pro-L 2 特定版本
4. **换插件 = 改源码**：想用 Ozone 替代 Pro-L 2？必须改 engine.py

**建议**：插件链应该可配置：
```python
@dataclass
class FXPreset:
    name: str                          # REAPER 中的 FX 名称
    params: dict[str, float] | None    # 参数名 → 归一化值
    alternatives: list[str] | None     # 备选名称

@dataclass
class MixingProfile:
    vocal_chain: list[FXPreset]
    bus_reverb: FXPreset
    master_limiter: FXPreset
    genre_table: dict[str, dict]
    clip_gain_ref_db: float = -18.0
    target_lufs: float = -12.0

# 用 YAML/JSON 配置文件加载
profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")
engine = MixingEngine(profile=profile)
```

### 需要的预检：
```python
def preflight_plugins(self, profile: MixingProfile) -> list[str]:
    """检查所有需要的插件是否已安装，返回缺失列表。"""
    missing = []
    for fx in profile.all_fx_names():
        if not self._fx.is_installed(fx):
            missing.append(fx)
    return missing
```

---

## 🟠 三、用户体验 — 从库到工具

### 当前状态：只能写代码调用

```python
# 这是当前使用这个项目的唯一方式
from hermes_core import MixingEngine
eng = MixingEngine()
eng.connect()
eng.create_project("我的混音", "/output")
eng.import_stems(["/path/to/vocal.wav", "/path/to/backing.wav"])
eng.prepare_stems(eng._stems, genre="pop", vocal_indices=[0])
# ... 还要手动调 FX、send、bus ...
eng.finalize_master("/output", target_lufs=-12)
```

### 缺少的用户入口

| 入口 | 优先级 | 说明 |
|------|--------|------|
| **CLI 工具** | 🔴 高 | `hermes mix --vocal vocal.wav --backing inst.wav --genre pop --output ./` |
| **配置文件** | 🔴 高 | `hermes.yaml` 描述工作流，不用写 Python |
| **`__main__.py`** | 🟡 中 | `python -m hermes_core` 入口 |
| **批处理** | 🟡 中 | 传入文件夹，逐首处理 |
| **Web UI / GUI** | 🟢 低 | 可以后续考虑 |

**最低可用 CLI 示例**：
```bash
# 一键贴唱混音
hermes vocal-mix \
  --vocal "望归 Vocal.wav" \
  --backing "望归 伴奏.wav" \
  --genre chinese_folk_bel_canto \
  --output ./output \
  --target-lufs -12

# 批量处理
hermes batch --input-dir ./songs --config profiles/pop.yaml --output-dir ./masters
```

---

## 🟠 四、文档 — 几乎空白

| 文档类型 | 状态 | 重要性 |
|---------|------|--------|
| **README.md** | ❌ 不存在 | 🔴 必须 — 项目门面 |
| **安装指南** | ❌ 不存在 | 🔴 必须 — 依赖 REAPER + 插件 + Python 版本限制 |
| **快速开始** | ❌ 不存在 | 🔴 必须 — 5 分钟跑通一次 |
| **API 文档** | ❌ 不存在 | 🟡 重要 — docstring 不完整 |
| **架构文档** | ✅ PROJECT_STATUS.md | OK |
| **故障排除** | ❌ 不存在 | 🟡 重要 — REAPER 配置问题多 |
| **示例脚本** | ❌ 不存在 | 🟡 重要 |

### README.md 应包含的最低内容：
- 项目一句话介绍
- 前置条件（REAPER 版本、Python 版本、必装插件列表）
- 安装步骤
- 快速开始（3 行代码跑通）
- 常见问题

---

## 🟡 五、环境鲁棒性

### 1. REAPER 项目状态感知

当前引擎不知道 REAPER 的真实状态：

```python
# 场景：用户在 REAPER 里手动切换了工程
eng.create_project("Song A", ...)
# 用户在 REAPER GUI 里打开了 Song B
eng.prepare_stems(...)  # ← 操作的是 Song B，不是 Song A！
```

**建议**：每次操作前校验当前项目：
```python
def _ensure_correct_project(self):
    current = RPR.GetProjectName(0, "", 512)[1]
    if current != self._project_name:
        raise RuntimeError(f"Project mismatch: expected '{self._project_name}', got '{current}'")
```

### 2. REAPER 版本兼容性

| 依赖 | 当前假设 | 风险 |
|------|---------|------|
| Action ID 42230 | REAPER 7.73 | 可能在未来版本变更 |
| `RENDER_FORMAT` 二进制格式 | 特定版本 | 升级 REAPER 后可能失效 |
| ARM64 元组格式 | REAPER 7.73 arm64 | 补丁更新可能修复此 bug，导致代码反而出错 |
| reapy 0.10 | 特定版本 | API 可能变化 |

**建议**：启动时检测并记录版本：
```python
def connect(self):
    ...
    version = RPR.GetAppVersion()
    if not version.startswith("7."):
        log.warning("Untested REAPER version: %s. Tested with 7.73.", version)
```

### 3. 磁盘空间

渲染操作可能产生很大的文件（一首 5 分钟 24-bit 48kHz 立体声 WAV ≈ 85MB），但 **没有磁盘空间检查**。

---

## 🟡 六、音频处理边界

### 未处理的真实场景

| 场景 | 当前行为 | 应有行为 |
|------|---------|---------|
| 伴奏是 MP3/FLAC（非 WAV）| signal.py 崩溃 | 自动转换或用 soundfile 读取 |
| Mono 人声 + Stereo 伴奏 | LUFS 计算可能异常 | 检测并适配通道数 |
| 44.1kHz 采样率 | LUFS 不准确 | 重采样或重算滤波器 |
| 采样率混合（44.1k + 48k）| 静默混合 | 警告或统一采样率 |
| 超长音频（>30分钟） | 内存溢出风险 | 分块处理 |
| 空白/无声音频 | LUFS = -inf → 二分搜索不收敛 | 提前检测并报错 |
| DC offset | 影响 RMS/LUFS 准确性 | 导入前去除 DC |

---

## 🟢 七、打包发布

### 当前 pyproject.toml 缺失项

```toml
# 应该补充的内容
[project]
license = {text = "MIT"}  # 或其他
authors = [{name = "..."}]
readme = "README.md"
classifiers = [
    "Development Status :: 3 - Alpha",
    "Operating System :: MacOS",
    "Topic :: Multimedia :: Sound/Audio :: Editors",
]

[project.scripts]
hermes = "hermes_core.cli:main"  # CLI 入口

[project.optional-dependencies]
test = ["pytest>=8", "pytest-cov>=6"]
dev = ["ruff", "mypy"]  # 开发工具
```

---

## 📋 落地路线图建议

### Phase 1: 安全可用（1-2 周）
- [ ] 幂等性守卫（检测已执行状态）
- [ ] REAPER Undo Block 集成
- [ ] `atexit` UI 清理钩子
- [ ] 插件预检（preflight_plugins）
- [ ] README.md + 安装指南
- [ ] 补全 pyproject.toml 依赖

### Phase 2: 可配置（1-2 周）
- [ ] MixingProfile 配置体系（YAML 驱动）
- [ ] 插件链可配置化
- [ ] CLI 入口 `hermes vocal-mix`
- [ ] 项目状态校验（防止工程切换）

### Phase 3: 健壮（2-3 周）
- [ ] RPR 调用超时封装
- [ ] 断线自动重连
- [ ] 渲染失败自动重试
- [ ] 非 WAV 格式支持
- [ ] 多采样率 LUFS 修正（同 CODE_REVIEW #1）
- [ ] 磁盘空间预检

### Phase 4: 好用（2-4 周）
- [ ] 批处理模式
- [ ] 进度回调 / 进度条
- [ ] API 文档（Sphinx / mkdocs）
- [ ] 示例脚本 + 教程
- [ ] 错误信息人性化
