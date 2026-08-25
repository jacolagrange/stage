from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .models import DesignPoint
from .runner import run
from .config import (
    PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS, DEFAULT_BRANCH_PREDICTOR_TYPE,
    BRANCH_PREDICTOR_PARAMS, CONDITIONAL_PARAMS, active_params,
)
from .plot import plot_pareto_front_on_asi, plot_pareto_fronts_on_asi, plot_hv_vs_simulations
from .state import (
    point_to_dict, point_from_dict, state_path, write_json_atomic, read_raw_state, cleanup_dirs,
)

_SHORT = {
    "l1i_size": "l1i", "l1d_size": "l1d", "l2_size": "l2", "l3_size": "l3",
    "l1i_assoc": "l1ia", "l1d_assoc": "l1da", "l2_assoc": "l2a", "l3_assoc": "l3a",
    "branch_predictor_type": "bpt", "branch_predictor_size": "bp",
    "num_history_registers": "nhist", "nn_batch_length": "nnbl", "nn_learning_rate": "nnlr",
    "rob_window_size": "robw", "rob_dispatch_width": "robd",
    "rob_commit_width": "robc",
    "rob_outstanding_loads": "ld_out", "rob_outstanding_stores": "st_out",
}


def fmt_params(params: dict[str, Any]) -> str:
    """Renders a point's (possibly sparse) params dict for terminal output.
    branch_predictor_type is always shown; every other key only when it
    deviates from its default."""
    if not params or all(params[p] == DEFAULTS[p] for p in params):
        return "baseline"
    bp_type = params.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    shown = {**params, "branch_predictor_type": bp_type}
    return " ".join(f"{_SHORT.get(k, k)}={v}" for k, v in sorted(shown.items()))


def sustainability_label(asi: float, speedup: float) -> str:
    tn = 1.0 / speedup if speedup > 0 else float("inf")
    upper = max(1.0, tn)
    lower = min(1.0, tn)
    if asi > upper:
        return "Strongly Sust."
    if asi < lower:
        return "Unsustainable"
    if abs(asi - 1.0) < 1e-9 and abs(speedup - 1.0) < 1e-9:
        return "Reference"
    if asi < 1.0:
        return "Weakly S-FW"
    return "Weakly S-FT"


def print_evaluated_point(params: dict[str, Any], point: DesignPoint, prefix: str = "") -> None:
    label = sustainability_label(point.asi, point.speedup)
    breakdown = ""
    if len(point.per_benchmark) > 1:
        breakdown = "  [" + ", ".join(
            f"{name}={data['speedup']:.2f}x" for name, data in point.per_benchmark.items()
        ) + "]"
    print(
        f"  {prefix}{fmt_params(params):<32}"
        f"  ASI={point.asi:7.4f}  S={point.speedup:6.4f}"
        f"  A={point.area:7.2f}  P={point.peak_power:6.2f}  [{label}]{breakdown}"
    )


def calculate_asi(Ay: float, Ax: float, Py: float, Px: float, alpha: float) -> float:
    return (1 - alpha * (Ax / Ay)) / ((1 - alpha) * (Px / Py))


def geomean(values: list[float]) -> float:
    product = 1.0
    for v in values:
        product *= v
    return product ** (1.0 / len(values))


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
    output_path: Path,
    reference_config: str,
    sniper: Path,
    benchmarks: dict[str, list[str]],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
) -> tuple[DesignPoint | None, bool, int]:
    """Returns (point, ran_sniper, sniper_invocations) -- see README's
    "evaluate_point()" bullet in Shared building blocks for their meaning."""
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
    (ASI=0, speedup=0). See README's "Pareto dominance & hypervolume" bullet."""
    if not front:
        return 0.0
    pts = sorted(front, key=lambda p: p.speedup)
    hv = 0.0
    prev_speedup = 0.0
    for p in pts:
        hv += max(0.0, p.asi) * max(0.0, p.speedup - prev_speedup)
        prev_speedup = p.speedup
    return hv


