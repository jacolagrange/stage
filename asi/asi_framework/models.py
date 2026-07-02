from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class DesignPoint:
    """One evaluated design point."""
    params: dict[str, Any]
    area: float                    # mm^2
    peak_power: float              # W
    time: float                    # ns
    asi: float = 0.0
    speedup: float = 0.0
    modified_params: set[str] = field(default_factory=set)
    output_path: Path | None = field(default=None, compare=False, repr=False)
