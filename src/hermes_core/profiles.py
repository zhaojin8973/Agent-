"""
Mix profile configuration — dataclasses + YAML serialisation.

Define a mixing workflow as data (plugin chains, routing, levels) so that
swapping plugins or adjusting the pipeline doesn't require editing engine
source code.
"""

import logging
import yaml
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)


# ── data structures ──────────────────────────────────────────────


@dataclass
class FXPreset:
    """A single plugin slot in a chain."""
    name: str                           # REAPER FX name (exact match required)
    params: dict[str, float] = field(default_factory=dict)
    alternatives: list[str] = field(default_factory=list)


@dataclass
class MixingProfile:
    """Complete mixing workflow description, loadable from YAML.

    Usage::

        profile = MixingProfile.from_yaml("profiles/vocal_pop.yaml")
        engine.apply_profile(profile)
    """
    name: str = "Default"
    description: str = ""

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
    master_limiter: FXPreset = field(
        default_factory=lambda: FXPreset(name="FabFilter Pro-L 2 (FabFilter)")
    )

    # ── genre table (backing reduction LU range) ──
    genre_table: dict[str, list[int]] = field(default_factory=lambda: {
        "folk":                   [3, 6],
        "pop":                    [6, 9],
        "chinese_folk_bel_canto": [9, 12],
    })

    @classmethod
    def from_yaml(cls, path: str) -> "MixingProfile":
        """Load a mixing profile from a YAML file.

        Example YAML::

            name: "Vocal Pop"
            target_lufs: -12.0

            vocal_chain:
              - name: "FabFilter Pro-Q 3 (FabFilter)"
              - name: "Waves RVox (Waves)"

            bus_reverb:
              name: "ValhallaVintageVerb (Valhalla DSP)"
              params:
                Mix: 0.3

            master_limiter:
              name: "FabFilter Pro-L 2 (FabFilter)"
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
            params=raw.get("params", {}),
            alternatives=raw.get("alternatives", []),
        )

    @classmethod
    def _from_dict(cls, d: dict) -> "MixingProfile":
        """Build a profile from a parsed YAML dict."""
        vocal = [cls._parse_fx(f) for f in d.get("vocal_chain", [])]
        backing = [cls._parse_fx(f) for f in d.get("backing_chain", [])]
        reverb = cls._parse_fx(d.get("bus_reverb"))
        limiter = cls._parse_fx(d.get("master_limiter")) or FXPreset(
            name="FabFilter Pro-L 2 (FabFilter)"
        )

        genre_table = {
            k: list(v) for k, v in d.get("genre_table", {
                "folk": [3, 6], "pop": [6, 9], "chinese_folk_bel_canto": [9, 12],
            }).items()
        }

        return cls(
            name=d.get("name", "Custom"),
            description=d.get("description", ""),
            clip_gain_ref_db=float(d.get("clip_gain_ref_db", -18.0)),
            target_lufs=float(d.get("target_lufs", -12.0)),
            ceiling_db=float(d.get("ceiling_db", -0.5)),
            tolerance_lufs=float(d.get("tolerance_lufs", 0.3)),
            vocal_chain=[f for f in vocal if f is not None],
            backing_chain=[f for f in backing if f is not None],
            bus_reverb=reverb,
            reverb_level_db=float(d.get("reverb_level_db", -8.0)),
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
        names.append(self.master_limiter.name)
        return list(dict.fromkeys(names))  # preserve order, dedupe
