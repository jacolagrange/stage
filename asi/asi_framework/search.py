from pathlib import Path
from typing import Any

from .models import DesignPoint
from .runner import run, run_test
from .config import (
    PARAM_SPACE,
    DEFAULT_ALPHA,
    DEFAULT_L1I_SIZE,
    DEFAULT_L1D_SIZE,
    DEFAULT_L2_SIZE,
    DEFAULT_L3_SIZE,
    DEFAULT_L1I_ASSOC,
    DEFAULT_L1D_ASSOC,
    DEFAULT_L2_ASSOC,
    DEFAULT_L3_ASSOC,
    DEFAULT_BRANCH_PREDICTOR_SIZE,
    DEFAULT_ROB_RS_ENTRIES,
    DEFAULT_ROB_OUTSTANDING_LOADS,
    DEFAULT_ROB_OUTSTANDING_STORES,
)


def calculate_asi(Ay: float, Ax: float, Py: float, Px: float, alpha: float) -> float:
    return (1 - alpha * (Ax / Ay)) / ((1 - alpha) * (Px / Py))


def dominates(a: DesignPoint, b: DesignPoint) -> bool:
    return (
        a.asi >= b.asi and a.speedup >= b.speedup
        and (a.asi > b.asi or a.speedup > b.speedup)
    )


def params_key(params: dict[str, Any]) -> frozenset:
    return frozenset(params.items())


def evaluate_point(
    params: dict[str, Any],
    modified_params: set[str],
    label: str,
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
) -> DesignPoint | None:
    """Evaluates a design point with strict global caching using dictionary-driven simulation overrides."""
    key = params_key(params)
    if key in global_cache:
        # Return a shallow copy with the current lineage's modified params tracking
        cached_point = global_cache[key]
        return DesignPoint(
            params=cached_point.params,
            area=cached_point.area,
            peak_power=cached_point.peak_power,
            time=cached_point.time,
            asi=cached_point.asi,
            speedup=cached_point.speedup,
            modified_params=modified_params
        )

    # Clean execution invocation directly feeding knobs into our dictionary-capable runner block
    try:
        area, peak_power, time = run_test(
            reference_config,
            sniper,
            outputdir / label,  # Give each run its own neat subdirectory to avoid overwriting files
            cmd,
            params,
        )
    except Exception as exc:
        print(f"  FAILED ({label}): {exc}")
        return None

    point = DesignPoint(
        params=params,
        area=area,
        peak_power=peak_power,
        time=time,
        modified_params=modified_params,
    )
    point.asi = calculate_asi(baseline.area, point.area, baseline.peak_power, point.peak_power, alpha)
    point.speedup = baseline.time / point.time

    global_cache[key] = point
    return point


def update_pareto_front(front: list[DesignPoint], points: list[DesignPoint]) -> list[DesignPoint]:
    all_points = front + points
    return [
        p for p in all_points
        if not any(dominates(other, p) for other in all_points if other is not p)
    ]


