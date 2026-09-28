import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Iterator

from .models import DesignPoint
from .config import (
    PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS, DEFAULT_BRANCH_PREDICTOR_TYPE,
    BRANCH_PREDICTOR_PARAMS, CONDITIONAL_PARAMS, active_params,
)
from .metrics import params_key, hypervolume, update_pareto_front
from .evaluation import compute_baseline, evaluate_point
from .display import print_evaluated_point, print_pareto_table
from .search_ops import full_factorial_configs, full_factorial_size, modified_params
from . import titan_batch
from .plot import plot_pareto_front_on_asi, plot_pareto_fronts_on_asi, plot_hv_vs_simulations
from .state import SearchStateBase, point_to_dict, point_from_dict, state_path, cleanup_dirs, format_elapsed


@dataclass
class GreedySearchState(SearchStateBase):
    """Resumable snapshot of an in-progress greedy/sensitivity search, checkpointed to JSON."""
    STRATEGY: ClassVar[str] = "greedy"

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
    hv_history: list[float]
    sim_history: list[int]
    pareto_size_history: list[int]

    @staticmethod
    def _current_param_space() -> dict[str, list]:
        return PARAM_SPACE

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


def explore_pareto_front_with_sensitivity(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 5,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
    start_time: float | None = None,
    prior_elapsed: float = 0.0,
) -> list[DesignPoint]:
    """Iterative Pareto-front exploration with sensitivity-based parameter
    freezing. Initial_cache seeds
    global_cache on a fresh start, ignored when resuming."""
    if start_time is None:
        start_time = time.monotonic()
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
        print(f"  Elapsed: {format_elapsed(prior_elapsed + (time.monotonic() - start_time))}")
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


@dataclass
class _SweepChunk:
    """One in-flight or finished-but-uncommitted chunk of a titan
    full-factorial sweep. start_index/count are this chunk's slice of the
    deterministic full_factorial_configs() enumeration (baseline excluded --
    see explore_full_factorial()), used to commit the persisted cursor
    (FullFactorialSearchState.next_commit_index) strictly in enumeration
    order even though chunks may finish out of order."""
    chunk: titan_batch.PendingChunk
    start_index: int
    count: int
    results: list[tuple[DesignPoint | None, bool, int]] | None = None


