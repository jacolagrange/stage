from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DesignPoint:
    """One evaluated design point; area/peak_power/time are means across
    benchmarks, per_benchmark keeps each benchmark's own values."""
    params: dict[str, Any]
    area: float
    peak_power: float
    time: float
    asi: float = 0.0
    speedup: float = 0.0
    modified_params: set[str] = field(default_factory=set)
    output_path: Path | None = field(default=None, compare=False, repr=False)
    per_benchmark: dict[str, dict[str, float]] = field(default_factory=dict)
