import dataclasses
import json
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import DesignPoint
from .runner import run
from .config import PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS

# Short display names for parameter keys
_SHORT = {
    "l1i_size": "l1i", "l1d_size": "l1d", "l2_size": "l2", "l3_size": "l3",
    "l1i_assoc": "l1ia", "l1d_assoc": "l1da", "l2_assoc": "l2a", "l3_assoc": "l3a",
    "branch_predictor_size": "bp", "rob_rs_entries": "rob",
    "rob_outstanding_loads": "ld_out", "rob_outstanding_stores": "st_out",
}


def fmt_params(params: dict[str, Any]) -> str:
    if not params:
        return "baseline"
    return " ".join(f"{_SHORT.get(k, k)}={v}" for k, v in sorted(params.items()))


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
    output_path: Path,
    reference_config: str,
    sniper: Path,
    cmd: list[str],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
) -> DesignPoint | None:
    key = params_key(params)
    if key in global_cache:
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
        )

    try:
        area, peak_power, time = run(reference_config, sniper, output_path, cmd, params)
    except Exception as exc:
        print(f"    FAILED ({output_path.name}): {exc}")
        return None

    point = DesignPoint(
        params=params,
        area=area,
        peak_power=peak_power,
        time=time,
        modified_params=modified_params,
        output_path=output_path,
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


def _cleanup_dirs(dirs: set[Path]) -> int:
    count = 0
    for d in dirs:
        if d and d.exists():
            shutil.rmtree(d, ignore_errors=True)
            count += 1
    return count


def _state_path(outputdir: Path) -> Path:
    return outputdir / "search_state.json"


def _point_to_dict(p: DesignPoint) -> dict:
    d = dataclasses.asdict(p)
    d["modified_params"] = sorted(d["modified_params"])
    d["output_path"] = str(d["output_path"]) if d["output_path"] else None
    return d


def _point_from_dict(d: dict) -> DesignPoint:
    d = dict(d)
    d["modified_params"] = set(d["modified_params"])
    d["output_path"] = Path(d["output_path"]) if d["output_path"] else None
    return DesignPoint(**d)


@dataclass
class _SearchState:
    """Resumable snapshot of an in-progress search, checkpointed to JSON."""
    reference_config: str
    cmd: list[str]
    alpha: float
    iteration: int
    baseline: DesignPoint
    pareto_set: list[DesignPoint]
    newly_added: list[DesignPoint]
    global_cache: dict[frozenset, DesignPoint]
    frozen_until: dict[str, int]
    freeze_count: dict[str, int]
    sensitivity_history: dict[str, tuple[list[float], list[float]]]

    def matches(self, reference_config: str, cmd: list[str], alpha: float) -> bool:
        return (
            self.reference_config == str(reference_config)
            and self.cmd == cmd
            and self.alpha == alpha
        )

    def to_dict(self) -> dict:
        return {
            "reference_config": self.reference_config,
            "cmd": self.cmd,
            "alpha": self.alpha,
            "iteration": self.iteration,
            "baseline": _point_to_dict(self.baseline),
            "pareto_set": [_point_to_dict(p) for p in self.pareto_set],
            "newly_added": [_point_to_dict(p) for p in self.newly_added],
            "global_cache": [_point_to_dict(p) for p in self.global_cache.values()],
            "frozen_until": self.frozen_until,
            "freeze_count": self.freeze_count,
            "sensitivity_history": self.sensitivity_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_SearchState":
        sensitivity_history = {k: tuple(v) for k, v in d["sensitivity_history"].items()}
        for p in PARAM_SPACE:
            sensitivity_history.setdefault(p, ([], []))
        return cls(
            reference_config=d["reference_config"],
            cmd=d["cmd"],
            alpha=d["alpha"],
            iteration=d["iteration"],
            baseline=_point_from_dict(d["baseline"]),
            pareto_set=[_point_from_dict(x) for x in d["pareto_set"]],
            newly_added=[_point_from_dict(x) for x in d["newly_added"]],
            global_cache={params_key(x["params"]): _point_from_dict(x) for x in d["global_cache"]},
            frozen_until=d["frozen_until"],
            freeze_count=d["freeze_count"],
            sensitivity_history=sensitivity_history,
        )

    def save(self, outputdir: Path) -> None:
        path = _state_path(outputdir)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2))
        tmp.replace(path)

    @classmethod
    def load(cls, outputdir: Path) -> "_SearchState | None":
        path = _state_path(outputdir)
        if not path.exists():
            return None
        with open(path) as f:
            return cls.from_dict(json.load(f))