def print_pareto_table(pareto_set: list[DesignPoint]) -> None:
    col = 34
    header = f"  {'Params':<{col}} {'ASI':>8} {'Speedup':>8} {'Area':>8} {'PeakPow':>8}  Region"
    sep = "  " + "─" * (len(header) - 2)
    print(header)
    print(sep)
    for p in sorted(pareto_set, key=lambda x: x.speedup, reverse=True):
        label = sustainability_label(p.asi, p.speedup)
        print(
            f"  {fmt_params(p.params):<{col}} {p.asi:8.4f} {p.speedup:8.4f}"
            f" {p.area:8.2f} {p.peak_power:8.2f}  {label}"
        )


@dataclass
class GreedySearchState:
    """Resumable snapshot of an in-progress greedy/sensitivity search, checkpointed to JSON."""
    STRATEGY: ClassVar[str] = "greedy"

    reference_config: str
    benchmarks: dict[str, list[str]]
    alpha: float
    iteration: int
    baseline: DesignPoint
    pareto_set: list[DesignPoint]
    pareto_set_history: list[list[DesignPoint]]
    newly_added: list[DesignPoint]
    global_cache: dict[frozenset, DesignPoint]
    frozen_until: dict[str, int]
    freeze_count: dict[str, int]
    sensitivity_history: dict[str, tuple[list[float], list[float]]]
    sniper_runs: int
    sniper_invocations: int
    param_space: dict[str, list]
    hv_history: list[float]
    sim_history: list[int]
    pareto_size_history: list[int]

    def matches(self, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> bool:
        return (
            self.reference_config == str(reference_config)
            and self.benchmarks == benchmarks
            and self.alpha == alpha
            and self.param_space == PARAM_SPACE
        )

    def to_dict(self) -> dict:
        return {
            "strategy": self.STRATEGY,
            "reference_config": self.reference_config,
            "benchmarks": self.benchmarks,
            "alpha": self.alpha,
            "iteration": self.iteration,
            "baseline": point_to_dict(self.baseline),
            "pareto_set": [point_to_dict(p) for p in self.pareto_set],
            "pareto_set_history": [[point_to_dict(p) for p in front] for front in self.pareto_set_history],
            "newly_added": [point_to_dict(p) for p in self.newly_added],
            "global_cache": [point_to_dict(p) for p in self.global_cache.values()],
            "frozen_until": self.frozen_until,
            "freeze_count": self.freeze_count,
            "sensitivity_history": self.sensitivity_history,
            "sniper_runs": self.sniper_runs,
            "sniper_invocations": self.sniper_invocations,
            "param_space": self.param_space,
            "hv_history": self.hv_history,
            "sim_history": self.sim_history,
            "pareto_size_history": self.pareto_size_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "GreedySearchState":
        sensitivity_history = {k: tuple(v) for k, v in d["sensitivity_history"].items()}
        for p in PARAM_SPACE:
            sensitivity_history.setdefault(p, ([], []))
        return cls(
            reference_config=d["reference_config"],
            benchmarks=d["benchmarks"],
            alpha=d["alpha"],
            iteration=d["iteration"],
            baseline=point_from_dict(d["baseline"]),
            pareto_set=[point_from_dict(x) for x in d["pareto_set"]],
            pareto_set_history=[
                [point_from_dict(x) for x in front] for front in d.get("pareto_set_history", [])
            ],
            newly_added=[point_from_dict(x) for x in d["newly_added"]],
            global_cache={params_key(x["params"]): point_from_dict(x) for x in d["global_cache"]},
            frozen_until=d["frozen_until"],
            freeze_count=d["freeze_count"],
            sensitivity_history=sensitivity_history,
            sniper_runs=d.get("sniper_runs", 0),
            sniper_invocations=d.get("sniper_invocations", 0),
            param_space=d.get("param_space", {}),
            hv_history=d.get("hv_history", []),
            sim_history=d.get("sim_history", []),
            pareto_size_history=d.get("pareto_size_history", []),
        )

    def save(self, outputdir: Path) -> None:
        write_json_atomic(state_path(outputdir), self.to_dict())

    @classmethod
    def load(cls, outputdir: Path) -> "GreedySearchState | None":
        raw = read_raw_state(outputdir)
        if raw is None:
            return None
        found = raw.get("strategy", "greedy")
        if found != cls.STRATEGY:
            print(f"Saved search state at {state_path(outputdir)} was written by strategy "
                  f"'{found}', not '{cls.STRATEGY}' — starting fresh.\n")
            return None
        return cls.from_dict(raw)


def explore_pareto_front_with_sensitivity(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 5,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
) -> list[DesignPoint]:
    """Iterative Pareto-front exploration with sensitivity-based parameter
    freezing; see README's "Strategy: greedy" section. initial_cache seeds
    global_cache on a fresh start, ignored when resuming."""
    SENSITIVITY_MIN_SAMPLES = 3
    SENSITIVITY_THRESHOLD = 0.05
    SENSITIVITY_WINDOW = 6
    PROBATION_LENGTH = 2

    loaded = GreedySearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, benchmarks, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha/param-space — starting fresh.\n")

    if resumable:
        state = loaded
        print(f"Resuming search from iteration {state.iteration} "
              f"(found {state_path(outputdir)})\n")
    else:
        global_cache: dict[frozenset, DesignPoint] = dict(initial_cache) if initial_cache else {}
        baseline_key = params_key(DEFAULTS)
        if baseline_key in global_cache:
            baseline = global_cache[baseline_key]
            print(f"Using baseline from pre-evaluation screening cache ({len(global_cache)} cached point"
                  f"{'s' if len(global_cache) != 1 else ''}).")
        else:
            print("Running baseline...")
            baseline_dir = outputdir / "baseline"
            baseline = compute_baseline(reference_config, sniper, baseline_dir, benchmarks)
            global_cache[baseline_key] = baseline
        print(f"  Area={baseline.area:.2f} mm²  PeakPow={baseline.peak_power:.2f} W")
        for name, d in baseline.per_benchmark.items():
            print(f"    {name}: Time={d['time']:.0f} ns")
        print()

        state = GreedySearchState(
            reference_config=str(reference_config), benchmarks=benchmarks, alpha=alpha, iteration=0,
            baseline=baseline, pareto_set=[baseline], pareto_set_history=[[baseline]], newly_added=[baseline],
            global_cache=global_cache, frozen_until={}, freeze_count={},
            sensitivity_history={p: ([], []) for p in PARAM_SPACE},
            sniper_runs=0, sniper_invocations=0, param_space=PARAM_SPACE,
            hv_history=[hypervolume([baseline])], sim_history=[0], pareto_size_history=[1],
        )
        state.save(outputdir)

    baseline_dir = state.baseline.output_path
    all_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path} | {baseline_dir}

    for iteration in range(state.iteration, max_iterations):
        print(f"=== Iteration {iteration} ===")

        search_set: list[tuple] = []
        seen_keys: set[frozenset] = set()
        for parent in state.newly_added:
            parent_bp_type = parent.params.get(
                "branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE)
            )
            parent_active = active_params(PARAM_SPACE, parent_bp_type)
            for param, values in PARAM_SPACE.items():
                if param not in parent_active:
                    continue
                if param in parent.modified_params or state.frozen_until.get(param, -1) >= iteration:
                    continue
                for value in values:
                    if value == parent.params.get(param, DEFAULTS[param]):
                        continue
                    child_params = {**parent.params, param: value}
                    if param == "branch_predictor_type":
                        for stale in CONDITIONAL_PARAMS - set(BRANCH_PREDICTOR_PARAMS.get(value, ())):
                            child_params.pop(stale, None)
                    child_key = params_key(child_params)
                    if child_key in state.global_cache or child_key in seen_keys:
                        continue
                    seen_keys.add(child_key)
                    search_set.append((child_params, parent.modified_params | {param}, param, parent.asi, parent.speedup))

        if not search_set:
            print("  Search set empty — terminating early.\n")
            break

        print(f"  Evaluating {len(search_set)} configurations...")

        evaluated: list[DesignPoint] = []
        runs_this_iter = 0
        invocations_this_iter = 0
        for i, (params, modified, varied_param, parent_asi, parent_speedup) in enumerate(search_set):
            out = outputdir / f"iter{iteration}_run{i}"
            point, ran, invocations = evaluate_point(
                params, modified, out, reference_config, sniper, state.benchmarks, state.baseline, alpha, state.global_cache,
            )
            runs_this_iter += ran
            invocations_this_iter += invocations
            if point is None:
                continue
            evaluated.append(point)
            print_evaluated_point(params, point)
            state.sensitivity_history[varied_param][0].append(
                abs(point.asi - parent_asi) / max(parent_asi, 1e-9)
            )
            state.sensitivity_history[varied_param][1].append(
                abs(point.speedup - parent_speedup) / max(parent_speedup, 1e-9)
            )
        state.sniper_runs += runs_this_iter
        state.sniper_invocations += invocations_this_iter

        for param, (d_asi, d_spd) in state.sensitivity_history.items():
            if state.frozen_until.get(param, -1) >= iteration or len(d_asi) < SENSITIVITY_MIN_SAMPLES:
                continue
            recent_asi = d_asi[-SENSITIVITY_WINDOW:]
            recent_spd = d_spd[-SENSITIVITY_WINDOW:]
            if max(recent_asi) < SENSITIVITY_THRESHOLD and max(recent_spd) < SENSITIVITY_THRESHOLD:
                state.freeze_count[param] = state.freeze_count.get(param, 0) + 1
                backoff = PROBATION_LENGTH * (2 ** (state.freeze_count[param] - 1))
                state.frozen_until[param] = iteration + backoff
                print(f"  Freezing '{param}' for {backoff} iterations (backoff ×{state.freeze_count[param]})")

        old_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path}
        state.pareto_set = update_pareto_front(state.pareto_set, evaluated)
        state.pareto_set_history.append(list(state.pareto_set))
        new_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path}
        all_pareto_dirs = new_pareto_dirs | {baseline_dir}

        dropped = (old_pareto_dirs | {p.output_path for p in evaluated if p.output_path}) - all_pareto_dirs
        n = cleanup_dirs(dropped)
        if n:
            print(f"  Deleted {n} non-Pareto output director{'y' if n == 1 else 'ies'}.")

        state.newly_added = [p for p in evaluated if p in state.pareto_set]

        hv = hypervolume(state.pareto_set)
        state.hv_history.append(hv)
        state.sim_history.append(state.sniper_runs)
        state.pareto_size_history.append(len(state.pareto_set))

        print(f"\n  Pareto front after iteration {iteration} "
              f"({len(state.pareto_set)} point{'s' if len(state.pareto_set) != 1 else ''}, HV={hv:.4f}):")
        print_pareto_table(state.pareto_set)
        print(f"  Ran sniper {runs_this_iter} time{'s' if runs_this_iter != 1 else ''} this iteration "
              f"({state.sniper_runs} total).")
        print()

        state.iteration = iteration + 1
        state.save(outputdir)

    n = cleanup_dirs(all_pareto_dirs - {p.output_path for p in state.pareto_set if p.output_path} - {baseline_dir})
    if n:
        print(f"Final cleanup: removed {n} stale output director{'y' if n == 1 else 'ies'}.")

    print(f"Configurations evaluated: {state.sniper_runs}")
    print(f"Total sniper invocations: {state.sniper_invocations}")
    print(f"Final hypervolume: {hypervolume(state.pareto_set):.4f}\n")

    plot_pareto_fronts_on_asi(
        state.pareto_set_history, title="ASI Pareto Fronts by Iteration",
        sequence_label="Iteration",
        save_path=outputdir / "pareto_history.png", show=False,
    )
    plot_pareto_front_on_asi(
        state.pareto_set, title="Final ASI Pareto Front",
        save_path=outputdir / "pareto_final.png", show=False,
    )
    plot_hv_vs_simulations(
        state.sim_history, state.hv_history, state.pareto_size_history,
        title="Hypervolume & Pareto Front Size vs. Simulations (greedy)",
        save_path=outputdir / "hv_vs_sims.png", show=False,
    )

    return state.pareto_set
