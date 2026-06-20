"""
Mix profile configuration — dataclasses + YAML serialisation.

Define a mixing workflow as data (plugin chains, routing, levels) so that
swapping plugins or adjusting the pipeline doesn't require editing engine
source code.
"""

import logging
import math
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── type aliases (name-based fallback when fx_type is empty) ─────

_TYPE_ALIASES: dict[str, str] = {
    "1176":     "fet",
    "CLA-76":   "fet",
    "LA-2A":    "opto",
    "CL-1B":    "tube_opto",
    "CL1B":     "tube_opto",
    "Tube-Tech": "tube_opto",
    "RVox":     "rvox",
    "RComp":    "vca",
    "API 2500": "vca",
    "Pro-C":    "vca",
    "SSL":      "vca",
    "Pro-Q":    "eq",
    "ReaEQ":    "eq",
    "Pro-L":    "limiter",
    "Valhalla": "reverb",
    "ReaVerb":  "reverb",
    "Decapitator": "saturation",
    "Saturn":      "saturation",
    "Pultec":      "color_eq",
    "EQP-1A":      "color_eq",
    "EQP1A":       "color_eq",
    "Bettermaker": "color_eq_232d",
    "EQ232D":      "color_eq_232d",
    "Shadow Hills": "tube_opto_sh",
    "Inflator":    "harmonic",
    "Oxford Inflator": "harmonic",
    "Maag":        "air_eq",
    "Maag EQ4":    "air_eq",
    "MicroShift":  "doubler",
    "Doubler":     "doubler",
}


def _resolve_fx_type(fx_name: str, declared_type: str = "") -> str:
    """Return the compressor type string for *fx_name*.

    If *declared_type* is non-empty it is returned as-is (profile author's
    explicit choice).  Otherwise a case-insensitive substring match against
    :data:`_TYPE_ALIASES` is used as a fallback.
    """
    if declared_type:
        return declared_type
    name_lower = fx_name.lower()
    for alias, typ in _TYPE_ALIASES.items():
        if alias.lower() in name_lower:
            return typ
    return ""


# ── compressor style presets (attack / release per genre) ────────

_COMPRESSOR_PRESETS: dict[str, dict[str, dict[str, float]]] = {
    "vocal": {
        "pop":     {"attack_ms": 5.0,  "release_ms": 80.0},
        "folk":    {"attack_ms": 10.0, "release_ms": 120.0},
        "rock":    {"attack_ms": 3.0,  "release_ms": 60.0},
        "default": {"attack_ms": 5.0,  "release_ms": 100.0},
    },
    "backing": {
        "pop":     {"attack_ms": 10.0, "release_ms": 150.0},
        "folk":    {"attack_ms": 15.0, "release_ms": 200.0},
        "rock":    {"attack_ms": 5.0,  "release_ms": 100.0},
        "default": {"attack_ms": 10.0, "release_ms": 150.0},
    },
}


def _get_compressor_preset(role: str, genre: str) -> dict[str, float]:
    """Return ``{attack_ms, release_ms}`` for *role* + *genre*."""
    role_presets = _COMPRESSOR_PRESETS.get(role, _COMPRESSOR_PRESETS.get("vocal", {}))
    return role_presets.get(genre, role_presets.get("default", {"attack_ms": 5.0, "release_ms": 100.0}))


# ── BPM-aware compressor timing ──────────────────────────────────


# BPM → attack/release mapping (from docs/MIXING_KNOWLEDGE_BASE.md §3).
_BPM_PRESETS: dict[str, dict[str, float]] = {
    "fast":  {"attack_ms": 3.0,  "release_ms": 60.0},   # BPM >= 130
    "med":   {"attack_ms": 5.0,  "release_ms": 100.0},  # BPM 90–130
    "slow":  {"attack_ms": 10.0, "release_ms": 200.0},  # BPM < 90
}


def get_bpm_timing(bpm: float) -> dict[str, float] | None:
    """Return ``{attack_ms, release_ms}`` for a given BPM, or ``None``.

    BPM must be a positive finite number, otherwise ``None`` is returned
    (callers should fall back to :func:`_get_compressor_preset`).

    Thresholds match :data:`_BPM_PRESETS`:

    - ``>= 130`` → fast (attack 3 ms, release 60 ms)
    - ``90–129`` → med  (attack 5 ms, release 100 ms)
    - ``< 90``   → slow (attack 10 ms, release 200 ms)
    """
    if not isinstance(bpm, (int, float)):
        return None
    if not (bpm > 0 and math.isfinite(bpm)):
        return None

    if bpm >= 130.0:
        return dict(_BPM_PRESETS["fast"])
    elif bpm >= 90.0:
        return dict(_BPM_PRESETS["med"])
    else:
        return dict(_BPM_PRESETS["slow"])