def explore_pareto_front_with_sensitivity(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 5,
) -> list[DesignPoint]:
    """
    Iterative Pareto-front exploration with sensitivity-based parameter freezing.
    """
    SENSITIVITY_MIN_SAMPLES = 3
    SENSITIVITY_THRESHOLD = 0.05
    SENSITIVITY_WINDOW = 6
    PROBATION_LENGTH = 2

    loaded = _SearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, cmd, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {_state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha — starting fresh.\n")

    if resumable:
        state = loaded
        print(f"Resuming search from iteration {state.iteration} "
              f"(found {_state_path(outputdir)})\n")
    else:
        # --- Baseline ---
        print("Running baseline...")
        baseline_dir = outputdir / "baseline"
        try:
            area, peak_power, time = run(reference_config, sniper, baseline_dir, cmd, {})
        except Exception as exc:
            raise RuntimeError(f"Baseline run failed: {exc}") from exc

        baseline = DesignPoint(
            params={}, area=area, peak_power=peak_power, time=time,
            asi=1.0, speedup=1.0, modified_params=set(), output_path=baseline_dir,
        )
        print(f"  Area={area:.2f} mm²  PeakPow={peak_power:.2f} W  Time={time:.0f} ns\n")

        state = _SearchState(
            reference_config=str(reference_config), cmd=cmd, alpha=alpha, iteration=0,
            baseline=baseline, pareto_set=[baseline], newly_added=[baseline],
            global_cache={params_key({}): baseline}, frozen_until={}, freeze_count={},
            sensitivity_history={p: ([], []) for p in PARAM_SPACE},
        )
        state.save(outputdir)

    baseline_dir = state.baseline.output_path
    all_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path} | {baseline_dir}

    for iteration in range(state.iteration, max_iterations):
        print(f"=== Iteration {iteration} ===")

        # Build search set from newly added Pareto points
        search_set: list[tuple] = []
        seen_keys: set[frozenset] = set()
        for parent in state.newly_added:
            for param, values in PARAM_SPACE.items():
                if param in parent.modified_params or state.frozen_until.get(param, -1) >= iteration:
                    continue
                for value in values:
                    if value == parent.params.get(param, DEFAULTS[param]):
                        continue
                    child_params = {**parent.params, param: value}
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
        for i, (params, modified, varied_param, parent_asi, parent_speedup) in enumerate(search_set):
            out = outputdir / f"iter{iteration}_run{i}"
            point = evaluate_point(
                params, modified, out, reference_config, sniper, cmd, state.baseline, alpha, state.global_cache,
            )
            if point is None:
                continue
            evaluated.append(point)
            label = sustainability_label(point.asi, point.speedup)
            print(
                f"  {fmt_params(params):<32}"
                f"  ASI={point.asi:7.4f}  S={point.speedup:6.4f}"
                f"  A={point.area:7.2f}  P={point.peak_power:6.2f}  [{label}]"
            )
            state.sensitivity_history[varied_param][0].append(
                abs(point.asi - parent_asi) / max(parent_asi, 1e-9)
            )
            state.sensitivity_history[varied_param][1].append(
                abs(point.speedup - parent_speedup) / max(parent_speedup, 1e-9)
            )

        # Sensitivity-based parameter freezing
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

        # Update Pareto front
        old_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path}
        state.pareto_set = update_pareto_front(state.pareto_set, evaluated)
        new_pareto_dirs = {p.output_path for p in state.pareto_set if p.output_path}
        all_pareto_dirs = new_pareto_dirs | {baseline_dir}

        # Delete output dirs of points that didn't make the Pareto front
        dropped = (old_pareto_dirs | {p.output_path for p in evaluated if p.output_path}) - all_pareto_dirs
        n = _cleanup_dirs(dropped)
        if n:
            print(f"  Deleted {n} non-Pareto output director{'y' if n == 1 else 'ies'}.")

        state.newly_added = [p for p in evaluated if p in state.pareto_set]

        print(f"\n  Pareto front after iteration {iteration} ({len(state.pareto_set)} point{'s' if len(state.pareto_set) != 1 else ''}):")
        print_pareto_table(state.pareto_set)
        print()

        state.iteration = iteration + 1
        state.save(outputdir)

    # Final cleanup: drop any surviving dirs no longer on the Pareto front
    n = _cleanup_dirs(all_pareto_dirs - {p.output_path for p in state.pareto_set if p.output_path} - {baseline_dir})
    if n:
        print(f"Final cleanup: removed {n} stale output director{'y' if n == 1 else 'ies'}.")

    return state.pareto_set
