from pathlib import Path
from typing import Any

from .models import DesignPoint
from .config_builder import build_runtime_config
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


# y: the reference design
# x: the design under test
def calculate_asi(Ay: float, Ax: float, Py: float, Px: float, alpha: float) -> float:
    return (1 - alpha * (Ax / Ay)) / ((1 - alpha) * (Px / Py))


def dominates(a: DesignPoint, b: DesignPoint) -> bool:
    """Return True if a dominates b (better or equal on both objectives,
    strictly better on at least one)."""
    return (
        a.asi >= b.asi and a.speedup >= b.speedup
        and (a.asi > b.asi or a.speedup > b.speedup)
    )


def params_key(params: dict[str, Any]) -> frozenset:
    """Hashable representation of a config for deduplication."""
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
) -> DesignPoint | None:
    """Write config, run Sniper, compute ASI and speedup, return DesignPoint."""
    cfg_path = outputdir / f"{label}.cfg"
    cfg_path.write_text(build_runtime_config(reference_config, **params), encoding="utf-8")
    try:
        area, peak_power, time = run_test(str(cfg_path), sniper, outputdir, cmd)
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
    point.asi = calculate_asi(
        Ay=baseline.area,
        Ax=point.area,
        Py=baseline.peak_power,
        Px=point.peak_power,
        alpha=alpha,
    )
    point.speedup = baseline.time / point.time
    return point


def update_pareto_front(front: list[DesignPoint], points: list[DesignPoint]) -> list[DesignPoint]:
    """Recompute the Pareto front from front + candidates combined."""
    all_points = front + points
    return [
        p for p in all_points
        if not any(dominates(other, p) for other in all_points if other is not p)
    ]


def explore_pareto_front(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 3,
) -> list[DesignPoint]:
    """
    Iterative one-parameter-at-a-time exploration as described in Section V-B
    of the ASI paper.

    Three sets are maintained:
      - pareto_set:    current Pareto-optimal points
      - search_set:    configs pending evaluation this iteration
      - discarded_set: all evaluated non-Pareto configs (for deduplication)

    Returns the final Pareto front as a list of DesignPoints.
    """
    # 0. Evaluate baseline
    print("Running baseline...")
    baseline_cfg = outputdir / "baseline.cfg"
    baseline_cfg.write_text(build_runtime_config(reference_config), encoding="utf-8")
    try:
        area, peak_power, time = run(str(baseline_cfg), sniper, outputdir, cmd)
    except Exception as exc:
        raise RuntimeError(f"Baseline run failed: {exc}") from exc

    baseline = DesignPoint(
        params={},
        area=area,
        peak_power=peak_power,
        time=time,
        asi=1.0,
        speedup=1.0,
        modified_params=set(),
    )
    print(f"  Area={baseline.area:.4f} mm^2  "
          f"PeakPower={baseline.peak_power:.4f} W  "
          f"Time={baseline.time:.2f} ns")

    # 1. Initialise sets
    pareto_set: list[DesignPoint] = [baseline]
    discarded_set: set[frozenset] = set()
    # newly_added tracks which points were just added to pareto_set and should
    # generate children in the next iteration (starts as just the baseline)
    newly_added: list[DesignPoint] = [baseline]

    run_counter = 0

    for iteration in range(max_iterations):
        print(f"\n=== Iteration {iteration} ===")

        # 2. Generate search_set from newly added Pareto points
        search_set: list[tuple[dict[str, Any], set[str]]] = []  # (params, modified)

        for parent in newly_added:
            for param, values in PARAM_SPACE.items():
                # Only vary parameters not already modified in this lineage
                if param in parent.modified_params:
                    continue
                for value in values:
                    # Skip the default value for this param (no change)
                    default_value = {
                        "l1i_size": DEFAULT_L1I_SIZE,
                        "l1d_size": DEFAULT_L1D_SIZE,
                        "l2_size":  DEFAULT_L2_SIZE,
                        "l3_size":  DEFAULT_L3_SIZE,
                        "l1i_assoc": DEFAULT_L1I_ASSOC,
                        "l1d_assoc": DEFAULT_L1D_ASSOC,
                        "l2_assoc":  DEFAULT_L2_ASSOC,
                        "l3_assoc":  DEFAULT_L3_ASSOC,
                        "branch_predictor_size": DEFAULT_BRANCH_PREDICTOR_SIZE,
                    }.get(param)
                    if value == default_value and param not in parent.params:
                        continue  # this would be identical to the parent

                    child_params = {**parent.params, param: value}
                    child_modified = parent.modified_params | {param}
                    key = params_key(child_params)

                    # Skip if already evaluated (in Pareto or discarded)
                    already_in_pareto = any(
                        params_key(p.params) == key for p in pareto_set
                    )
                    if key in discarded_set or already_in_pareto:
                        continue

                    search_set.append((child_params, child_modified))

        if not search_set:
            print("  Search set is empty — converged.")
            break

        # 3. Evaluate search_set
        evaluated: list[DesignPoint] = []
        for i, (params, modified) in enumerate(search_set):
            label = f"iter{iteration}_run{i}"
            print(f"  {label}: {params}")
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
            )
            if point is not None:
                run_counter += 1
                print(f"    ASI={point.asi:.4f}  Speedup={point.speedup:.4f}  "
                      f"Area={point.area:.4f}  PeakPower={point.peak_power:.4f}")
                evaluated.append(point)

        # 4. Update Pareto front
        new_pareto = update_pareto_front(pareto_set, evaluated)

        # Determine which points are newly on the front
        old_keys = {params_key(p.params) for p in pareto_set}
        newly_added = [p for p in new_pareto if params_key(p.params) not in old_keys]

        # Everything evaluated but not on the new front goes to discarded
        new_pareto_keys = {params_key(p.params) for p in new_pareto}
        for p in evaluated:
            if params_key(p.params) not in new_pareto_keys:
                discarded_set.add(params_key(p.params))

        pareto_set = new_pareto
        print(f"  Pareto front size: {len(pareto_set)}  "
              f"({len(newly_added)} new points added)")

    print(f"\nExploration complete. Total Sniper runs: {run_counter + 1} (including baseline)")
    return pareto_set


