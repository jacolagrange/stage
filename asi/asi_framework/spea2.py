"""COLE-style multi-objective evolutionary (SPEA2) search strategy. See
README's "Strategy: spea2" section."""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .models import DesignPoint
from .config import (
    PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS, DEFAULT_BRANCH_PREDICTOR_TYPE,
    BRANCH_PREDICTOR_PARAMS, CONDITIONAL_PARAMS, active_params,
)
from .greedy import (
    evaluate_point, dominates, update_pareto_front, print_pareto_table,
    print_evaluated_point, params_key, compute_baseline, hypervolume,
)
from . import titan_batch
from .plot import plot_pareto_front_on_asi, plot_pareto_fronts_on_asi, plot_hv_vs_simulations
from .state import (
    point_to_dict, point_from_dict, state_path, write_json_atomic, read_raw_state,
    cleanup_dirs, rng_state_to_json, rng_state_from_json,
)


def _random_entity(rng: random.Random) -> dict[str, Any]:
    """A fully-specified configuration: one value per PARAM_SPACE parameter
    relevant to the randomly chosen branch predictor type."""
    entity = {
        param: rng.choice(values)
        for param, values in PARAM_SPACE.items()
        if param not in CONDITIONAL_PARAMS
    }
    for param in BRANCH_PREDICTOR_PARAMS.get(entity["branch_predictor_type"], ()):
        entity[param] = rng.choice(PARAM_SPACE[param])
    return entity


