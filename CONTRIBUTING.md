# Contributing to Hermes-Core

## Development Setup

```bash
git clone <repo>
cd hermes-core
python3.13 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

## Running Tests

```bash
# Unit tests (no REAPER required)
pytest tests/ -m unit

# All tests (REAPER must be running)
pytest tests/

# With coverage
pytest tests/ --cov=src/hermes_core --cov-report=html
```

## Code Style

- **Formatting**: ruff
- **Type checking**: mypy
- **Import sorting**: ruff (isort rules)

```bash
ruff check src/ tests/
ruff format src/ tests/
mypy src/
```

## Architecture

See `PROJECT_STATUS.md` for the full architecture description.

Three layers:
1. **L1** (`bridge.py`) — REAPER connection, UI suppression, dialog killing
2. **L2** (`track.py`, `bus.py`, `fx.py`, `send.py`, `render.py`, `signal.py`, `loudness_optimizer.py`) — domain managers
3. **L3** (`engine.py`) — unified MixingEngine API

## Commit Convention

```
<type>: <description>
```

Types: `feat`, `fix`, `refactor`, `docs`, `test`, `chore`, `perf`, `ci`

## Adding a New Plugin Preset

1. Add your plugin configuration to `profiles/<name>.yaml`
2. Test with: `hermes check --profile profiles/<name>.yaml`
3. Document the plugin requirements in the profile description
