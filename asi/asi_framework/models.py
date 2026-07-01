from dataclasses import dataclass, field
from typing import Any


@dataclass
class DesignPoint:
    """One evaluated design point."""
    params: dict[str, Any]          # overrides from SEARCH_SPACE
    area: float                     # mm^2
    peak_power: float               # W
    time: float                     # ns
    asi: float = 0.0                # computed after baseline is known
    speedup: float = 0.0            # computed after baseline is known
    modified_params: set[str] = field(default_factory=set)