def _mutate(entity: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Randomly reassign a single active parameter to a new candidate value."""
    child = dict(entity)
    bp_type = entity.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    param = rng.choice(sorted(active_params(PARAM_SPACE, bp_type)))
    child[param] = rng.choice(PARAM_SPACE[param])
    if param == "branch_predictor_type":
        for stale in CONDITIONAL_PARAMS - set(BRANCH_PREDICTOR_PARAMS.get(child[param], ())):
            child.pop(stale, None)
        for new_param in BRANCH_PREDICTOR_PARAMS.get(child[param], ()):
            child[new_param] = rng.choice(PARAM_SPACE[new_param])
    return child


def _crossover(a: dict[str, Any], b: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Uniform crossover: each always-relevant parameter independently comes
    from parent a or b; the resulting type's own knobs are filled in from
    whichever parent(s) used that type, or drawn fresh otherwise."""
    child = {
        param: (a.get(param, DEFAULTS[param]) if rng.random() < 0.5 else b.get(param, DEFAULTS[param]))
        for param in PARAM_SPACE if param not in CONDITIONAL_PARAMS
    }
    bp_type = child["branch_predictor_type"]
    a_type = a.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    b_type = b.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    for param in BRANCH_PREDICTOR_PARAMS.get(bp_type, ()):
        candidates = [d[param] for d, t in ((a, a_type), (b, b_type)) if t == bp_type and param in d]
        child[param] = rng.choice(candidates) if candidates else rng.choice(PARAM_SPACE[param])
    return child


def _modified_params(params: dict[str, Any]) -> set[str]:
    return {p for p, v in params.items() if v != DEFAULTS[p]}


def _normalized_objectives(points: list[DesignPoint]) -> dict[int, tuple[float, float]]:
    """id(point) -> (asi, speedup) min-max normalized to [0, 1] across `points`,
    so density/distance isn't skewed by ASI and speedup living on different scales."""
    asis = [p.asi for p in points]
    speedups = [p.speedup for p in points]
    a_lo, a_hi = min(asis), max(asis)
    s_lo, s_hi = min(speedups), max(speedups)
    a_span = (a_hi - a_lo) or 1.0
    s_span = (s_hi - s_lo) or 1.0
    return {id(p): ((p.asi - a_lo) / a_span, (p.speedup - s_lo) / s_span) for p in points}


def _distance(obj: dict[int, tuple[float, float]], a: DesignPoint, b: DesignPoint) -> float:
    ax, ay = obj[id(a)]
    bx, by = obj[id(b)]
    return ((ax - bx) ** 2 + (ay - by) ** 2) ** 0.5


def _spea2_fitness(pool: list[DesignPoint]) -> tuple[dict[int, float], dict[int, int]]:
    """SPEA2 fitness assignment (Zitzler et al. 2001): raw fitness (dominance
    strength) plus a density term (distance to the k-th nearest neighbor) as
    a tiebreaker. Lower is better; raw==0 means non-dominated within `pool`.
    Returns (fitness, raw) keyed by id(point)."""
    n = len(pool)
    strength = {id(p): sum(1 for q in pool if dominates(p, q)) for p in pool}
    raw = {id(p): sum(strength[id(q)] for q in pool if dominates(q, p)) for p in pool}

    obj = _normalized_objectives(pool)
    k = max(1, int(n ** 0.5))
    density = {}
    for p in pool:
        dists = sorted(_distance(obj, p, q) for q in pool if q is not p)
        sigma_k = dists[k - 1] if len(dists) >= k else (dists[-1] if dists else 0.0)
        density[id(p)] = 1.0 / (sigma_k + 2.0)

    fitness = {id(p): raw[id(p)] + density[id(p)] for p in pool}
    return fitness, raw


def _truncate(nondominated: list[DesignPoint], size: int) -> list[DesignPoint]:
    """Repeatedly drop the most crowded point until size non-dominated
    points remain, so survivors spread out across the objective range."""
    pool = list(nondominated)
    while len(pool) > size:
        obj = _normalized_objectives(pool)
        nearest = {id(p): sorted(_distance(obj, p, q) for q in pool if q is not p) for p in pool}
        pool.sort(key=lambda p: (nearest[id(p)], -len(p.modified_params)))
        pool.pop(0)
    return pool


def _environmental_selection(
    population: list[DesignPoint], archive: list[DesignPoint], archive_size: int,
) -> list[DesignPoint]:
    """Build the next archive from the current population + current archive:
    keep all non-dominated points; if there are too few, pad with the
    best-fitness dominated ones; if too many, truncate by crowding."""
    combined = population + archive
    fitness, raw = _spea2_fitness(combined)
    nondominated = [p for p in combined if raw[id(p)] == 0]

    if len(nondominated) == archive_size:
        return nondominated
    if len(nondominated) < archive_size:
        rest = sorted(
            (p for p in combined if raw[id(p)] > 0),
            key=lambda p: (fitness[id(p)], len(p.modified_params)),
        )
        return nondominated + rest[: archive_size - len(nondominated)]
    return _truncate(nondominated, archive_size)


def _binary_tournament(archive: list[DesignPoint], fitness: dict[int, float], rng: random.Random) -> DesignPoint:
    if len(archive) < 2:
        return archive[0]
    a, b = rng.sample(archive, 2)
    return a if fitness[id(a)] <= fitness[id(b)] else b


def _make_mating_pool(
    pop_idx: int, archives: list[list[DesignPoint]], pool_size: int, p_migration: float, rng: random.Random,
) -> list[DesignPoint]:
    own_archive = archives[pop_idx]
    fitness, _ = _spea2_fitness(own_archive)
    others = [i for i in range(len(archives)) if i != pop_idx and archives[i]]
    pool = []
    for _ in range(pool_size):
        if others and rng.random() < p_migration:
            pool.append(rng.choice(archives[rng.choice(others)]))
        else:
            pool.append(_binary_tournament(own_archive, fitness, rng))
    return pool


def _has_converged(hv_history: list[float], patience: int, rel_tol: float = 1e-3) -> bool:
    """True if hypervolume hasn't meaningfully improved in the last patience generations."""
    if len(hv_history) <= patience:
        return False
    best_before = max(hv_history[:-patience])
    recent_best = max(hv_history[-patience:])
    return recent_best <= best_before * (1 + rel_tol)


@dataclass
class Spea2SearchState:
    """Resumable snapshot of an in-progress SPEA2/COLE search, checkpointed to JSON."""
    STRATEGY: ClassVar[str] = "spea2"

    reference_config: str
    benchmarks: dict[str, list[str]]
    alpha: float
    generation: int
    baseline: DesignPoint
    global_cache: dict[frozenset, DesignPoint]
    populations: list[list[DesignPoint]]
    archives: list[list[DesignPoint]]
    pareto_front: list[DesignPoint]
    pareto_front_history: list[list[DesignPoint]]
    hv_history: list[float]
    sim_history: list[int]
    pareto_size_history: list[int]
    rng_state: list
    sniper_runs: int
    sniper_invocations: int
    param_space: dict[str, list]

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
            "generation": self.generation,
            "baseline": point_to_dict(self.baseline),
            "global_cache": [point_to_dict(p) for p in self.global_cache.values()],
            "populations": [[point_to_dict(p) for p in pop] for pop in self.populations],
            "archives": [[point_to_dict(p) for p in arc] for arc in self.archives],
            "pareto_front": [point_to_dict(p) for p in self.pareto_front],
            "pareto_front_history": [[point_to_dict(p) for p in front] for front in self.pareto_front_history],
            "hv_history": self.hv_history,
            "sim_history": self.sim_history,
            "pareto_size_history": self.pareto_size_history,
            "rng_state": self.rng_state,
            "sniper_runs": self.sniper_runs,
            "sniper_invocations": self.sniper_invocations,
            "param_space": self.param_space,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Spea2SearchState":
        return cls(
            reference_config=d["reference_config"],
            benchmarks=d["benchmarks"],
            alpha=d["alpha"],
            generation=d["generation"],
            baseline=point_from_dict(d["baseline"]),
            global_cache={params_key(x["params"]): point_from_dict(x) for x in d["global_cache"]},
            populations=[[point_from_dict(x) for x in pop] for pop in d["populations"]],
            archives=[[point_from_dict(x) for x in arc] for arc in d["archives"]],
            pareto_front=[point_from_dict(x) for x in d["pareto_front"]],
            pareto_front_history=[
                [point_from_dict(x) for x in front] for front in d.get("pareto_front_history", [])
            ],
            hv_history=d["hv_history"],
            sim_history=d.get("sim_history", []),
            pareto_size_history=d.get("pareto_size_history", []),
            rng_state=d["rng_state"],
            sniper_runs=d.get("sniper_runs", 0),
            sniper_invocations=d.get("sniper_invocations", 0),
            param_space=d.get("param_space", {}),
        )

    def save(self, outputdir: Path) -> None:
        write_json_atomic(state_path(outputdir), self.to_dict())

    @classmethod
    def load(cls, outputdir: Path) -> "Spea2SearchState | None":
        raw = read_raw_state(outputdir)
        if raw is None:
            return None
        found = raw.get("strategy", "greedy")
        if found != cls.STRATEGY:
            print(f"Saved search state at {state_path(outputdir)} was written by strategy "
                  f"'{found}', not '{cls.STRATEGY}' — starting fresh.\n")
            return None
        return cls.from_dict(raw)


def _evaluate_entity(
    params: dict[str, Any], out: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]],
    baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], prefix: str,
) -> tuple[DesignPoint | None, bool, int]:
    point, ran, invocations = evaluate_point(params, _modified_params(params), out, reference_config, sniper, benchmarks, baseline, alpha, global_cache)
    if point is not None:
        print_evaluated_point(params, point, prefix=prefix)
    return point, ran, invocations


