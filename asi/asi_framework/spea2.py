"""
COLE-style multi-objective evolutionary search strategy.

Adapted from Hoste & Eeckhout, "COLE: Compiler Optimization Level
Exploration" (CGO'08), which itself builds on SPEA2 (Zitzler, Laumanns &
Thiele, 2001). COLE applied SPEA2 to compiler-flag selection (two objectives:
execution time, compilation time); here the same SPEA2 mechanism is applied
to Sniper microarchitecture parameters (two objectives: ASI, speedup).

Unlike the greedy strategy in search.py -- which starts from the baseline and
grows the design space one parameter at a time -- SPEA2 samples full
multi-dimensional configurations ("entities") directly from the whole
PARAM_SPACE and refines them generation over generation via selection,
crossover and mutation. See explore_pareto_front_spea2()'s docstring for the
per-generation algorithm, and the accompanying conversation writeup for the
full explanation of each step and the design decisions made where the paper's
description was ambiguous for this domain.
"""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from .models import DesignPoint
from .runner import run
from .config import PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS
from .greedy import (
    evaluate_point, dominates, update_pareto_front, print_pareto_table,
    print_evaluated_point, params_key,
)
from .state import (
    point_to_dict, point_from_dict, state_path, write_json_atomic, read_raw_state,
    cleanup_dirs, rng_state_to_json, rng_state_from_json,
)


# ---------------------------------------------------------------------------
# Entity generation and genetic operators
# ---------------------------------------------------------------------------

def _random_entity(rng: random.Random) -> dict[str, Any]:
    """A fully-specified configuration: one value per PARAM_SPACE parameter,
    each drawn independently and uniformly from that parameter's candidates."""
    return {param: rng.choice(values) for param, values in PARAM_SPACE.items()}


