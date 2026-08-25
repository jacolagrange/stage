"""Registry of available search strategies -- see README's "Extending:
adding a new strategy" section."""
from dataclasses import dataclass
from typing import Callable

from . import greedy, spea2, mesmo, hybrid


@dataclass(frozen=True)
class StrategySpec:
    name: str
    run: Callable[..., list]


STRATEGIES: dict[str, StrategySpec] = {
    "greedy": StrategySpec(
        name="greedy",
        run=greedy.explore_pareto_front_with_sensitivity,
    ),
    "spea2": StrategySpec(
        name="spea2",
        run=spea2.explore_pareto_front_spea2,
    ),
    "mesmo": StrategySpec(
        name="mesmo",
        run=mesmo.explore_pareto_front_mesmo,
    ),
    "hybrid": StrategySpec(
        name="hybrid",
        run=hybrid.explore_pareto_front_hybrid,
    ),
}