@dataclass
class FullFactorialSearchState(SearchStateBase):
    """Resumable snapshot of an in-progress titan full-factorial sweep,
    checkpointed to JSON. Unlike the other strategies' states, this one
    deliberately does *not* keep a global_cache of every evaluated point (it
    would grow as large as the whole sweep) -- only the live Pareto front is
    kept, plus next_commit_index/chunks_submitted, two integers cheap enough
    to checkpoint after every chunk. See explore_full_factorial()."""
    STRATEGY: ClassVar[str] = "full_factorial"

    baseline: DesignPoint
    pareto_set: list[DesignPoint]
    pareto_set_history: list[list[DesignPoint]]
    next_commit_index: int
    chunks_submitted: int
    total_configs: int
    sniper_runs: int
    sniper_invocations: int
    hv_history: list[float]
    sim_history: list[int]
    pareto_size_history: list[int]

    @staticmethod
    def _current_param_space() -> dict[str, list]:
        return PARAM_SPACE

    def to_dict(self) -> dict:
        return {
            "strategy": self.STRATEGY,
            "reference_config": self.reference_config,
            "benchmarks": self.benchmarks,
            "alpha": self.alpha,
            "baseline": point_to_dict(self.baseline),
            "pareto_set": [point_to_dict(p) for p in self.pareto_set],
            "pareto_set_history": [[point_to_dict(p) for p in front] for front in self.pareto_set_history],
            "next_commit_index": self.next_commit_index,
            "chunks_submitted": self.chunks_submitted,
            "total_configs": self.total_configs,
            "sniper_runs": self.sniper_runs,
            "sniper_invocations": self.sniper_invocations,
            "param_space": self.param_space,
            "hv_history": self.hv_history,
            "sim_history": self.sim_history,
            "pareto_size_history": self.pareto_size_history,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FullFactorialSearchState":
        return cls(
            reference_config=d["reference_config"],
            benchmarks=d["benchmarks"],
            alpha=d["alpha"],
            baseline=point_from_dict(d["baseline"]),
            pareto_set=[point_from_dict(x) for x in d["pareto_set"]],
            pareto_set_history=[
                [point_from_dict(x) for x in front] for front in d.get("pareto_set_history", [])
            ],
            next_commit_index=d["next_commit_index"],
            chunks_submitted=d["chunks_submitted"],
            total_configs=d["total_configs"],
            sniper_runs=d.get("sniper_runs", 0),
            sniper_invocations=d.get("sniper_invocations", 0),
            param_space=d.get("param_space", {}),
            hv_history=d.get("hv_history", []),
            sim_history=d.get("sim_history", []),
            pareto_size_history=d.get("pareto_size_history", []),
        )


def _absorb_full_factorial_results(
    state: FullFactorialSearchState,
    results: list[tuple[DesignPoint | None, bool, int]],
) -> None:
    """Folds one chunk's (or one local point's) results into the live
    Pareto front and running totals. update_pareto_front() drops anything
    just-dominated in the same call -- a point discarded here is gone for
    good, never reconsidered, since domination between two fixed points
    never depends on anything else evaluated later."""
    points = []
    for point, ran, invocations in results:
        state.sniper_runs += ran
        state.sniper_invocations += invocations
        if point is not None:
            points.append(point)
            print_evaluated_point(point.params, point)
    if not points:
        return
    state.pareto_set = update_pareto_front(state.pareto_set, points)
    state.pareto_set_history.append(list(state.pareto_set))
    state.hv_history.append(hypervolume(state.pareto_set))
    state.sim_history.append(state.sniper_runs)
    state.pareto_size_history.append(len(state.pareto_set))


def _print_full_factorial_progress(state: FullFactorialSearchState, start_time: float, prior_elapsed: float) -> None:
    done = 1 + state.next_commit_index
    print(f"\n  Progress: {done}/{state.total_configs} configurations "
          f"— Pareto front ({len(state.pareto_set)} point{'s' if len(state.pareto_set) != 1 else ''}, "
          f"HV={state.hv_history[-1]:.4f}):")
    print_pareto_table(state.pareto_set)
    print(f"  Ran sniper {state.sniper_runs} time{'s' if state.sniper_runs != 1 else ''} total.")
    print(f"  Elapsed: {format_elapsed(prior_elapsed + (time.monotonic() - start_time))}")
    print()


def _run_local_full_factorial(
    state: FullFactorialSearchState,
    remaining_configs: Iterator[dict[str, Any]],
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    start_time: float,
    prior_elapsed: float,
) -> None:
    """Sequential local fallback (no --titan): one Sniper run at a time.
    Meant for small/pruned param spaces or testing -- exhaustively sweeping
    an unpruned PARAM_SPACE this way would take far too long."""
    baseline_dir = state.baseline.output_path
    checkpoint_every = 20

    for params in remaining_configs:
        out = outputdir / f"cfg{state.next_commit_index}"
        point, ran, invocations = evaluate_point(
            params, modified_params(params), out, reference_config, sniper,
            state.benchmarks, state.baseline, state.alpha, {},
        )
        old_dirs = {p.output_path for p in state.pareto_set if p.output_path}
        _absorb_full_factorial_results(state, [(point, ran, invocations)])
        new_dirs = {p.output_path for p in state.pareto_set if p.output_path} | {baseline_dir}
        cleanup_dirs((old_dirs | {out}) - new_dirs)

        state.next_commit_index += 1
        if state.next_commit_index % checkpoint_every == 0:
            state.save(outputdir)
            _print_full_factorial_progress(state, start_time, prior_elapsed)

    state.save(outputdir)


def _run_titan_full_factorial(
    state: FullFactorialSearchState,
    remaining_configs: Iterator[dict[str, Any]],
    reference_config: str,
    outputdir: Path,
    titan_config: dict[str, Any],
    chunk_size: int,
    max_concurrent: int,
    start_time: float,
    prior_elapsed: float,
) -> None:
    """Rolling-window submission: up to max_concurrent chunks of
    chunk_size configs each are in flight on Titan at any time. Each chunk
    is its own Slurm array job (titan_batch.submit_chunk), so it already
    gets scheduled across every free compute node on its own; the instant
    ANY chunk finishes, this refills that window slot with the next chunk
    immediately -- the cluster is never left idle waiting for one job's
    poll cycle. next_commit_index only advances in strict enumeration
    order (see _SweepChunk), so chunks that finish out of order are held in
    pending_commit until it's their turn -- that's what keeps a
    crash-and-resume from silently losing or re-submitting completed work."""
    dispatched_index = state.next_commit_index

    def make_chunk_entities(start_index: int) -> tuple[list[tuple[dict, Path, set]], int]:
        entities = []
        idx = start_index
        while len(entities) < chunk_size:
            try:
                params = next(remaining_configs)
            except StopIteration:
                break
            entities.append((params, outputdir / f"cfg{idx}", modified_params(params)))
            idx += 1
        return entities, idx

    def submit_next() -> _SweepChunk | None:
        nonlocal dispatched_index
        start_index = dispatched_index
        entities, end_index = make_chunk_entities(start_index)
        if not entities:
            return None
        dispatched_index = end_index
        state.chunks_submitted += 1
        pending = titan_batch.submit_chunk(
            entities, reference_config,
            titan_controller_dir=titan_config["titan_controller_dir"],
            benchmark_json_path=titan_config["benchmark_json_path"],
            host_destination_path=titan_config["host_destination_path"] / f"chunk{state.chunks_submitted}",
            sniper_mount=titan_config["sniper_mount"],
            benchmarks_mount=titan_config["benchmarks_mount"],
            job_name=f"asi_full_chunk{state.chunks_submitted}",
        )
        print(f"  Submitted chunk {state.chunks_submitted} ({len(entities)} configs, "
              f"cfg{start_index}..cfg{end_index - 1})...")
        return _SweepChunk(chunk=pending, start_index=start_index, count=len(entities))

    in_flight: list[_SweepChunk] = []
    pending_commit: list[_SweepChunk] = []

    for _ in range(max_concurrent):
        sc = submit_next()
        if sc is None:
            break
        in_flight.append(sc)

    while in_flight or pending_commit:
        progressed = False
        if in_flight:
            list_out = titan_batch.list_jobs(titan_config["titan_controller_dir"])
            still_running = []
            for sc in in_flight:
                if sc.chunk.job_ids and any(jid in list_out for jid in sc.chunk.job_ids):
                    still_running.append(sc)
                    continue
                results = titan_batch.try_collect_chunk(
                    sc.chunk, reference_config, state.benchmarks, state.baseline, state.alpha,
                    titan_controller_dir=titan_config["titan_controller_dir"],
                )
                if results is None:
                    still_running.append(sc)
                    continue
                progressed = True
                sc.results = results
                pending_commit.append(sc)
                next_sc = submit_next()
                if next_sc is not None:
                    still_running.append(next_sc)
            in_flight = still_running

        pending_commit.sort(key=lambda c: c.start_index)
        while pending_commit and pending_commit[0].start_index == state.next_commit_index:
            sc = pending_commit.pop(0)
            _absorb_full_factorial_results(state, sc.results)
            state.next_commit_index = sc.start_index + sc.count
            state.save(outputdir)
            _print_full_factorial_progress(state, start_time, prior_elapsed)
            progressed = True

        if not progressed and (in_flight or pending_commit):
            time.sleep(titan_config["poll_interval"])


def explore_full_factorial(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
    titan: bool = False,
    titan_benchmark_json: str | None = None,
    titan_dir: str | None = None,
    titan_host_dir: str | None = None,
    titan_sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    titan_benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    titan_poll_interval: float = 30.0,
    titan_chunk_size: int = 50,
    titan_max_concurrent: int = 3,
    start_time: float | None = None,
    prior_elapsed: float = 0.0,
) -> list[DesignPoint]:
    """Exhaustive full-factorial search: evaluates every configuration in
    PARAM_SPACE (search_ops.full_factorial_configs) exactly once -- meant
    for a small or pre-evaluation-pruned space (see --preeval-samples),
    not the full unpruned grid, which is far too large to run exhaustively.

    With titan=True, the sweep is submitted as a rolling window of Titan job
    chunks that keeps titan_max_concurrent chunks in flight at all times,
    immediately refilling any chunk's slot the moment it finishes (see
    _run_titan_full_factorial) -- each chunk's own Slurm array job already
    spreads its tasks across every free compute node, so this keeps the
    cluster continuously fed instead of waiting for one job to fully
    complete before submitting the next. Without --titan, configurations
    are evaluated one at a time locally.

    The Pareto front is maintained live: every newly evaluated point is
    folded into it and then discarded (see metrics.update_pareto_front) --
    a point once dominated can never re-enter the front no matter what's
    evaluated afterwards, so nothing is lost by not keeping it around. This
    keeps memory bounded by the front's own size instead of growing with
    the number of configurations evaluated, which matters here since a
    full-factorial sweep can run into the thousands or more.
    """
    if start_time is None:
        start_time = time.monotonic()
    titan_config = titan_batch.build_config(
        titan, outputdir, titan_benchmark_json, titan_dir, titan_host_dir,
        titan_sniper_mount, titan_benchmarks_mount, titan_poll_interval,
    )

    loaded = FullFactorialSearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, benchmarks, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha/param-space — starting fresh.\n")

    if resumable:
        state = loaded
        print(f"Resuming titan full-factorial search from config "
              f"{1 + state.next_commit_index}/{state.total_configs} "
              f"(found {state_path(outputdir)})\n")
    else:
        global_cache: dict[frozenset, DesignPoint] = dict(initial_cache) if initial_cache else {}
        baseline_key = params_key(DEFAULTS)
        if baseline_key in global_cache:
            baseline = global_cache[baseline_key]
            print("Using baseline from pre-evaluation screening cache.")
        else:
            print("Running baseline...")
            baseline_dir = outputdir / "baseline"
            if titan_config is not None:
                baseline = titan_batch.evaluate_baseline(
                    reference_config, benchmarks, baseline_dir,
                    titan_controller_dir=titan_config["titan_controller_dir"],
                    benchmark_json_path=titan_config["benchmark_json_path"],
                    host_destination_path=titan_config["host_destination_path"] / "baseline",
                    sniper_mount=titan_config["sniper_mount"],
                    benchmarks_mount=titan_config["benchmarks_mount"],
                    poll_interval=titan_config["poll_interval"],
                )
            else:
                baseline = compute_baseline(reference_config, sniper, baseline_dir, benchmarks)
        print(f"  Area={baseline.area:.2f} mm²  PeakPow={baseline.peak_power:.2f} W")
        for name, d in baseline.per_benchmark.items():
            print(f"    {name}: Time={d['time']:.0f} ns")

        total_configs = full_factorial_size(PARAM_SPACE)
        print(f"\n  Full-factorial sweep: {total_configs} configurations "
              f"(baseline is config 1, already evaluated above).\n")

        state = FullFactorialSearchState(
            reference_config=str(reference_config), benchmarks=benchmarks, alpha=alpha,
            baseline=baseline, pareto_set=[baseline], pareto_set_history=[[baseline]],
            next_commit_index=0, chunks_submitted=0, total_configs=total_configs,
            sniper_runs=0, sniper_invocations=0,
            hv_history=[hypervolume([baseline])], sim_history=[0], pareto_size_history=[1],
            param_space=PARAM_SPACE,
        )
        state.save(outputdir)

    all_configs = full_factorial_configs(PARAM_SPACE)
    first = next(all_configs)
    first_bp_type = first.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    expected_first = {p: DEFAULTS[p] for p in active_params(PARAM_SPACE, first_bp_type)}
    if first != expected_first:
        raise RuntimeError(
            "full_factorial_configs()'s first configuration is no longer the all-defaults "
            "baseline -- explore_full_factorial() relies on that ordering guarantee to skip "
            "resubmitting the baseline and to keep next_commit_index simple."
        )
    for _ in range(state.next_commit_index):
        next(all_configs)

    if titan_config is not None:
        _run_titan_full_factorial(
            state, all_configs, reference_config, outputdir, titan_config,
            titan_chunk_size, titan_max_concurrent, start_time, prior_elapsed,
        )
    else:
        _run_local_full_factorial(
            state, all_configs, reference_config, sniper, outputdir, start_time, prior_elapsed,
        )

    print(f"Configurations evaluated: {state.sniper_runs}")
    print(f"Total sniper invocations: {state.sniper_invocations}")
    print(f"Final hypervolume: {hypervolume(state.pareto_set):.4f}\n")

    plot_pareto_fronts_on_asi(
        state.pareto_set_history, title="ASI Pareto Fronts by Chunk",
        sequence_label="Chunk",
        save_path=outputdir / "pareto_history.png", show=False,
    )
    plot_pareto_front_on_asi(
        state.pareto_set, title="Final ASI Pareto Front",
        save_path=outputdir / "pareto_final.png", show=False,
    )
    plot_hv_vs_simulations(
        state.sim_history, state.hv_history, state.pareto_size_history,
        title="Hypervolume & Pareto Front Size vs. Simulations (titan full-factorial)",
        save_path=outputdir / "hv_vs_sims.png", show=False,
    )

    return state.pareto_set