def _titan_config(
    titan: bool, outputdir: Path, titan_benchmark_json: str | None, titan_dir: str | None,
    titan_host_dir: str | None, titan_sniper_mount: str, titan_benchmarks_mount: str,
    titan_poll_interval: float,
) -> dict[str, Any] | None:
    """None when titan=False -- callers fall back to local per-entity
    evaluation, unchanged."""
    if not titan:
        return None
    if not titan_benchmark_json:
        raise ValueError("titan=True requires titan_benchmark_json (the titan_controller "
                          "benchmark JSON covering the same benchmark names given via --).")
    return {
        "titan_controller_dir": Path(titan_dir) if titan_dir else Path(__file__).resolve().parents[2] / "titan_controller",
        "benchmark_json_path": titan_benchmark_json,
        "host_destination_path": Path(titan_host_dir) if titan_host_dir else outputdir / "titan",
        "sniper_mount": titan_sniper_mount,
        "benchmarks_mount": titan_benchmarks_mount,
        "poll_interval": titan_poll_interval,
    }


def _evaluate_populations(
    populations_params: list[list[dict[str, Any]]],
    out_prefix: str,
    reference_config: str,
    sniper: Path,
    benchmarks: dict[str, list[str]],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
    outputdir: Path,
    titan_config: dict[str, Any] | None,
) -> tuple[list[list[DesignPoint]], int, int]:
    """Evaluates every entity across all populations for one generation --
    locally one at a time, or (titan_config given) as one titan_batch job."""
    if titan_config is None:
        pop_points_list: list[list[DesignPoint]] = []
        runs = invocations = 0
        for pop_idx, entity_params in enumerate(populations_params):
            pop_points = []
            for ent_idx, params in enumerate(entity_params):
                out = outputdir / f"{out_prefix}_pop{pop_idx}_ent{ent_idx}"
                point, ran, inv = _evaluate_entity(
                    params, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache,
                    prefix=f"[pop{pop_idx}] ",
                )
                runs += ran
                invocations += inv
                if point is not None:
                    pop_points.append(point)
            pop_points_list.append(pop_points)
        return pop_points_list, runs, invocations

    flat = [
        (pop_idx, params, outputdir / f"{out_prefix}_pop{pop_idx}_ent{ent_idx}", _modified_params(params))
        for pop_idx, entity_params in enumerate(populations_params)
        for ent_idx, params in enumerate(entity_params)
    ]
    results = titan_batch.evaluate_batch(
        [(params, out, mod) for _, params, out, mod in flat],
        reference_config, benchmarks, baseline, alpha, global_cache,
        titan_controller_dir=titan_config["titan_controller_dir"],
        benchmark_json_path=titan_config["benchmark_json_path"],
        host_destination_path=titan_config["host_destination_path"] / out_prefix,
        sniper_mount=titan_config["sniper_mount"],
        benchmarks_mount=titan_config["benchmarks_mount"],
        poll_interval=titan_config["poll_interval"],
        job_name=f"asi_{out_prefix}",
    )
    pop_points_list = [[] for _ in populations_params]
    runs = invocations = 0
    for (pop_idx, params, out, _), (point, ran, inv) in zip(flat, results):
        runs += ran
        invocations += inv
        if point is not None:
            print_evaluated_point(params, point, prefix=f"[pop{pop_idx}] ")
            pop_points_list[pop_idx].append(point)
    return pop_points_list, runs, invocations


