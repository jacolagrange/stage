"""Pure math over DesignPoints: the ASI formula, Pareto dominance/front
bookkeeping, hypervolume, and search-convergence detection. No I/O, no
Sniper -- see evaluation.py for that."""
from typing import Any

from .models import DesignPoint


def calculate_asi(Ay: float, Ax: float, Py: float, Px: float, alpha: float) -> float:
    return (1 - alpha * (Ax / Ay)) / ((1 - alpha) * (Px / Py))


def geomean(values: list[float]) -> float:
    product = 1.0
    for v in values:
        product *= v
    return product ** (1.0 / len(values))


def params_key(params: dict[str, Any]) -> frozenset:
    return frozenset(params.items())


def dominates(a: DesignPoint, b: DesignPoint) -> bool:
    return (
        a.asi >= b.asi and a.speedup >= b.speedup
        and (a.asi > b.asi or a.speedup > b.speedup)
    )


def update_pareto_front(front: list[DesignPoint], points: list[DesignPoint]) -> list[DesignPoint]:
    """Non-dominated points from front + points, deduplicated by params."""
    all_points = front + points
    non_dominated = [
        p for p in all_points
        if not any(dominates(other, p) for other in all_points if other is not p)
    ]
    deduped: dict[frozenset, DesignPoint] = {}
    for p in non_dominated:
        deduped.setdefault(params_key(p.params), p)
    return list(deduped.values())


def hypervolume(front: list[DesignPoint]) -> float:
    """2D hypervolume of a maximizing Pareto front relative to the origin
    (ASI=0, speedup=0)."""
    if not front:
        return 0.0
    pts = sorted(front, key=lambda p: p.speedup)
    hv = 0.0
    prev_speedup = 0.0
    for p in pts:
        hv += max(0.0, p.asi) * max(0.0, p.speedup - prev_speedup)
        prev_speedup = p.speedup
    return hv


def has_converged(hv_history: list[float], patience: int, rel_tol: float = 1e-3) -> bool:
    """True if hypervolume hasn't meaningfully improved in the last patience
    iterations/generations. Shared convergence check for mesmo (iterations)
    and spea2 (generations)."""
    if len(hv_history) <= patience:
        return False
    best_before = max(hv_history[:-patience])
    recent_best = max(hv_history[-patience:])
    return recent_best <= best_before * (1 + rel_tol)
