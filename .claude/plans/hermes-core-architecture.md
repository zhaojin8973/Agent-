# Hermes Core — Architecture Specification v0.1

**Date**: 2026-05-24
**Status**: CTO Review
**Source**: Phase 0 Diagnostic Data + architect agent

## 1. Module Dependency Graph (Acyclic)

```
Layer 1: bridge.py  →  stdlib + reapy (zero project imports)

Layer 2: track.py   →  bridge
         bus.py     →  bridge
         fx.py      →  bridge
         send.py    →  bridge
         render.py  →  bridge
         signal.py  →  bridge (optional, core is pure numpy)

Layer 3: engine.py  →  bridge, track, bus, fx, send, render, signal
```

**Zero cross-imports between Layer 2 modules.** `send.py` does NOT import `fx.py`.

## 2. Public API Per Module

### bridge.py — `ReaperBridge`, `_extract_reaper_string()`

`_extract_reaper_string(result) -> str` — module-level utility, lives HERE only. Handles ARM64 5-value tuple format: collects all non-pointer strings from the tuple, returns the LAST one (the value, not the key or ptr).

```python
class ReaperBridge:
    # Lifecycle
    def connect(launch=True) -> bool
    def disconnect()
    def shutdown(graceful=True)
    def force_shutdown()
    def health_check() -> dict

    # Properties
    api   # raw RPR API handle
    rpr   # reapy module

    # Dialog defense
    def start_dialog_killer(interval=0.5)

    # Project state (safe, non-modal)
    def get_sample_rate() -> int
    def get_project_length() -> float
    def get_project_snapshot() -> dict

    # Undo
    def begin_undo_block()
    def end_undo_block(description="", flags=0)

    # Context manager
    def __enter__() -> ReaperBridge
    def __exit__(...)
```

### track.py — `TrackManager`, `TrackInfo`, `db_to_norm()`, `norm_to_db()`

```python
class TrackManager(bridge: ReaperBridge):
    def create(index=-1, name="") -> int
    def delete(index)
    def set_name(i, name) / set_volume(i, db) / set_pan(i, pan)
    def set_mute(i, mute) / set_solo(i, solo)
    def set_folder_depth(i, depth)
    def get(index) -> TrackInfo | None
    def list_all() -> list[TrackInfo]
    def count() -> int
    def import_media(track_index, file_path, position=0.0) -> bool
    def import_stems(file_paths, position=0.0) -> list[dict]
    def get_item_position(track_index, item_index=0) -> float

class TrackInfo:  # dataclass
    index, name, volume_db, pan, mute, solo, fx_count, depth, item_count, selected
```

### bus.py — `BusManager`, `FolderInfo`

```python
class BusManager(bridge: ReaperBridge):
    def create(name, child_indices, position=None) -> int
    def dissolve(bus_index)
    def delete(bus_index)
    def add_child(bus_index, child_index)
    def remove_child(child_index)
    def get_structure() -> list[FolderInfo]
    def validate(bus_index) -> dict
```

### fx.py — `FxManager`, `FxInfo`

**Plugin-agnostic. No ReaEQ-specific methods.** Uses reapy's high-level `Track`/`FX`/`FXParamsList` API — reapy internally handles ARM64 5-tuple unpacking with fixed indices (`GetFXName[3]`, `GetParamName[4]`, `GetParam[-2:]`), and strips `VST:`/`VST3:` prefixes from FX names before `AddByName`.

```python
class FxManager(bridge: ReaperBridge):
    def add(track_index, fx_name, instantiate=True) -> int
    def remove(track_index, fx_index)
    def get_chain(track_index) -> list[FxInfo]

    # Params — via reapy FXParamsList, supports index and name access
    def set_param(track_index, fx_index, param, normalized)
        # param: int (index) or str (name) — e.g. fx.params["Threshold"] = 0.5
    def get_param(track_index, fx_index, param) -> float
    def get_param_name(track_index, fx_index, param_index) -> str
    def get_param_list(track_index, fx_index) -> list[dict]  # {name, value}

    def set_enabled(track_index, fx_index, enabled)
    def copy_to(src_track, src_fx, dest_track, dest_fx=-1, move=False)
```

### send.py — `SendManager`, `SendInfo`

**No `create_aux_return`. No import of fx.py.**

```python
class SendManager(bridge: ReaperBridge):
    def create(src, dest, level_db=0.0, mode="post-fader", pan=0.0) -> dict
    def remove(src, send_idx, mode="post-fader")
    def set_level(src, send_idx, level_db, mode="post-fader")
    def set_pan(src, send_idx, pan, mode="post-fader")
    def set_mute(src, send_idx, mute, mode="post-fader")
    def get_info(src, send_idx, category=None) -> SendInfo | None
    def list_all(track_index) -> list[SendInfo]
```