def explore_pareto_front_with_sensitivity(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 5,
) -> list[DesignPoint]:
    """
    Feasible Iterative Design Space Exploration optimized for physical Sniper simulation.
    Uses continuous freezing backoffs combined with global memoization.
    """
    print("Running baseline...")
    try:
        area, peak_power, time = run_test(
            reference_config,
            sniper,
            outputdir / "baseline",
            cmd,
            {},  # Empty dict for baseline
        )
    except Exception as exc:
        raise RuntimeError(f"Baseline run failed: {exc}") from exc

    baseline = DesignPoint(params={}, area=area, peak_power=peak_power, time=time, asi=1.0, speedup=1.0, modified_params=set())
    print(f"  Base Area={baseline.area:.4f}  Base Power={baseline.peak_power:.4f}  Base Time={baseline.time:.2f}")

    pareto_set: list[DesignPoint] = [baseline]
    newly_added: list[DesignPoint] = [baseline]

    # Global State Structures
    global_simulation_cache: dict[frozenset, DesignPoint] = {params_key({}): baseline}
    frozen_until: dict[str, int] = {}   
    freeze_count: dict[str, int] = {}   
    
    # Configuration constraints
    PROBATION_LENGTH = 2                
    SENSITIVITY_MIN_SAMPLES = 3   
    SENSITIVITY_THRESHOLD = 0.05  # Increased slightly for better stability

    sim_counter = 0

    for iteration in range(max_iterations):
        print(f"\n=== Iteration {iteration} ===")

        sensitivity: dict[str, list[float]] = {p: ([], []) for p in PARAM_SPACE}
        search_set: list[tuple[dict[str, Any], set[str], str, float, float]] = []

        # Generate unique microarchitectural modifications
        for parent in newly_added:
            for param, values in PARAM_SPACE.items():
                if param in parent.modified_params or frozen_until.get(param, -1) >= iteration:
                    continue
                for value in values:
                    # Filter: ignore if value is equal to default and wasn't manually set
                    default_value = {
                        "l1i_size": DEFAULT_L1I_SIZE, "l1d_size": DEFAULT_L1D_SIZE,
                        "l2_size":  DEFAULT_L2_SIZE,  "l3_size":  DEFAULT_L3_SIZE,
                        "l1i_assoc": DEFAULT_L1I_ASSOC, "l1d_assoc": DEFAULT_L1D_ASSOC,
                        "l2_assoc":  DEFAULT_L2_ASSOC,  "l3_assoc":  DEFAULT_L3_ASSOC,
                        "branch_predictor_size": DEFAULT_BRANCH_PREDICTOR_SIZE,
                        "rob_rs_entries": DEFAULT_ROB_RS_ENTRIES,
                    }.get(param)
                    
                    if value == default_value and param not in parent.params:
                        continue  
                    
                    child_params = {**parent.params, param: value}
                    child_modified = parent.modified_params | {param}
                    
                    if any(params_key(item[0]) == params_key(child_params) for item in search_set):
                        continue

                    search_set.append((child_params, child_modified, param, parent.asi, parent.speedup))

        if not search_set:
            print("  Design space converged or search set empty.")
            break

        evaluated: list[DesignPoint] = []
        for i, (params, modified, varied_param, parent_asi, parent_speedup) in enumerate(search_set):
            label = f"iter{iteration}_run{i}"
            
            is_cached = params_key(params) in global_simulation_cache
            
            point = evaluate_point(
                params=params,
                modified_params=modified,
                label=label,
                reference_config=reference_config,
                sniper=sniper,
                outputdir=outputdir,
                cmd=cmd,
                baseline=baseline,
                alpha=alpha,
                global_cache=global_simulation_cache
            )
            
            if point is not None:
                if not is_cached:
                    sim_counter += 1
                evaluated.append(point)
                
                # CRITICAL: Collect sensitivity data for every evaluation, cached or not
                sensitivity[varied_param][0].append(abs(point.asi - parent_asi))
                sensitivity[varied_param][1].append(abs(point.speedup - parent_speedup))
                
                status = " (Cache Hit)" if is_cached else " (Cache Miss)"
                print(f"  {label}{status}: ASI={point.asi:.4f}  Speedup={point.speedup:.4f}")

        # Perform Freezing Evaluation
        for param, (delta_asi, delta_speedup) in sensitivity.items():
            if frozen_until.get(param, -1) >= iteration or len(delta_asi) < SENSITIVITY_MIN_SAMPLES:
                continue
            
            max_impact_asi = max(delta_asi)
            max_impact_speedup = max(delta_speedup)
            
            if max_impact_asi < SENSITIVITY_THRESHOLD and max_impact_speedup < SENSITIVITY_THRESHOLD:
                freeze_count[param] = freeze_count.get(param, 0) + 1
                backoff = PROBATION_LENGTH * (2 ** (freeze_count[param] - 1))  
                frozen_until[param] = iteration + backoff
                print(f"  >>> [GLOBAL FREEZE] '{param}' locked until iteration {iteration + backoff}. "
                      f"Impact ASI: {max_impact_asi:.4f}")

        new_pareto = update_pareto_front(pareto_set, evaluated)
        old_keys = {params_key(p.params) for p in pareto_set}
        newly_added = [p for p in new_pareto if params_key(p.params) not in old_keys]
        pareto_set = new_pareto
        
        print(f"  Pareto Front Size: {len(pareto_set)} | New Nodes: {len(newly_added)}")

    print(f"\nExploration complete. Total UNIQUE physical Sniper runs: {sim_counter + 1}")
    return pareto_set