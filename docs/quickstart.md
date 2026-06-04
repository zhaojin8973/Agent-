# 快速开始

## 环境要求

| 依赖 | 版本 | 说明 |
|------|------|------|
| REAPER | 7.73+ | 必须安装并运行中 |
| Python | 3.11–3.13 | 推荐 3.13 |
| python-reapy | >=0.10 | REAPER 远程控制 |

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/zhaojin/hermes-core
cd hermes-core

# 2. 创建虚拟环境
python3.13 -m venv .venv
source .venv/bin/activate

# 3. 安装（开发依赖可选）
pip install -e ".[dev,test]"
```

## REAPER 配置

在 REAPER 中设置 Python 路径：

1. 打开 REAPER，按 `Cmd+,` 打开 Preferences
2. 导航到 **Plug-ins > ReaScript**
3. 勾选 "Enable Python for use with ReaScript"
4. 填入 Python 库路径（macOS ARM64 示例）：

    ```
    /opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13
    ```

或直接编辑 `reaper.ini`：

```ini
[reaper]
pythonlibpath64=/opt/homebrew/opt/python@3.13/Frameworks/Python.framework/Versions/3.13
```

## 5 分钟跑通一次贴唱混音

```python
from hermes_core import MixingEngine

with MixingEngine(watchdog=True) as eng:
    # 1. 创建工程
    eng.create_project("望归", "./output", sample_rate=48000)

    # 2. 导入 + 增益分级
    eng.prepare_stems(
        ["./望归_Vocal.wav", "./望归_Backing.wav"],
        genre="chinese_folk_bel_canto",
        vocal_indices=[0],
    )

    # 3. 添加人声处理（EQ + 压缩）
    eng.add_fx(0, "FabFilter Pro-Q 3 (FabFilter)")
    eng.add_fx(0, "Waves RVox (Waves)")

    # 4. 创建混响发送
    eng.create_reverb_send(0, level_db=-8.0)

    # 5. 母带 + 渲染
    result = eng.finalize_master(target_lufs=-12.0)
    print(f"输出文件: {result['output_path']}")
    print(f"实际 LUFS: {result['achieved_lufs']}")
```

## 使用 MixingProfile（推荐）

```python
from hermes_core import MixingEngine, MixingProfile

profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")

with MixingEngine() as eng:
    eng.create_project("DemoSong", "./output", sample_rate=48000)
    eng.prepare_stems(
        ["./vocal.wav", "./backing.wav"],
        genre="pop",
        vocal_indices=[0],
    )
    eng.apply_profile(profile)  # 自动加载 FX 链 + 发送 + 母带
    eng.finalize_master()
```

## CLI 使用

```bash
# 一键贴唱混音
hermes vocal-mix \
  --vocal "望归 Vocal.wav" \
  --backing "望归 伴奏.wav" \
  --genre chinese_folk_bel_canto \
  --output ./output \
  --target-lufs -12

# 使用自定义配置
hermes vocal-mix \
  --vocal vocal.wav \
  --backing inst.wav \
  --profile profiles/rock.yaml

# 批量处理
hermes batch \
  --input-dir ./songs \
  --profile profiles/pop.yaml \
  --output-dir ./masters

# 插件预检
hermes check --profile profiles/rock.yaml
```

## 运行测试

```bash
# 单元测试（不需要 REAPER）
pytest tests/ -m unit

# 全部测试（需要 REAPER 运行中）
pytest tests/

# 覆盖率报告
pytest tests/ --cov=src/hermes_core --cov-report=html
```

## 下一步

- [API 参考 - 引擎](api/engine.md) — MixingEngine 完整 API
- [混音指南 - 流派 Profile](guides/profiles.md) — 编写自定义 Profile
- [开发 - 架构](dev/architecture.md) — 三层架构详解