# ── conservative EQ baseline (no FFT analysis) ───────────────────

_EQ_BASELINE: dict[str, list[dict]] = {
    "vocal": [
        # Remove sub-bass rumble, proximity effect
        {"type": "hp", "freq_hz": 80.0, "q": 0.7},
        # Gentle presence lift — helps vocal cut through
        {"type": "bell", "freq_hz": 3000.0, "gain_db": 1.5, "q": 1.0},
    ],
    "backing": [
        # Remove sub-sonic content only
        {"type": "hp", "freq_hz": 40.0, "q": 0.7},
    ],
}


# ── data structures ──────────────────────────────────────────────


@dataclass
class FXPreset:
    """A single plugin slot in a chain."""
    name: str                           # REAPER FX name (exact match required)
    fx_type: str = ""                   # "vca" | "fet" | "opto" | "rvox" | "eq" | "reverb" | "limiter" | ""
    params: dict[str, float] = field(default_factory=dict)
    alternatives: list[str] = field(default_factory=list)
    eq_position: str = "solo"           # "pre" | "post" | "solo" — only meaningful for EQ slots


@dataclass
class MixingProfile:
    """Complete mixing workflow description, loadable from YAML.

    Usage::

        profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")
        engine.apply_profile(profile)
    """
    name: str = "Default"
    description: str = ""
    chain_variant: str = ""  # "a" = Vocal A (无 UAD), "b" = Vocal B (有 UAD), "" = 默认
    gender: str = ""         # "male" | "female"
    technique: str = ""      # "pop" | "folk" | "bel_canto" | "rock" | ...

    # ── gain staging ──
    clip_gain_ref_db: float = -18.0
    target_lufs: float = -12.0
    ceiling_db: float = -0.5
    tolerance_lufs: float = 0.3

    # ── plugin chains ──
    vocal_chain: list[FXPreset] = field(default_factory=list)
    backing_chain: list[FXPreset] = field(default_factory=list)
    bus_reverb: Optional[FXPreset] = None
    reverb_level_db: float = -8.0
    bus_delay: Optional[FXPreset] = None
    delay_level_db: float = -12.0
    master_limiter: FXPreset = field(
        default_factory=lambda: FXPreset(name="VST: FabFilter Pro-L 2 (FabFilter)")
    )

    # ── genre table (backing reduction LU range) ──
    genre_table: dict[str, list[int]] = field(default_factory=lambda: {
        "folk":                   [3, 6],
        "pop":                    [6, 9],
        "chinese_folk_bel_canto": [9, 12],
    })

    @classmethod
    def for_genre(cls, genre: str, variant: str = "a") -> "MixingProfile":
        """根据流派名加载对应的 Profile YAML。

        YAML 文件位于 ``profiles/vocal_{variant}_{genre}.yaml``。
        未找到对应文件时返回默认 Profile。

        Parameters
        ----------
        genre : str
            流派名称，如 ``"pop"``, ``"rock"``, ``"jazz"`` 等。
        variant : str
            链版本。``"a"`` = Vocal A（无 UAD，默认），``"b"`` = Vocal B（有 UAD）。
            传 ``""`` 加载旧版无前缀 profile。

        Returns
        -------
        MixingProfile
        """
        import os
        _GENRE_YAML_MAP: dict[str, str] = {
            "pop": "vocal_pop",
            "rock": "vocal_rock",
            "folk": "vocal_folk",
            "ballad": "vocal_ballad",
            "electronic": "vocal_electronic",
            "hiphop": "vocal_hiphop",
            "rnb": "vocal_rnb",
            "jazz": "vocal_jazz",
            "chinese_folk_bel_canto": "vocal_chinese_bel_canto",
            "chinese_bel_canto": "vocal_chinese_bel_canto",
        }

        profile_name = _GENRE_YAML_MAP.get(genre, "vocal_pop")
        if variant:
            profile_name = profile_name.replace("vocal_", f"vocal_{variant}_")
        # 从 src/hermes_core/profiles.py 上溯三级到项目根目录
        _project_root = os.path.dirname(
            os.path.dirname(os.path.dirname(__file__))
        )
        profile_dir = os.path.join(_project_root, "profiles")
        yaml_path = os.path.join(profile_dir, f"{profile_name}.yaml")

        try:
            return cls.from_yaml(yaml_path)
        except (FileNotFoundError, OSError):
            log.warning("Profile YAML not found: %s, using default chain", yaml_path)
            return cls(vocal_chain=get_default_vocal_chain())

    @classmethod
    def from_yaml(cls, path: str) -> "MixingProfile":
        """Load a mixing profile from a YAML file.

        Example YAML::

            name: "Vocal Pop"
            target_lufs: -12.0

            vocal_chain:
              - name: "VST: FabFilter Pro-Q 3 (FabFilter)"
              - name: "Waves RVox (Waves)"

            bus_reverb:
              name: "ValhallaVintageVerb (Valhalla DSP)"
              params:
                Mix: 0.3

            master_limiter:
              name: "VST: FabFilter Pro-L 2 (FabFilter)"
              params:
                "Output Level": -0.5

            genre_table:
              rock: [6, 10]
              ballad: [3, 5]
        """
        raw = yaml.safe_load(Path(path).read_text())
        return cls._from_dict(raw)

    @staticmethod
    def _parse_fx(raw) -> Optional[FXPreset]:
        """Parse a single FX entry from dict."""
        if raw is None:
            return None
        if isinstance(raw, str):
            return FXPreset(name=raw)
        return FXPreset(
            name=raw.get("name", ""),
            fx_type=raw.get("type", raw.get("fx_type", "")),
            params=raw.get("params", {}),
            alternatives=raw.get("alternatives", []),
            eq_position=raw.get("eq_position", "solo"),
        )

    @classmethod
    def _from_dict(cls, d: dict) -> "MixingProfile":
        """Build a profile from a parsed YAML dict."""
        vocal = [cls._parse_fx(f) for f in d.get("vocal_chain", [])]
        backing = [cls._parse_fx(f) for f in d.get("backing_chain", [])]
        reverb = cls._parse_fx(d.get("bus_reverb"))
        delay = cls._parse_fx(d.get("bus_delay"))
        limiter = cls._parse_fx(d.get("master_limiter")) or FXPreset(
            name="VST: FabFilter Pro-L 2 (FabFilter)"
        )

        genre_table = {
            k: list(v) for k, v in d.get("genre_table", {
                "folk": [3, 6], "pop": [6, 9], "chinese_folk_bel_canto": [9, 12],
            }).items()
        }

        return cls(
            name=d.get("name", "Custom"),
            description=d.get("description", ""),
            chain_variant=d.get("chain_variant", ""),
            gender=d.get("gender", ""),
            technique=d.get("technique", ""),
            clip_gain_ref_db=float(d.get("clip_gain_ref_db", -18.0)),
            target_lufs=float(d.get("target_lufs", -12.0)),
            ceiling_db=float(d.get("ceiling_db", -0.5)),
            tolerance_lufs=float(d.get("tolerance_lufs", 0.3)),
            vocal_chain=[f for f in vocal if f is not None],
            backing_chain=[f for f in backing if f is not None],
            bus_reverb=reverb,
            reverb_level_db=float(d.get("reverb_level_db", -8.0)),
            bus_delay=delay,
            delay_level_db=float(d.get("delay_level_db", -12.0)),
            master_limiter=limiter,
            genre_table=genre_table,
        )

    def all_fx_names(self) -> list[str]:
        """Return every distinct FX name in this profile."""
        names: list[str] = []
        for fx in self.vocal_chain + self.backing_chain:
            names.append(fx.name)
        if self.bus_reverb:
            names.append(self.bus_reverb.name)
        if self.bus_delay:
            names.append(self.bus_delay.name)
        names.append(self.master_limiter.name)
        return list(dict.fromkeys(names))  # preserve order, dedupe