def _mutate(entity: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Randomly reassign a single parameter to one of its candidate values."""
    child = dict(entity)
    param = rng.choice(list(PARAM_SPACE))
    child[param] = rng.choice(PARAM_SPACE[param])
    return child


def _crossover(a: dict[str, Any], b: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """Uniform crossover: each parameter independently comes from parent a or b.
    Parents may be "sparse" (e.g. the seeded baseline entity is {}, meaning
    "use the reference config's own value for every parameter") -- .get()
    with the DEFAULTS fallback treats a missing key the same way the rest of
    the codebase does, and the child this produces is always fully dense."""
    return {
        param: (a.get(param, DEFAULTS[param]) if rng.random() < 0.5 else b.get(param, DEFAULTS[param]))
        for param in PARAM_SPACE
    }


def _modified_params(params: dict[str, Any]) -> set[str]:
    return {p for p, v in params.items() if v != DEFAULTS[p]}


# ---------------------------------------------------------------------------
# SPEA2 fitness assignment and environmental selection
# ---------------------------------------------------------------------------

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
    """SPEA2's truncation operator: while there are more non-dominated points
    than fit in the archive, repeatedly drop the one closest to its nearest
    neighbor (the most "crowded" point), so the survivors spread out to cover
    the objective-value range as widely as possible -- this is the mechanism
    behind the paper's "the archive selects the entities that cover the
    objective function ranges as widely as possible." Ties are broken in
    favor of dropping the point with *more* non-default parameters, matching
    the paper's preference for simpler configurations."""
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


# ---------------------------------------------------------------------------
# Mating pool construction (selection + migration)
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Hypervolume (paper Sec 4.3) -- used both to report progress and to detect
# convergence ("no more improvement").
# ---------------------------------------------------------------------------

def _hypervolume(front: list[DesignPoint], ref_asi: float = 0.0, ref_speedup: float = 0.0) -> float:
    """2D hypervolume of a maximizing Pareto front relative to a reference
    point that's worse than every point on both axes (the origin, since ASI
    and speedup are always positive here)."""
    if not front:
        return 0.0
    pts = sorted(front, key=lambda p: p.speedup)  # ascending speedup => non-increasing asi
    hv = 0.0
    prev_speedup = ref_speedup
    for p in pts:
        hv += max(0.0, p.asi - ref_asi) * max(0.0, p.speedup - prev_speedup)
        prev_speedup = p.speedup
    return hv


def _has_converged(hv_history: list[float], patience: int, rel_tol: float = 1e-3) -> bool:
    """True if the best hypervolume seen in the last `patience` generations
    hasn't meaningfully exceeded the best hypervolume from everything before
    that window -- i.e. the overall Pareto frontier has stopped improving."""
    if len(hv_history) <= patience:
        return False
    best_before = max(hv_history[:-patience]) # evrything before the last `patience` generations
    recent_best = max(hv_history[-patience:]) # the last `patience` generations
    return recent_best <= best_before * (1 + rel_tol)


# ---------------------------------------------------------------------------
# Resumable state
# ---------------------------------------------------------------------------

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
    hv_history: list[float]
    rng_state: list

    def matches(self, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> bool:
        return (
            self.reference_config == str(reference_config)
            and self.benchmarks == benchmarks
            and self.alpha == alpha
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
            "hv_history": self.hv_history,
            "rng_state": self.rng_state,
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
            hv_history=d["hv_history"],
            rng_state=d["rng_state"],
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


# ---------------------------------------------------------------------------
# Main search loop
# ---------------------------------------------------------------------------

def _evaluate_entity(
    params: dict[str, Any], out: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]],
    baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], prefix: str,
) -> DesignPoint | None:
    point = evaluate_point(params, _modified_params(params), out, reference_config, sniper, benchmarks, baseline, alpha, global_cache)
    if point is not None:
        print_evaluated_point(params, point, prefix=prefix)
    return point


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
) -> list[DesignPoint]:
    """
    COLE-style multi-objective evolutionary (SPEA2) exploration of the ASI
    versus speedup design space.

    Per generation:
      1. Environmental selection: for each population, combine it with its
         current archive, compute SPEA2 fitness (dominance strength + a
         crowding-distance density term), and keep the best `archive_size`
         entities -- all non-dominated ones if they fit, otherwise a
         crowding-truncated subset so the archive spreads across the front
         rather than clustering.
      2. Mating pool: for each population, fill a pool of `population_size`
         slots. Each slot is either migrated in from another population's
         archive (probability p_migration) or chosen via binary tournament
         from the population's own archive (fitness-based, lower is better).
      3. Reproduction: repeatedly pick two mating-pool parents; with
         probability p_crossover produce a child via uniform crossover
         (each parameter independently inherited from either parent),
         otherwise the child is just a copy of one parent; then with
         probability p_mutation, reassign one random parameter of the
         child to a new value. This becomes the next generation's
         population.
      4. Evaluate every entity in the new populations (cache-aware, same
         evaluate_point()/global_cache as the greedy strategy).

    Termination: stops when the combined (across-population) Pareto front's
    hypervolume hasn't meaningfully improved for `patience` consecutive
    generations, or when `max_iterations` generations have run -- whichever
    comes first.
    """
    loaded = Spea2SearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, benchmarks, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha — starting fresh.\n")

    if resumable:
        state = loaded
        rng = random.Random()
        rng.setstate(rng_state_from_json(state.rng_state))
        print(f"Resuming SPEA2 search from generation {state.generation} "
              f"(found {state_path(outputdir)})\n")
    else:
        print("Running baseline...")
        baseline_dir = outputdir / "baseline"
        areas: list[float] = []
        powers: list[float] = []
        per_benchmark: dict[str, dict[str, float]] = {}
        for name, bench_cmd in benchmarks.items():
            try:
                area, peak_power, time = run(reference_config, sniper, baseline_dir / name, bench_cmd, {})
            except Exception as exc:
                raise RuntimeError(f"Baseline run failed ({name}): {exc}") from exc
            areas.append(area)
            powers.append(peak_power)
            per_benchmark[name] = {"time": time, "speedup": 1.0}

        baseline = DesignPoint(
            params={}, area=sum(areas) / len(areas), peak_power=sum(powers) / len(powers),
            time=sum(d["time"] for d in per_benchmark.values()) / len(per_benchmark),
            asi=1.0, speedup=1.0, modified_params=set(), output_path=baseline_dir,
            per_benchmark=per_benchmark,
        )
        print(f"  Area={baseline.area:.2f} mm²  PeakPow={baseline.peak_power:.2f} W")
        for name, d in per_benchmark.items():
            print(f"    {name}: Time={d['time']:.0f} ns")
        print()

        rng = random.Random(seed)
        global_cache: dict[frozenset, DesignPoint] = {params_key({}): baseline}

        print(f"=== Generation 0 (initializing {num_populations} populations "
              f"of {population_size} entities) ===")
        populations: list[list[DesignPoint]] = []
        for pop_idx in range(num_populations):
            # Seed with the baseline (analogous to COLE seeding -O1/-O2/-O3/-Os)
            # so the search has a known-good starting point, then fill the
            # rest of the population with random entities.
            entity_params = [{}] + [_random_entity(rng) for _ in range(population_size - 1)]
            pop_points = []
            for ent_idx, params in enumerate(entity_params):
                out = outputdir / f"gen0_pop{pop_idx}_ent{ent_idx}"
                point = _evaluate_entity(
                    params, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache,
                    prefix=f"[pop{pop_idx}] ",
                )
                if point is not None:
                    pop_points.append(point)
            populations.append(pop_points)

        archives = [
            _environmental_selection(pop, [], min(archive_size, len(pop)))
            for pop in populations
        ]
        pareto_front = update_pareto_front([], [p for arc in archives for p in arc])
        hv_history = [_hypervolume(pareto_front)]

        state = Spea2SearchState(
            reference_config=str(reference_config), benchmarks=benchmarks, alpha=alpha, generation=0,
            baseline=baseline, global_cache=global_cache, populations=populations,
            archives=archives, pareto_front=pareto_front, hv_history=hv_history,
            rng_state=rng_state_to_json(rng),
        )
        state.save(outputdir)
        print(f"\n  Combined Pareto front after generation 0 "
              f"({len(state.pareto_front)} points, HV={hv_history[-1]:.4f}):")
        print_pareto_table(state.pareto_front)
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

        evaluated_populations: list[list[DesignPoint]] = []
        for pop_idx, entity_params in enumerate(next_populations):
            print(f"  Evaluating population {pop_idx} ({len(entity_params)} entities)...")
            pop_points = []
            for ent_idx, params in enumerate(entity_params):
                out = outputdir / f"gen{generation}_pop{pop_idx}_ent{ent_idx}"
                point = _evaluate_entity(
                    params, out, reference_config, sniper, state.benchmarks, state.baseline, alpha, state.global_cache,
                    prefix=f"[pop{pop_idx}] ",
                )
                if point is not None:
                    pop_points.append(point)
            evaluated_populations.append(pop_points)
        state.populations = evaluated_populations

        state.archives = [
            _environmental_selection(state.populations[i], state.archives[i], archive_size)
            for i in range(len(state.populations))
        ]

        state.pareto_front = update_pareto_front([], [p for arc in state.archives for p in arc])
        hv = _hypervolume(state.pareto_front)
        state.hv_history.append(hv)

        live_dirs = _live_output_dirs(state, baseline_dir)
        all_cached_dirs = {p.output_path for p in state.global_cache.values() if p.output_path}
        n = cleanup_dirs(all_cached_dirs - live_dirs)
        if n:
            print(f"  Deleted {n} discarded output director{'y' if n == 1 else 'ies'}.")

        print(f"\n  Combined Pareto front after generation {generation} "
              f"({len(state.pareto_front)} points, HV={hv:.4f}):")
        print_pareto_table(state.pareto_front)
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

    return state.pareto_front
