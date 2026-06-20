# Contributing to Hermes-Core

## Development Setup

```bash
git clone <repo>
cd hermes-core
python3.11 -m venv .venv
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
1. **L1** (`bridge.py`) — REAPER connection, UI suppression, cross-platform dialog handling
2. **L2** — domain managers + support modules:
   - Managers: `track.py`, `bus.py`, `fx.py`, `send.py`, `render.py`
   - Analysis: `signal.py`, `spectrum.py`, `reference.py`
   - Processing: `eq_engine.py`, `comp_engine.py`, `fx_builder.py`, `spatial_engine.py`, `audio_utils.py`
   - Subsystems: `mastering.py`, `gain_staging.py`, `master_templates.py`, `chain_renderer.py`, `automation.py`
   - Profiles: `profiles.py`, `genre_tables.py`, `plugin_registry.py`, `normalize.py`
   - Support: `dialog_handler.py`, `loudness_optimizer.py`, `project_meta.py`, `config.py`, `security.py`
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
