# 贡献指南

## 开发环境搭建

```bash
git clone https://github.com/zhaojin/hermes-core
cd hermes-core
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

## 架构概览

hermes-core 采用三层架构，详见[架构文档](architecture.md)：

| 层 | 模块 | 职责 |
|------|------|------|
| L1 | `bridge.py` | REAPER 连接、UI 抑制、弹窗守护 |
| L2 | `track.py` `bus.py` `fx.py` `send.py` `render.py` `signal.py` | 各领域管理器 |
| L3 | `engine.py` | 统一 MixingEngine API |

## 代码风格

```bash
# 检查代码风格
ruff check src/ tests/

# 自动格式化
ruff format src/ tests/

# 类型检查
mypy src/hermes_core --ignore-missing-imports
```

遵循标准：

- **PEP 8** 格式约定
- **类型标注**：所有函数签名必须有类型标注
- **不可变性**：优先使用 `frozen=True` 的 dataclass
- **ruff** 用于 linting 和格式化

## 运行测试

```bash
# 单元测试（不需要 REAPER）
pytest tests/ -m unit

# 集成测试（需要 REAPER 运行中）
pytest tests/ -m integration -v

# 全部测试
pytest tests/

# 覆盖率报告
pytest tests/ --cov=src/hermes_core --cov-report=html

# 覆盖率必须 >= 80%
pytest tests/ --cov=src/hermes_core --cov-fail-under=80
```

## 提交规范

遵循 Conventional Commits 格式：

```
<类型>: <中文描述>

<可选正文>
```

类型：

| 类型 | 说明 |
|------|------|
| `feat` | 新功能 |
| `fix` | 修复 bug |
| `refactor` | 重构 |
| `docs` | 文档更新 |
| `test` | 测试相关 |
| `chore` | 杂项（依赖更新等） |
| `perf` | 性能优化 |
| `ci` | CI/CD 配置 |

### 示例

```
feat: 添加空间效果器引擎支持
fix: 修复 ARM64 元组解包错误
refactor: 将流派参数表提取到独立模块
docs: 补充 Plugin Registry API 文档
```

## 添加新功能

1. **先查注册表**：确认是否有已注册的插件/参数可用
2. **从 L2 开始**：新领域逻辑添加到对应的 L2 管理器
3. **暴露到 L3**：在 `engine.py` 中组合新功能
4. **导出公共 API**：更新 `__init__.py` 的 `__all__`
5. **编写测试**：覆盖率达到 80%+
6. **更新文档**：添加到对应的 `docs/` 文件

## 添加新插件

1. 在 `normalize.py` 的 `PLUGIN_REGISTRY` 中添加参数规格
2. （可选）在 `plugin_registry.py` 中添加信号链映射
3. 编写或更新 `profiles/*.yaml` 使用新插件
4. 使用 `hermes check` 验证插件可访问性

## 添加新流派

在 `genre_tables.py` 中添加新流派的参数：

```python
_GENRE_VOCAL_TO_BACKING["new_genre"] = [vocal_gain, backing_gain]
_GENRE_TARGET_LUFS["new_genre"] = -12.0
# 以及其他相关字典...
```

## CI/CD

提交或 PR 到 `master`/`main` 分支会自动触发：

- ruff 代码风格检查
- mypy 类型检查
- Python 3.11/3.12/3.13 单元测试
- 覆盖率 >= 80% 检查

集成测试（需要 REAPER）为手动触发。

## 项目结构

```
hermes-core/
├── src/hermes_core/     # 源代码
│   ├── __init__.py      # 公共 API
│   ├── engine.py        # L3: MixingEngine
│   ├── bridge.py        # L1: REAPER 桥接
│   ├── track.py         # L2: 音轨管理
│   ├── bus.py           # L2: 总线管理
│   ├── fx.py            # L2: 效果器管理
│   ├── send.py          # L2: 发送管理
│   ├── render.py        # L2: 渲染管理
│   ├── signal.py        # L2: 信号分析
│   ├── loudness_optimizer.py  # L2: 响度优化
│   ├── profiles.py      # MixingProfile 配置
│   ├── normalize.py     # 参数归一化 + 注册表
│   ├── plugin_registry.py    # 信号链注册表
│   ├── genre_tables.py  # 流派参数表
│   ├── security.py      # 安全层
│   ├── agent_protocol.py     # Agent 通信层
│   ├── dag.py           # DAG / AudioNode
│   ├── config.py        # 全局配置
│   ├── exceptions.py    # 异常层次
│   └── cli.py           # CLI 入口
├── tests/               # 测试
├── profiles/            # YAML Profile 配置
├── docs/                # 文档站源码
├── examples/            # 使用示例
├── tools/               # 开发工具
├── mkdocs.yml           # MkDocs 配置
├── pyproject.toml       # 项目配置
└── .github/workflows/   # CI/CD
```

## 提问/讨论

- 提交 Issue 描述问题或建议
- PR 前请确保 CI 通过
