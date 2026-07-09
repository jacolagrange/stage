"""
Registry of available search strategies, so the CLI can dispatch on a single
--strategy flag without hardcoding per-strategy logic. Adding a new strategy
means writing its run function + resumable-state dataclass (see greedy.py's
GreedySearchState / spea2.py's Spea2SearchState for the pattern each should
follow), then registering it here -- cli.py needs no further changes since
it forwards matching CLI flags to the run function automatically based on
its parameter names (see cli.py's use of inspect.signature).
"""
from dataclasses import dataclass
from typing import Callable

from . import greedy, spea2, mesmo, hybrid


@dataclass(frozen=True)
class StrategySpec:
    name: str
    run: Callable[..., list]   # (reference_config, sniper, outputdir, benchmarks, alpha, max_iterations, **kwargs) -> list[DesignPoint]


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
