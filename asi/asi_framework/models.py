from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DesignPoint:
    """One evaluated design point.

    A point may be evaluated against several benchmarks at once (see
    greedy.py's evaluate_point); area/peak_power/time are then the mean
    across benchmarks and speedup is their geometric mean, while
    per_benchmark keeps each benchmark's own {time, speedup} so a config
    that trades a loss on one workload for a bigger win on another is
    still inspectable rather than only visible as a blended number.
    """
    params: dict[str, Any]
    area: float                    # mm^2
    peak_power: float              # W
    time: float                    # ns
    asi: float = 0.0
    speedup: float = 0.0
    modified_params: set[str] = field(default_factory=set)
    output_path: Path | None = field(default=None, compare=False, repr=False)
    per_benchmark: dict[str, dict[str, float]] = field(default_factory=dict)
