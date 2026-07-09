"""
Hybrid search strategy: run MESMO until its hypervolume plateaus, then switch
to SPEA2 -- seeding every SPEA2 population's generation 0 with the Pareto
front MESMO found, instead of SPEA2's usual all-random start.

Rationale: MESMO's Bayesian-optimization surrogate is good at cheaply
narrowing in on promising regions of PARAM_SPACE with relatively few real
simulations, but it evaluates points one (or a small batch) at a time and
its GP surrogate gets more expensive to refit as more points accumulate.
SPEA2, by contrast, is good at *spreading out* an already-found front once
it has a decent population to recombine and mutate, but from a cold,
fully-random start it wastes many generations rediscovering what MESMO
already found in its first several iterations. This strategy tries to get
the best of both: MESMO for cheap early exploration, SPEA2 (warm-started
from MESMO's result) for the more expensive but more thorough refinement.

Reuses explore_pareto_front_mesmo() and explore_pareto_front_spea2()
completely unchanged, aside from one small addition to spea2.py
(`seed_entities`, see its docstring) needed to seed generation 0 from an
externally supplied Pareto front. This module itself is only those two
calls plus the glue needed to hand MESMO's results to SPEA2 -- see
explore_pareto_front_hybrid()'s docstring for why each phase gets its own
output sub-directory.
"""
from pathlib import Path

from .models import DesignPoint
from .config import DEFAULT_ALPHA
from . import mesmo, spea2
from .plot import plot_hv_vs_simulations

# MESMO's own --mesmo-patience defaults to None (run the whole iteration
# budget) since a plain MESMO run has no reason to stop early on its own --
# see mesmo.py's explore_pareto_front_mesmo docstring. The hybrid strategy's
# entire premise is "stop MESMO once it plateaus", so it needs a non-None
# default of its own here. cli.py always forwards --mesmo-patience's
# argparse default (None) unless the user passes it explicitly, so this is
# applied inside explore_pareto_front_hybrid() itself rather than as a plain
# Python default argument -- see the `mesmo_patience = ...` line below.
_DEFAULT_HYBRID_MESMO_PATIENCE = 5


