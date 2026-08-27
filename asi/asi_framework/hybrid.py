"""Hybrid strategy: MESMO until its hypervolume plateaus, then SPEA2 seeded
from MESMO's final Pareto front."""
from pathlib import Path
from typing import Any

from .models import DesignPoint
from .config import DEFAULT_ALPHA
from . import mesmo, spea2
from .plot import plot_hv_vs_simulations

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
    """Hybrid MESMO -> SPEA2 exploration.
    titan (and the rest of the titan_* flags) are forwarded to both phases
    unchanged -- each phase's own outputdir (mesmo_phase/, spea2_phase/) keeps
    their Titan job submissions under separate host_destination_path trees.

    seed_entities (e.g. from pre-evaluation screening) are added to SPEA2's
    seed pool alongside MESMO's own final Pareto front, not to MESMO's phase
    -- MESMO already gets them for free as initial_cache hits.

    If a previous invocation already reached the SPEA2 phase (a matching
    spea2_phase/ checkpoint exists), the MESMO phase is skipped entirely on
    resume rather than re-entered. MESMO's own hv_patience convergence isn't
    persisted in its checkpoint -- only the last completed iteration is --
    so resuming explore_pareto_front_mesmo after it already converged and
    handed off to SPEA2 would otherwise just run more (wasted) MESMO
    iterations before ever reaching the SPEA2 resume check below."""
    mesmo_dir = Path(outputdir) / "mesmo_phase"
    spea2_dir = Path(outputdir) / "spea2_phase"
    mesmo_dir.mkdir(parents=True, exist_ok=True)
    spea2_dir.mkdir(parents=True, exist_ok=True)

    mesmo_patience = hv_patience if hv_patience is not None else _DEFAULT_HYBRID_MESMO_PATIENCE

    loaded_spea2_state = spea2.Spea2SearchState.load(spea2_dir)
    spea2_already_started = (
        loaded_spea2_state is not None and loaded_spea2_state.matches(reference_config, benchmarks, alpha)
    )

    if spea2_already_started:
        print("Found an existing SPEA2-phase checkpoint for this run -- MESMO phase already "
              "completed in an earlier invocation, resuming directly in SPEA2.\n")
        mesmo_state = mesmo.MesmoSearchState.load(mesmo_dir)
    else:
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
            titan=titan,
            titan_benchmark_json=titan_benchmark_json,
            titan_dir=titan_dir,
            titan_host_dir=titan_host_dir,
            titan_sniper_mount=titan_sniper_mount,
            titan_benchmarks_mount=titan_benchmarks_mount,
            titan_poll_interval=titan_poll_interval,
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
        seed_entities=[p.params for p in mesmo_state.pareto_front] + list(seed_entities or []),
        preeval_runs=preeval_runs,
        preeval_invocations=preeval_invocations,
        titan=titan,
        titan_benchmark_json=titan_benchmark_json,
        titan_dir=titan_dir,
        titan_host_dir=titan_host_dir,
        titan_sniper_mount=titan_sniper_mount,
        titan_benchmarks_mount=titan_benchmarks_mount,
        titan_poll_interval=titan_poll_interval,
    )
    spea2_state = spea2.Spea2SearchState.load(spea2_dir)

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