def explore_pareto_front_with_sensitivity(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 3,
) -> list[DesignPoint]:
    """
    Iterative one-parameter-at-a-time exploration as described in Section V-B
    of the ASI paper.

    Three sets are maintained:
      - pareto_set:    current Pareto-optimal points
      - search_set:    configs pending evaluation this iteration
      - discarded_set: all evaluated non-Pareto configs (for deduplication)

    Returns the final Pareto front as a list of DesignPoints.
    """
    # 0. Evaluate baseline
    print("Running baseline...")
    baseline_cfg = outputdir / "baseline.cfg"
    baseline_cfg.write_text(build_runtime_config(reference_config), encoding="utf-8")
    try:
        area, peak_power, time = run(str(baseline_cfg), sniper, outputdir, cmd)
    except Exception as exc:
        raise RuntimeError(f"Baseline run failed: {exc}") from exc

    baseline = DesignPoint(
        params={},
        area=area,
        peak_power=peak_power,
        time=time,
        asi=1.0,
        speedup=1.0,
        modified_params=set(),
    )
    print(f"  Area={baseline.area:.4f} mm^2  "
          f"PeakPower={baseline.peak_power:.4f} W  "
          f"Time={baseline.time:.2f} ns")

    # 1. Initialise sets
    pareto_set: list[DesignPoint] = [baseline]
    discarded_set: set[frozenset] = set()
    # newly_added tracks which points were just added to pareto_set and should
    # generate children in the next iteration (starts as just the baseline)
    newly_added: list[DesignPoint] = [baseline]

    # Runtime sensitivity tracking: for each parameter, the ASI values observed
    # whenever that parameter was the one just varied to produce a child.
    sensitivity: dict[str, list[float]] = {p: [1.0] for p in PARAM_SPACE}
    # we add the baseline ASI=1.0 to each parameter's list, so that if a parameter is never varied, it will have a single ASI value of 1.0 and will not be frozen.
    frozen_until: dict[str, int] = {}   # param -> iteration index when it becomes active again
    freeze_count: dict[str, int] = {}   # param -> number of times it's been frozen so far
    PROBATION_LENGTH = 2                # # iterations a param stays frozen before re-testing
    SENSITIVITY_MIN_SAMPLES = 3   # need at least this many recent observations before judging
    SENSITIVITY_WINDOW = 3        # only look at the last N observations, not full history
    SENSITIVITY_THRESHOLD = 0.02  # ASI spread below this -> freeze (drop from future search)

    run_counter = 0

    for iteration in range(max_iterations):
        print(f"\n=== Iteration {iteration} ===")

        # 2. Generate search_set from newly added Pareto points
        search_set: list[tuple[dict[str, Any], set[str], str]] = []  # (params, modified, varied_param)

        for parent in newly_added:
            for param, values in PARAM_SPACE.items():
                # Only vary parameters not already modified in this lineage,
                # and skip parameters frozen due to low observed sensitivity
                if param in parent.modified_params or frozen_until.get(param, -1) >= iteration:
                    continue
                for value in values:
                    # Skip the default value for this param (no change)
                    default_value = {
                        "l1i_size": DEFAULT_L1I_SIZE,
                        "l1d_size": DEFAULT_L1D_SIZE,
                        "l2_size":  DEFAULT_L2_SIZE,
                        "l3_size":  DEFAULT_L3_SIZE,
                        "l1i_assoc": DEFAULT_L1I_ASSOC,
                        "l1d_assoc": DEFAULT_L1D_ASSOC,
                        "l2_assoc":  DEFAULT_L2_ASSOC,
                        "l3_assoc":  DEFAULT_L3_ASSOC,
                        "branch_predictor_size": DEFAULT_BRANCH_PREDICTOR_SIZE,
                        "rob_rs_entries": DEFAULT_ROB_RS_ENTRIES,
                        "rob_outstanding_loads": DEFAULT_ROB_OUTSTANDING_LOADS,
                        "rob_outstanding_stores": DEFAULT_ROB_OUTSTANDING_STORES,
                    }.get(param)
                    if value == default_value and param not in parent.params:
                        continue  # this would be identical to the parent

                    child_params = {**parent.params, param: value}
                    child_modified = parent.modified_params | {param}
                    key = params_key(child_params)

                    # Skip if already evaluated (in Pareto or discarded)
                    already_in_pareto = any(
                        params_key(p.params) == key for p in pareto_set
                    )
                    if key in discarded_set or already_in_pareto:
                        continue

                    search_set.append((child_params, child_modified, param))

        if not search_set:
            print("  Search set is empty — converged.")
            break

        # 3. Evaluate search_set
        evaluated: list[DesignPoint] = []
        for i, (params, modified, varied_param) in enumerate(search_set):
            label = f"iter{iteration}_run{i}"
            print(f"  {label}: {params}")
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
            )
            if point is not None:
                run_counter += 1
                print(f"    ASI={point.asi:.4f}  Speedup={point.speedup:.4f}  "
                      f"Area={point.area:.4f}  PeakPower={point.peak_power:.4f}")
                evaluated.append(point)
                sensitivity[varied_param].append(point.asi)

        # 3b. Freeze parameters that show negligible ASI impact so far
        for param, asi_values in sensitivity.items():
            if frozen_until.get(param, -1) >= iteration or len(asi_values) < SENSITIVITY_MIN_SAMPLES:
                continue
            # Only consider the last SENSITIVITY_WINDOW samples
            asi_values = asi_values[-SENSITIVITY_WINDOW:]
            spread = max(asi_values) - min(asi_values)
            if spread < SENSITIVITY_THRESHOLD:
                freeze_count[param] = freeze_count.get(param, 0) + 1
                backoff = PROBATION_LENGTH * (2 ** (freeze_count[param] - 1))  # exponential backoff for repeated freezes
                frozen_until[param] = iteration + backoff
                print(f"  Freezing '{param}' until iteration {iteration + backoff} "
                      f"(freeze #{freeze_count[param]}, recent ASI spread {spread:.4f})")

        # 4. Update Pareto front
        new_pareto = update_pareto_front(pareto_set, evaluated)

        # Determine which points are newly on the front
        old_keys = {params_key(p.params) for p in pareto_set}
        newly_added = [p for p in new_pareto if params_key(p.params) not in old_keys]

        # Everything evaluated but not on the new front goes to discarded
        new_pareto_keys = {params_key(p.params) for p in new_pareto}
        for p in evaluated:
            if params_key(p.params) not in new_pareto_keys:
                discarded_set.add(params_key(p.params))

        pareto_set = new_pareto
        print(f"  Pareto front size: {len(pareto_set)}  "
              f"({len(newly_added)} new points added)")

    print(f"\nExploration complete. Total Sniper runs: {run_counter + 1} (including baseline)")
    return pareto_set