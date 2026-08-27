"""Running a configuration through Sniper (or reading it back from the
global cache) and turning the raw measurements into a DesignPoint. Shared
by every search strategy plus screening.py and titan_batch.py's batch path."""
from pathlib import Path
from typing import Any

from .models import DesignPoint
from .runner import run
from .config import DEFAULTS
from .metrics import calculate_asi, geomean, params_key
from .display import print_evaluated_point
from .search_ops import modified_params


def cached_point(
    key: frozenset, modified_params: set[str], global_cache: dict[frozenset, DesignPoint],
) -> DesignPoint | None:
    """Reconstructs a DesignPoint from a global_cache hit with this call's own
    modified_params (which vary per caller even for the same params key).
    Shared by evaluate_point() and titan_batch.py's batch evaluator."""
    if key not in global_cache:
        return None
    cached = global_cache[key]
    return DesignPoint(
        params=cached.params,
        area=cached.area,
        peak_power=cached.peak_power,
        time=cached.time,
        asi=cached.asi,
        speedup=cached.speedup,
        modified_params=modified_params,
        output_path=cached.output_path,
        per_benchmark=cached.per_benchmark,
    )


def finalize_point(
    params: dict[str, Any],
    modified_params: set[str],
    output_path: Path,
    per_benchmark: dict[str, dict[str, float]],
    areas: list[float],
    powers: list[float],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
    key: frozenset,
) -> DesignPoint:
    """Aggregates one fully-measured point's per-benchmark (area, power, time)
    into a DesignPoint (mean area/power, ASI, geomean speedup) and caches it.
    Shared by evaluate_point() (local runs) and titan_batch.py (Titan-collected
    runs), so both paths produce identical DesignPoints from the same raw
    measurements."""
    for name, data in per_benchmark.items():
        data["speedup"] = baseline.per_benchmark[name]["time"] / data["time"]

    point = DesignPoint(
        params=params,
        area=sum(areas) / len(areas),
        peak_power=sum(powers) / len(powers),
        time=sum(data["time"] for data in per_benchmark.values()) / len(per_benchmark),
        modified_params=modified_params,
        output_path=output_path,
        per_benchmark=per_benchmark,
    )
    point.asi = calculate_asi(baseline.area, point.area, baseline.peak_power, point.peak_power, alpha)
    point.speedup = geomean([data["speedup"] for data in per_benchmark.values()])
    global_cache[key] = point
    return point


def evaluate_point(
    params: dict[str, Any],
    modified_params: set[str],
    output_path: Path,
    reference_config: str,
    sniper: Path,
    benchmarks: dict[str, list[str]],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
) -> tuple[DesignPoint | None, bool, int]:
    """Returns (point, ran_sniper, sniper_invocations), first checking the global_cache for a previously-evaluated point with the same params."""
    key = params_key(params)
    cached = cached_point(key, modified_params, global_cache)
    if cached is not None:
        return cached, False, 0

    areas: list[float] = []
    powers: list[float] = []
    per_benchmark: dict[str, dict[str, float]] = {}
    sniper_invocations = 0
    for name, cmd in benchmarks.items():
        sniper_invocations += 1
        try:
            area, peak_power, time = run(reference_config, sniper, output_path / name, cmd, params)
        except Exception as exc:
            print(f"    FAILED ({output_path.name}/{name}): {exc}")
            return None, True, sniper_invocations
        areas.append(area)
        powers.append(peak_power)
        per_benchmark[name] = {"time": time}

    point = finalize_point(params, modified_params, output_path, per_benchmark, areas, powers, baseline, alpha, global_cache, key)
    return point, True, sniper_invocations


def evaluate_and_print(
    params: dict[str, Any], out: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]],
    baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], prefix: str,
) -> tuple[DesignPoint | None, bool, int]:
    """evaluate_point() + print_evaluated_point() on success -- shared by
    mesmo._evaluate_candidate and spea2._evaluate_entity, which were
    otherwise identical single-entity evaluation wrappers."""
    point, ran, invocations = evaluate_point(params, modified_params(params), out, reference_config, sniper, benchmarks, baseline, alpha, global_cache)
    if point is not None:
        print_evaluated_point(params, point, prefix=prefix)
    return point, ran, invocations


def compute_baseline(
    reference_config: str,
    sniper: Path,
    baseline_dir: Path,
    benchmarks: dict[str, list[str]],
) -> DesignPoint:
    """Runs every benchmark once with every parameter forced to DEFAULTS to
    get the reference-config baseline DesignPoint (asi=speedup=1.0)."""
    areas: list[float] = []
    powers: list[float] = []
    per_benchmark: dict[str, dict[str, float]] = {}
    for name, cmd in benchmarks.items():
        try:
            area, peak_power, time = run(reference_config, sniper, baseline_dir / name, cmd, DEFAULTS)
        except Exception as exc:
            raise RuntimeError(f"Baseline run failed ({name}): {exc}") from exc
        areas.append(area)
        powers.append(peak_power)
        per_benchmark[name] = {"time": time, "speedup": 1.0}

    return DesignPoint(
        params=dict(DEFAULTS), area=sum(areas) / len(areas), peak_power=sum(powers) / len(powers),
        time=sum(d["time"] for d in per_benchmark.values()) / len(per_benchmark),
        asi=1.0, speedup=1.0, modified_params=set(), output_path=baseline_dir,
        per_benchmark=per_benchmark,
    )
