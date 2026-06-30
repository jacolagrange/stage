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
 
 
@dataclass
class DesignPoint:
    """One evaluated design point."""
    params: dict[str, Any]      # overrides from baseline, e.g. {"l3_size": 4096}
    area: float                 # mm^2
    peak_power: float           # W
    time: float                 # ns
    asi: float = 0.0
    speedup: float = 0.0
    # which parameters have already been modified (not to be varied again in children)
    modified_params: set[str] = field(default_factory=set)
 