### render.py — `RenderManager`

**No SafetyGateway, CircuitBreaker, RenderGuard, PostAuditor — KISS.**

```python
class RenderManager(bridge: ReaperBridge):
    def render_mix(output_dir, bounds="time_selection", fmt="wav_24bit", timeout=120) -> str
    def render_stems(output_dir, stereo=True, timeout=300) -> list[str]
    def set_time_selection(start, end)
    def set_time_selection_to_item(track_index=0, item_index=0)
    def get_settings() -> dict
```

### signal.py — `SignalAnalyzer`, `SignalReport`

**Bridge optional. Core analysis is pure numpy.**

```python
class SignalAnalyzer(bridge: ReaperBridge | None = None):
    def analyze(file_path) -> SignalReport

class SignalReport:  # dataclass
    rms_db, peak_db, integrated_lufs, true_peak_dbtp,
    clip_count, clip_passed, silence_passed, duration_sec, sample_rate
```

### engine.py — `HermesEngine`

**Composition over inheritance.**

```python
class HermesEngine(reaper_path=None, workspace="/tmp/hermes"):
    def initialize(allow_offline=False) -> dict
    def shutdown()

    # Manager access
    bridge: ReaperBridge
    tracks: TrackManager
    bus: BusManager
    fx: FxManager
    send: SendManager
    render: RenderManager
    signal: SignalAnalyzer

    # Convenience delegates (one-liners to managers)
    def import_stems(file_paths, position=0.0) -> list[dict]
    def create_bus(name, child_indices) -> int
    def create_send(src, dest, level_db=0.0) -> dict
    def add_fx(track_index, fx_name) -> int
    def set_fx_param(track, fx, param, value)
    def render_mix(output_dir, verify=True) -> dict

    # Orchestration (multi-manager operations, was in send.py)
    def create_aux_return(name, fx_names, position=-1) -> int
    def create_parallel_compression(src_indices, fx_name, level_db=-6.0) -> dict
    def health_check() -> dict
```

## 3. Key Design Decisions

| Decision | Why |
|----------|-----|
| `_extract` lives ONLY in bridge | Old project had 3 copies (bridge, track, fx). One source of truth. |
| No ReaEQ API in fx.py | Old `add_eq_band`/`set_eq_band` hardcoded ReaEQ's band layout. New: `set_param(track, fx, param_idx, norm_value)` with runtime param name discovery via `get_param_list()`. |
| `create_aux_return` in engine.py | Eliminates send.py → fx.py circular dependency. Engine orchestrates: create track → add FX. |
| SignalAnalyzer bridge optional | Core analysis is pure numpy. Bridge only needed for project SR context. |
| Composition, not inheritance | Engine holds managers, doesn't subclass them. Cleaner separation, easier testing. |
| No `__init__.py` barrel re-exports | Each module directly: `from hermes_core.track import TrackManager`. |

## 4. Test Strategy

```
tests/
├── conftest.py         # mock_bridge fixture, real_bridge (module-scoped)
├── test_bridge.py      # unit: mock reapy
├── test_track.py       # unit: mock bridge
├── test_bus.py         # unit: mock bridge
├── test_fx.py          # unit: mock bridge
├── test_send.py        # unit: mock bridge
├── test_render.py      # unit: mock bridge
├── test_signal.py      # unit: pure numpy, no REAPER
├── test_engine.py      # integration: real REAPER
└── fixtures/
    └── sine_1k.wav
```

- **Unit tests** (fast, no REAPER): Mock `ReaperBridge.api` with `MagicMock`. Verify correct RPR function + args.
- **Integration tests** (require REAPER): `test_engine.py` only. Full pipeline: kill → start → operate → verify → kill.
- **Signal tests**: Pure Python — generate sine WAV → verify RMS/Peak against known references.

## 5. pyproject.toml

```toml
[project]
name = "hermes-core"
version = "0.1.0"
description = "REAPER DAW automation — lean 3-layer engine"
requires-python = ">=3.11"
dependencies = ["numpy>=1.26", "python-reapy>=1.0"]

[project.optional-dependencies]
test = ["pytest>=8", "pytest-cov>=6"]

[tool.pytest.ini_options]
markers = ["unit: no REAPER", "integration: requires REAPER"]
testpaths = ["tests"]
addopts = ["-v", "--tb=short"]
```