def explore_pareto_front_hybrid(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 30,
    mesmo_max_iterations: int = 30,
    num_initial_points: int = 5,
    candidate_pool_size: int = 200,
    batch_size: int = 1,
    num_mc_samples: int = 10,
    gp_features: int = 250,
    gp_lengthscale: float | None = None,
    gp_noise: float = 1e-4,
    hv_patience: int | None = None,
    num_populations: int = 3,
    population_size: int = 20,
    archive_size: int = 10,
    p_mutation: float = 0.10,
    p_crossover: float = 0.90,
    p_migration: float = 0.10,
    patience: int = 5,
    seed: int = 0,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
) -> list[DesignPoint]:
    """
    Hybrid MESMO -> SPEA2 exploration of the ASI versus speedup design space.

    Phase 1 runs explore_pareto_front_mesmo() (unchanged) against
    `outputdir/mesmo_phase`, with `hv_patience` defaulted to
    `_DEFAULT_HYBRID_MESMO_PATIENCE` (5) instead of mesmo's own "unset"
    default -- this is the "plateau" the whole strategy is named for: MESMO
    stops as soon as its hypervolume hasn't improved for that many
    iterations (or `mesmo_max_iterations` is reached, whichever comes
    first), rather than running a full fixed budget.

    Phase 2 runs explore_pareto_front_spea2() (unchanged except for its new
    `seed_entities` parameter) against `outputdir/spea2_phase`, with:
      - `initial_cache=<MESMO's final global_cache>`, so SPEA2's own
        baseline run and any generation-0 entity that happens to coincide
        with something MESMO already evaluated is served from cache instead
        of re-simulated (same mechanism screening.py's `initial_cache`
        already uses for both mesmo and spea2).
      - `seed_entities=<MESMO's final Pareto front's params>`, which is what
        actually changes what SPEA2 evaluates: every population's
        generation 0 starts from exactly that front, with no random padding
        at all -- since every point in it is already in `initial_cache`,
        generation 0 costs zero new sniper runs (see spea2.py's
        `seed_entities` docstring), instead of the usual
        `population_size - 1` fresh random draws per population.

    Each phase gets its own output sub-directory rather than sharing
    `outputdir` directly. This matters for resumability: mesmo.py and
    spea2.py each stamp their own `strategy` name into `search_state.json`
    and refuse to resume a file written by the other one (see
    Mesmo/Spea2SearchState.load()) -- falling back to "starting fresh"
    instead. If both phases wrote to the same directory, SPEA2's very first
    checkpoint write would silently overwrite MESMO's saved state; resuming
    an interrupted hybrid run later would then find no MESMO state at all
    and have to redo every MESMO iteration (re-spending its Sniper runs)
    before even reaching SPEA2 again. Separate sub-directories keep each
    phase independently resumable, exactly as if you ran `--strategy mesmo`
    and `--strategy spea2` by hand against two different `--outputdir`s.
    """
    mesmo_dir = Path(outputdir) / "mesmo_phase"
    spea2_dir = Path(outputdir) / "spea2_phase"
    # Mirrors cli.py's own outputdir.mkdir() before dispatching to a
    # strategy: neither sub-directory is otherwise guaranteed to exist
    # before its phase's first search_state.json write, e.g. if the
    # baseline is served entirely from `initial_cache` and every
    # evaluate_point() call in that phase up to the first checkpoint is
    # also a cache hit, so runner.run() never gets a chance to mkdir it as
    # a side effect.
    mesmo_dir.mkdir(parents=True, exist_ok=True)
    spea2_dir.mkdir(parents=True, exist_ok=True)

    mesmo_patience = hv_patience if hv_patience is not None else _DEFAULT_HYBRID_MESMO_PATIENCE

    print("=== Hybrid phase 1/2: MESMO until hypervolume plateau ===\n")
    mesmo.explore_pareto_front_mesmo(
        reference_config=reference_config,
        sniper=sniper,
        outputdir=mesmo_dir,
        benchmarks=benchmarks,
        alpha=alpha,
        max_iterations=mesmo_max_iterations,
        num_initial_points=num_initial_points,
        candidate_pool_size=candidate_pool_size,
        batch_size=batch_size,
        num_mc_samples=num_mc_samples,
        gp_features=gp_features,
        gp_lengthscale=gp_lengthscale,
        gp_noise=gp_noise,
        hv_patience=mesmo_patience,
        seed=seed,
        initial_cache=initial_cache,
    )
    mesmo_state = mesmo.MesmoSearchState.load(mesmo_dir)

    print("\n=== Hybrid phase 2/2: SPEA2 seeded from MESMO's Pareto front ===\n")
    front = spea2.explore_pareto_front_spea2(
        reference_config=reference_config,
        sniper=sniper,
        outputdir=spea2_dir,
        benchmarks=benchmarks,
        alpha=alpha,
        max_iterations=max_iterations,
        num_populations=num_populations,
        population_size=population_size,
        archive_size=archive_size,
        p_mutation=p_mutation,
        p_crossover=p_crossover,
        p_migration=p_migration,
        patience=patience,
        seed=seed,
        initial_cache=mesmo_state.global_cache,
        seed_entities=[p.params for p in mesmo_state.pareto_front],
    )
    spea2_state = spea2.Spea2SearchState.load(spea2_dir)

    # Splice the two phases' checkpoint histories into one continuous,
    # correctly-offset series instead of leaving each phase's plot to
    # (mis)report a plain sniper_runs total as "Total sniper runs" -- spea2's
    # own summary print (and its own sim_history, which restarts counting
    # from 0 the moment the phase begins) only knows about *its* runs, not
    # the mesmo phase's, so naively printing/plotting it alone would
    # understate the true cost of the hybrid run and make it look like SPEA2
    # accomplished everything from a standing start. spea2_state.sim_history
    # always starts with a redundant [0]-th entry (hypervolume of the
    # baseline alone, before seed_entities/initial_cache are even applied --
    # see explore_pareto_front_spea2's own generation-0 bookkeeping), so it's
    # dropped here in favor of mesmo's own final point, which is the actual
    # state SPEA2's search started from.
    switch_sim = mesmo_state.sniper_runs
    combined_sim_history = mesmo_state.sim_history + [switch_sim + s for s in spea2_state.sim_history[1:]]
    combined_hv_history = mesmo_state.hv_history + spea2_state.hv_history[1:]
    combined_size_history = mesmo_state.pareto_size_history + spea2_state.pareto_size_history[1:]
    total_configs_evaluated = mesmo_state.sniper_runs + spea2_state.sniper_runs
    total_sniper_invocations = mesmo_state.sniper_invocations + spea2_state.sniper_invocations

    print(f"\nConfigurations evaluated (hybrid): {total_configs_evaluated} "
          f"({mesmo_state.sniper_runs} mesmo + {spea2_state.sniper_runs} spea2)")
    print(f"Total sniper invocations (hybrid): {total_sniper_invocations} "
          f"({mesmo_state.sniper_invocations} mesmo + {spea2_state.sniper_invocations} spea2)")
    print(f"Final hypervolume: {combined_hv_history[-1]:.4f}\n")

    plot_hv_vs_simulations(
        combined_sim_history, combined_hv_history, combined_size_history,
        title="Hypervolume & Pareto Front Size vs. Simulations (hybrid)",
        save_path=outputdir / "hv_vs_sims.png", show=False,
        switch_sims=[switch_sim], switch_label="MESMO → SPEA2 switch",
    )

    return front