def _live_output_dirs(state: "Spea2SearchState", baseline_dir: Path) -> set[Path]:
    dirs = {baseline_dir}
    for pop in state.populations:
        dirs |= {p.output_path for p in pop if p.output_path}
    for arc in state.archives:
        dirs |= {p.output_path for p in arc if p.output_path}
    dirs |= {p.output_path for p in state.pareto_front if p.output_path}
    return dirs


def explore_pareto_front_spea2(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 30,
    num_populations: int = 3,
    population_size: int = 20,
    archive_size: int = 10,
    p_mutation: float = 0.10,
    p_crossover: float = 0.90,
    p_migration: float = 0.10,
    patience: int = 5,
    seed: int = 0,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
    seed_entities: list[dict[str, Any]] | None = None,
    preeval_runs: int = 0,
    preeval_invocations: int = 0,
    titan: bool = False,
    titan_benchmark_json: str | None = None,
    titan_dir: str | None = None,
    titan_host_dir: str | None = None,
    titan_sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    titan_benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    titan_poll_interval: float = 30.0,
) -> list[DesignPoint]:
    """COLE-style multi-objective evolutionary (SPEA2) exploration of the ASI
    versus speedup design space; see README's "Strategy: spea2" section.

    initial_cache seeds global_cache on a fresh start, ignored when resuming.
    seed_entities replaces generation 0's random population with a
    caller-supplied list (round-robined across populations), used by
    hybrid.py to seed SPEA2 from another strategy's front. preeval_runs/
    preeval_invocations attribute Sniper cost already spent before this call
    (e.g. screening) into generation 0's own totals.
    """
    titan_config = _titan_config(
        titan, outputdir, titan_benchmark_json, titan_dir, titan_host_dir,
        titan_sniper_mount, titan_benchmarks_mount, titan_poll_interval,
    )

    loaded = Spea2SearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, benchmarks, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha/param-space — starting fresh.\n")

    if resumable:
        state = loaded
        rng = random.Random()
        rng.setstate(rng_state_from_json(state.rng_state))
        print(f"Resuming SPEA2 search from generation {state.generation} "
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

        rng = random.Random(seed)

        seed_prefix = [dict(DEFAULTS)] + [dict(p) for p in (seed_entities or [])]
        if seed_entities:
            seed_partitions: list[list[dict[str, Any]]] = [[] for _ in range(num_populations)]
            for i, ent in enumerate(seed_entities):
                seed_partitions[i % num_populations].append(dict(ent))
            sizes = ", ".join(str(len(p)) for p in seed_partitions)
            print(f"=== Generation 0 (initializing {num_populations} populations by splitting "
                  f"{len(seed_entities)} seed entities across them [{sizes}], plus baseline each, "
                  f"no random fill) ===")
        else:
            print(f"=== Generation 0 (initializing {num_populations} populations "
                  f"of {population_size} entities) ===")
        populations_params: list[list[dict[str, Any]]] = []
        for pop_idx in range(num_populations):
            if seed_entities:
                entity_params = [dict(DEFAULTS)] + seed_partitions[pop_idx]
            else:
                fill = [_random_entity(rng) for _ in range(max(0, population_size - len(seed_prefix)))]
                entity_params = (seed_prefix + fill)[:population_size]
            populations_params.append(entity_params)

        populations, runs_this_gen, invocations_this_gen = _evaluate_populations(
            populations_params, "gen0", reference_config, sniper, benchmarks, baseline, alpha,
            global_cache, outputdir, titan_config,
        )

        if preeval_runs:
            print(f"  (+{preeval_runs} configuration{'s' if preeval_runs != 1 else ''} already "
                  f"spent by pre-evaluation screening, counted into generation 0)")
            runs_this_gen += preeval_runs
            invocations_this_gen += preeval_invocations

        archives = [
            _environmental_selection(pop, [], min(archive_size, len(pop)))
            for pop in populations
        ]
        pareto_front = update_pareto_front([], [p for arc in archives for p in arc])
        hv_history = [hypervolume([baseline]), hypervolume(pareto_front)]
        sim_history = [0, runs_this_gen]
        pareto_size_history = [1, len(pareto_front)]

        state = Spea2SearchState(
            reference_config=str(reference_config), benchmarks=benchmarks, alpha=alpha, generation=0,
            baseline=baseline, global_cache=global_cache, populations=populations,
            archives=archives, pareto_front=pareto_front, pareto_front_history=[list(pareto_front)],
            hv_history=hv_history, sim_history=sim_history, pareto_size_history=pareto_size_history,
            rng_state=rng_state_to_json(rng), sniper_runs=runs_this_gen,
            sniper_invocations=invocations_this_gen, param_space=PARAM_SPACE,
        )
        state.save(outputdir)
        print(f"\n  Combined Pareto front after generation 0 "
              f"({len(state.pareto_front)} points, HV={hv_history[-1]:.4f}):")
        print_pareto_table(state.pareto_front)
        print(f"  Ran sniper {runs_this_gen} time{'s' if runs_this_gen != 1 else ''} this generation "
              f"({state.sniper_runs} total).")
        print()

    baseline_dir = state.baseline.output_path

    for generation in range(state.generation + 1, max_iterations + 1):
        print(f"=== Generation {generation} ===")

        next_populations: list[list[dict[str, Any]]] = []
        for pop_idx in range(len(state.archives)):
            mating_pool = _make_mating_pool(pop_idx, state.archives, population_size, p_migration, rng)
            children: list[dict[str, Any]] = []
            while len(children) < population_size:
                a, b = (rng.sample(mating_pool, 2) if len(mating_pool) >= 2 else (mating_pool[0], mating_pool[0]))
                child = _crossover(a.params, b.params, rng) if rng.random() < p_crossover else dict(a.params)
                if rng.random() < p_mutation:
                    child = _mutate(child, rng)
                children.append(child)
            next_populations.append(children)

        for pop_idx, entity_params in enumerate(next_populations):
            print(f"  Evaluating population {pop_idx} ({len(entity_params)} entities)...")
        evaluated_populations, runs_this_gen, invocations_this_gen = _evaluate_populations(
            next_populations, f"gen{generation}", reference_config, sniper, state.benchmarks, state.baseline,
            alpha, state.global_cache, outputdir, titan_config,
        )
        state.populations = evaluated_populations
        state.sniper_runs += runs_this_gen
        state.sniper_invocations += invocations_this_gen

        state.archives = [
            _environmental_selection(state.populations[i], state.archives[i], archive_size)
            for i in range(len(state.populations))
        ]

        state.pareto_front = update_pareto_front([], [p for arc in state.archives for p in arc])
        state.pareto_front_history.append(list(state.pareto_front))
        hv = hypervolume(state.pareto_front)
        state.hv_history.append(hv)
        state.sim_history.append(state.sniper_runs)
        state.pareto_size_history.append(len(state.pareto_front))

        live_dirs = _live_output_dirs(state, baseline_dir)
        all_cached_dirs = {p.output_path for p in state.global_cache.values() if p.output_path}
        n = cleanup_dirs(all_cached_dirs - live_dirs)
        if n:
            print(f"  Deleted {n} discarded output director{'y' if n == 1 else 'ies'}.")

        print(f"\n  Combined Pareto front after generation {generation} "
              f"({len(state.pareto_front)} points, HV={hv:.4f}):")
        print_pareto_table(state.pareto_front)
        print(f"  Ran sniper {runs_this_gen} time{'s' if runs_this_gen != 1 else ''} this generation "
              f"({state.sniper_runs} total).")
        print()

        state.generation = generation
        state.rng_state = rng_state_to_json(rng)
        state.save(outputdir)

        if _has_converged(state.hv_history, patience):
            print(f"  No hypervolume improvement for {patience} generations — converged.\n")
            break

    live_dirs = _live_output_dirs(state, baseline_dir)
    all_cached_dirs = {p.output_path for p in state.global_cache.values() if p.output_path}
    n = cleanup_dirs(all_cached_dirs - live_dirs)
    if n:
        print(f"Final cleanup: removed {n} stale output director{'y' if n == 1 else 'ies'}.")

    print(f"Configurations evaluated: {state.sniper_runs}")
    print(f"Total sniper invocations: {state.sniper_invocations}")
    print(f"Final hypervolume: {hypervolume(state.pareto_front):.4f}\n")

    plot_pareto_fronts_on_asi(
        state.pareto_front_history, title="ASI Pareto Fronts by Generation",
        save_path=outputdir / "pareto_history.png", show=False,
    )
    plot_pareto_front_on_asi(
        state.pareto_front, title="Final ASI Pareto Front",
        save_path=outputdir / "pareto_final.png", show=False,
    )
    plot_hv_vs_simulations(
        state.sim_history, state.hv_history, state.pareto_size_history,
        title="Hypervolume & Pareto Front Size vs. Simulations (spea2)",
        save_path=outputdir / "hv_vs_sims.png", show=False,
    )

    return state.pareto_front