# ── default vocal chain ──────────────────────────────────────────


def get_default_vocal_chain() -> list[FXPreset]:
    """返回默认 9 段人声处理链 (Vocal A: 无 UAD)。

    与 ``profiles/vocal_a_chinese_bel_canto.yaml`` 等 YAML 保持一致。
    已废弃 SSLEQ/MicroShift 等早期测试插件。
    """
    return [
        FXPreset(
            name="VST: FabFilter Pro-Q 3 (FabFilter)",
            fx_type="eq",
            eq_position="pre",
        ),
        FXPreset(
            name="VST3: CLA-76 Mono (Waves)",
            fx_type="fet",
        ),
        FXPreset(
            name="VST3: Decapitator (Soundtoys)",
            fx_type="saturation",
        ),
        FXPreset(
            name="VST: FabFilter Pro-DS (FabFilter)",
            fx_type="deesser",
        ),
        FXPreset(
            name="VST3: Bettermaker EQ232D (Plugin Alliance)",
            fx_type="color_eq_232d",
        ),
        FXPreset(
            name="VST3: RVox Mono (Waves)",
            fx_type="rvox",
        ),
        FXPreset(
            name="VST3: Oxford Inflator (Sonnox)",
            fx_type="harmonic",
        ),
        FXPreset(
            name="VST3: Shadow Hills Mastering Compressor (Plugin Alliance)",
            fx_type="tube_opto_sh",
        ),
        FXPreset(
            name="VST3: Maag EQ4 (Plugin Alliance)",
            fx_type="air_eq",
        ),
    ]
