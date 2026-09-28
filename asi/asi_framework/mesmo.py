"""MESMO (Max-value Entropy Search for Multi-objective Optimization)
Bayesian-optimization search strategy; adapted from Belakaria, Deshwal &
Doppa, "Max-value Entropy Search for Multi-Objective Bayesian Optimization"
(NeurIPS'19 / JAIR'21)."""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from scipy.stats import norm

from .models import DesignPoint
from .config import PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS
from .metrics import update_pareto_front, params_key, hypervolume, has_converged
from .display import print_pareto_table, print_evaluated_point
from .evaluation import compute_baseline, evaluate_and_print
from .search_ops import random_entity, random_variant, modified_params
from . import titan_batch
from .plot import plot_pareto_front_on_asi, plot_pareto_fronts_on_asi, plot_hv_vs_simulations
from .state import (
    SearchStateBase, point_to_dict, point_from_dict, state_path,
    cleanup_dirs, rng_state_to_json, rng_state_from_json,
)


_LOCAL_POOL_FRACTION = 0.5


def _candidate_pool(
    rng: random.Random, pool_size: int, exclude: set[frozenset], anchors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Fresh pool of not-yet-evaluated configs to score this iteration --
    up to _LOCAL_POOL_FRACTION are neighbors of anchors (e.g. the current
    Pareto front), the rest fresh random draws. Falls back to pure random
    when anchors is empty. May return short if PARAM_SPACE is nearly exhausted."""
    seen = set(exclude)
    pool: list[dict[str, Any]] = []
    attempts = 0
    max_attempts = max(pool_size * 20, 200)
    num_local = int(pool_size * _LOCAL_POOL_FRACTION) if anchors else 0
    while len(pool) < pool_size and attempts < max_attempts:
        entity = random_variant(rng.choice(anchors), rng, PARAM_SPACE) if len(pool) < num_local else random_entity(rng, PARAM_SPACE)
        attempts += 1
        key = params_key(entity)
        if key in seen:
            continue
        seen.add(key)
        pool.append(entity)
    return pool


def _feature_names(param_space: dict[str, list]) -> list[tuple[str, Any]]:
    """Ordered (param, value) pairs, one per non-default candidate value --
    a fixed column order for _encode()'s one-hot vectors."""
    return [(param, value) for param, values in param_space.items() for value in values[1:]]


def _encode(params: dict[str, Any], feature_names: list[tuple[str, Any]]) -> np.ndarray:
    """params -> one-hot vector over feature_names."""
    vec = np.zeros(len(feature_names), dtype=np.float64)
    for i, (param, value) in enumerate(feature_names):
        if params.get(param, DEFAULTS[param]) == value:
            vec[i] = 1.0
    return vec


def _encode_all(points_params: list[dict[str, Any]], feature_names: list[tuple[str, Any]]) -> np.ndarray:
    if not points_params:
        return np.zeros((0, len(feature_names)))
    return np.stack([_encode(p, feature_names) for p in points_params])


def _rng_array(rng: random.Random, n: int, kind: str) -> np.ndarray:
    if kind == "normal":
        return np.array([rng.gauss(0.0, 1.0) for _ in range(n)])
    if kind == "uniform_2pi":
        return np.array([rng.uniform(0.0, 2 * np.pi) for _ in range(n)])
    raise ValueError(kind)


def _safe_cholesky(cov: np.ndarray, jitter: float = 1e-10, max_tries: int = 5) -> np.ndarray:
    """Cholesky factor of cov, adding diagonal jitter if not quite PSD."""
    n = cov.shape[0]
    eye = np.eye(n)
    cur = jitter
    for _ in range(max_tries):
        try:
            return np.linalg.cholesky(cov + cur * eye)
        except np.linalg.LinAlgError:
            cur *= 10
    return np.linalg.cholesky(cov + cur * eye)


@dataclass
class _RFFModel:
    """Random-Fourier-Features approximation of a single objective's GP
    posterior (Rahimi & Recht 2008)."""
    W: np.ndarray
    b: np.ndarray
    feature_scale: float
    noise_var: float
    theta_mean: np.ndarray
    A_inv: np.ndarray
    y_mean: float
    y_std: float

    def _phi(self, X: np.ndarray) -> np.ndarray:
        return self.feature_scale * np.cos(X @ self.W.T + self.b)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mu, sigma) at every row of X, in original (ASI/speedup) units."""
        phi = self._phi(X)
        mu = self.y_mean + self.y_std * (phi @ self.theta_mean)
        var = self.noise_var * np.einsum("ij,jk,ik->i", phi, self.A_inv, phi)
        sigma = self.y_std * np.sqrt(np.maximum(var, 1e-12))
        return mu, sigma

    def sample_weights(self, rng: random.Random) -> np.ndarray:
        """One draw theta ~ N(theta_mean, noise_var * A_inv)."""
        L = _safe_cholesky(self.noise_var * self.A_inv)
        z = _rng_array(rng, len(self.theta_mean), "normal")
        return self.theta_mean + L @ z

    def evaluate(self, theta: np.ndarray, X: np.ndarray) -> np.ndarray:
        """f(x) = phi(x)^T theta for a sampled weight vector."""
        return self.y_mean + self.y_std * (self._phi(X) @ theta)


def _median_heuristic_lengthscale(X: np.ndarray, fallback: float = 1.0) -> float:
    """Median-heuristic kernel lengthscale: l^2 = (median squared pairwise
    distance) / 2, recomputed from real training data every iteration."""
    n = X.shape[0]
    if n < 2:
        return fallback
    sq_norms = np.sum(X ** 2, axis=1)
    sq_dists = np.maximum(sq_norms[:, None] + sq_norms[None, :] - 2 * (X @ X.T), 0.0)
    pairwise = sq_dists[np.triu_indices(n, k=1)]
    median = np.median(pairwise)
    return float(np.sqrt(median / 2.0)) if median > 0 else fallback


def _fit_rff_model(
    X: np.ndarray, y: np.ndarray, num_features: int, lengthscale: float, noise_var: float, rng: random.Random,
) -> _RFFModel:
    """Fit an _RFFModel to (X, y): fresh random feature basis, y
    standardized to zero-mean/unit-variance, closed-form Bayesian linear
    regression posterior over theta in that feature space."""
    noise_var = max(float(noise_var), 1e-8)
    num_dims = X.shape[1]
    W = _rng_array(rng, num_features * num_dims, "normal").reshape(num_features, num_dims) / lengthscale
    b = _rng_array(rng, num_features, "uniform_2pi")
    feature_scale = np.sqrt(2.0 / num_features)

    y_mean = float(np.mean(y))
    y_std = float(np.std(y)) or 1.0
    y_standardized = (y - y_mean) / y_std

    phi = feature_scale * np.cos(X @ W.T + b)
    A = phi.T @ phi + noise_var * np.eye(num_features)
    A_inv = np.linalg.inv(A)
    theta_mean = A_inv @ (phi.T @ y_standardized)

    return _RFFModel(
        W=W, b=b, feature_scale=feature_scale, noise_var=noise_var,
        theta_mean=theta_mean, A_inv=A_inv, y_mean=y_mean, y_std=y_std,
    )


@dataclass
class _Sample:
    """Lightweight stand-in for DesignPoint, just what dominates()/
    update_pareto_front() need to run on sampled (not real) objective values."""
    asi: float
    speedup: float
    params: dict


def _sample_pareto_front(asi_values: np.ndarray, speedup_values: np.ndarray) -> tuple[float, float]:
    """One Monte-Carlo sample's Pareto front (brute-force non-domination
    filtering over the candidate pool); returns each objective's maximum
    across that front."""
    samples = [_Sample(a, s, {"_pool_idx": i}) for i, (a, s) in enumerate(zip(asi_values, speedup_values))]
    front = update_pareto_front([], samples)
    return max(p.asi for p in front), max(p.speedup for p in front)


def _entropy_term(gamma: np.ndarray) -> np.ndarray:
    """Truncated-Gaussian entropy term of MESMO's acquisition function
    (Belakaria et al. eq. 4.13)."""
    cdf = np.clip(norm.cdf(gamma), 1e-12, 1.0)
    pdf = norm.pdf(gamma)
    return gamma * pdf / (2.0 * cdf) - np.log(cdf)


def _acquisition_scores(
    pool_X: np.ndarray, model_asi: _RFFModel, model_speedup: _RFFModel,
    num_mc_samples: int, rng: random.Random,
) -> np.ndarray:
    """MESMO's acquisition function evaluated at every row of pool_X: mean
    over num_mc_samples posterior draws of each objective's entropy term."""
    mu_asi, sigma_asi = model_asi.predict(pool_X)
    mu_speedup, sigma_speedup = model_speedup.predict(pool_X)
    sigma_asi = np.maximum(sigma_asi, 1e-9)
    sigma_speedup = np.maximum(sigma_speedup, 1e-9)

    total = np.zeros(pool_X.shape[0])
    for _ in range(num_mc_samples):
        theta_asi = model_asi.sample_weights(rng)
        theta_speedup = model_speedup.sample_weights(rng)
        asi_tilde = model_asi.evaluate(theta_asi, pool_X)
        speedup_tilde = model_speedup.evaluate(theta_speedup, pool_X)

        y_star_asi, y_star_speedup = _sample_pareto_front(asi_tilde, speedup_tilde)

        gamma_asi = (y_star_asi - mu_asi) / sigma_asi
        gamma_speedup = (y_star_speedup - mu_speedup) / sigma_speedup
        total += _entropy_term(gamma_asi) + _entropy_term(gamma_speedup)

    return total / num_mc_samples


@dataclass
class MesmoSearchState(SearchStateBase):
    """Resumable snapshot of an in-progress MESMO search, checkpointed to JSON."""
    STRATEGY: ClassVar[str] = "mesmo"

    iteration: int
    baseline: DesignPoint
    global_cache: dict[frozenset, DesignPoint]
    pareto_front: list[DesignPoint]
    pareto_front_history: list[list[DesignPoint]]
    hv_history: list[float]
    sim_history: list[int]
    pareto_size_history: list[int]
    rng_state: list
    sniper_runs: int
    sniper_invocations: int

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
            "global_cache": [point_to_dict(p) for p in self.global_cache.values()],
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
    def from_dict(cls, d: dict) -> "MesmoSearchState":
        return cls(
            reference_config=d["reference_config"],
            benchmarks=d["benchmarks"],
            alpha=d["alpha"],
            iteration=d["iteration"],
            baseline=point_from_dict(d["baseline"]),
            global_cache={params_key(x["params"]): point_from_dict(x) for x in d["global_cache"]},
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


def _evaluate_candidates(
    params_list: list[dict[str, Any]], out_prefix: str, reference_config: str, sniper: Path,
    benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float,
    global_cache: dict[frozenset, DesignPoint], outputdir: Path, titan_config: dict[str, Any] | None, prefix: str,
) -> tuple[list[DesignPoint], int, int]:
    """Evaluates every candidate in params_list -- locally one at a time, or
    (titan_config given) as one titan_batch job. Mirrors
    spea2._evaluate_populations, flattened for mesmo's single list of
    candidates per step (initial design, or one iteration's batch)."""
    if titan_config is None:
        evaluated: list[DesignPoint] = []
        runs = invocations = 0
        for idx, params in enumerate(params_list):
            out = outputdir / f"{out_prefix}{idx}"
            point, ran, inv = evaluate_and_print(
                params, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache, prefix=prefix,
            )
            runs += ran
            invocations += inv
            if point is not None:
                evaluated.append(point)
        return evaluated, runs, invocations

    flat = [
        (params, outputdir / f"{out_prefix}{idx}", modified_params(params))
        for idx, params in enumerate(params_list)
    ]
    results = titan_batch.evaluate_batch(
        [(params, out, mod) for params, out, mod in flat],
        reference_config, benchmarks, baseline, alpha, global_cache,
        titan_controller_dir=titan_config["titan_controller_dir"],
        benchmark_json_path=titan_config["benchmark_json_path"],
        host_destination_path=titan_config["host_destination_path"] / out_prefix,
        sniper_mount=titan_config["sniper_mount"],
        benchmarks_mount=titan_config["benchmarks_mount"],
        poll_interval=titan_config["poll_interval"],
        job_name=f"asi_{out_prefix}",
    )
    evaluated = []
    runs = invocations = 0
    for (params, _out, _mod), (point, ran, inv) in zip(flat, results):
        runs += ran
        invocations += inv
        if point is not None:
            print_evaluated_point(params, point, prefix=prefix)
            evaluated.append(point)
    return evaluated, runs, invocations


def explore_pareto_front_mesmo(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    benchmarks: dict[str, list[str]],
    alpha: float = DEFAULT_ALPHA,
    max_iterations: int = 30,
    num_initial_points: int = 5,
    candidate_pool_size: int = 200,
    batch_size: int = 1,
    num_mc_samples: int = 10,
    gp_features: int = 250,
    gp_lengthscale: float | None = None,
    gp_noise: float = 1e-4,
    hv_patience: int | None = None,
    seed: int = 0,
    initial_cache: dict[frozenset, DesignPoint] | None = None,
    titan: bool = False,
    titan_benchmark_json: str | None = None,
    titan_dir: str | None = None,
    titan_host_dir: str | None = None,
    titan_sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    titan_benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    titan_poll_interval: float = 30.0,
) -> list[DesignPoint]:
    """MESMO Bayesian-optimization exploration of the ASI versus speedup
    design space. hv_patience defaults to None (disabled), unlike spea2's aggressive default, since a
    mesmo iteration can evaluate as few as one point (batch_size=1) -- too
    noisy a signal for a short patience window. initial_cache seeds
    global_cache on a fresh start, ignored when resuming. With titan=True,
    the initial design and each iteration's batch_size candidates are
    submitted as one titan_batch job instead of evaluated one at a time --
    batch_size=1 (the default) gains nothing from titan past the initial
    design, since there's only ever one candidate per iteration to batch."""
    titan_config = titan_batch.build_config(
        titan, outputdir, titan_benchmark_json, titan_dir, titan_host_dir,
        titan_sniper_mount, titan_benchmarks_mount, titan_poll_interval,
    )

    loaded = MesmoSearchState.load(outputdir)
    resumable = loaded is not None and loaded.matches(reference_config, benchmarks, alpha)
    if loaded is not None and not resumable:
        print(f"Saved search state at {state_path(outputdir)} doesn't match this "
              f"run's config/command/alpha/param-space — starting fresh.\n")

    if resumable:
        state = loaded
        rng = random.Random()
        rng.setstate(rng_state_from_json(state.rng_state))
        print(f"Resuming MESMO search from iteration {state.iteration} "
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

        preeval_points = [p for k, p in global_cache.items() if k != baseline_key]
        if preeval_points:
            evaluated: list[DesignPoint] = [baseline] + preeval_points
            runs_this_iter = len(preeval_points)
            invocations_this_iter = sum(len(p.per_benchmark) for p in preeval_points)
            print(f"=== Initial design: baseline + {len(preeval_points)} pre-evaluation "
                  f"screening point{'s' if len(preeval_points) != 1 else ''} (reused from screening, "
                  f"no new simulations this run) ===")
        else:
            print(f"=== Initial design ({num_initial_points} points: baseline + "
                  f"{num_initial_points - 1} random configurations) ===")
            initial_params = [dict(DEFAULTS)] + [random_entity(rng, PARAM_SPACE) for _ in range(num_initial_points - 1)]
            evaluated, runs_this_iter, invocations_this_iter = _evaluate_candidates(
                initial_params, "init", reference_config, sniper, benchmarks, baseline, alpha, global_cache,
                outputdir, titan_config, prefix="[mesmo] ",
            )

        pareto_front = update_pareto_front([], evaluated)
        hv_history = [hypervolume([baseline]), hypervolume(pareto_front)]
        sim_history = [0, runs_this_iter]
        pareto_size_history = [1, len(pareto_front)]

        state = MesmoSearchState(
            reference_config=str(reference_config), benchmarks=benchmarks, alpha=alpha, iteration=0,
            baseline=baseline, global_cache=global_cache, pareto_front=pareto_front,
            pareto_front_history=[list(pareto_front)], hv_history=hv_history, sim_history=sim_history,
            pareto_size_history=pareto_size_history,
            rng_state=rng_state_to_json(rng), sniper_runs=runs_this_iter,
            sniper_invocations=invocations_this_iter, param_space=PARAM_SPACE,
        )
        state.save(outputdir)
        print(f"\n  Pareto front after initial design "
              f"({len(state.pareto_front)} point{'s' if len(state.pareto_front) != 1 else ''}, "
              f"HV={hv_history[-1]:.4f}):")
        print_pareto_table(state.pareto_front)
        print(f"  Ran sniper {runs_this_iter} time{'s' if runs_this_iter != 1 else ''} this step "
              f"({state.sniper_runs} total).")
        plot_pareto_front_on_asi(
            state.pareto_front, title="ASI Pareto Front (initial design)",
            save_path=outputdir / "pareto_init.png", show=False,
        )
        print()

    baseline_dir = state.baseline.output_path
    feature_names = _feature_names(PARAM_SPACE)

    for iteration in range(state.iteration + 1, max_iterations + 1):
        print(f"=== Iteration {iteration} ===")

        evaluated_points = list(state.global_cache.values())
        X_train = _encode_all([p.params for p in evaluated_points], feature_names)
        y_asi = np.array([p.asi for p in evaluated_points])
        y_speedup = np.array([p.speedup for p in evaluated_points])
        lengthscale = gp_lengthscale if gp_lengthscale is not None else _median_heuristic_lengthscale(X_train)

        model_asi = _fit_rff_model(X_train, y_asi, gp_features, lengthscale, gp_noise, rng)
        model_speedup = _fit_rff_model(X_train, y_speedup, gp_features, lengthscale, gp_noise, rng)

        pool_params = _candidate_pool(
            rng, candidate_pool_size, set(state.global_cache), [p.params for p in state.pareto_front],
        )
        if not pool_params:
            print("  Candidate pool empty (param space exhausted) — terminating early.\n")
            break
        pool_X = _encode_all(pool_params, feature_names)

        scores = _acquisition_scores(pool_X, model_asi, model_speedup, num_mc_samples, rng)
        order = np.argsort(-scores)
        chosen = order[:min(batch_size, len(pool_params))]

        print(f"  Evaluating {len(chosen)} configuration(s) (top acquisition score of "
              f"{len(pool_params)} candidates)...")
        chosen_params = [pool_params[int(idx)] for idx in chosen]
        evaluated, runs_this_iter, invocations_this_iter = _evaluate_candidates(
            chosen_params, f"iter{iteration}_cand", reference_config, sniper, state.benchmarks, state.baseline,
            alpha, state.global_cache, outputdir, titan_config, prefix="[mesmo] ",
        )
        state.sniper_runs += runs_this_iter
        state.sniper_invocations += invocations_this_iter

        old_pareto_dirs = {p.output_path for p in state.pareto_front if p.output_path}
        state.pareto_front = update_pareto_front(state.pareto_front, evaluated)
        state.pareto_front_history.append(list(state.pareto_front))
        hv = hypervolume(state.pareto_front)
        state.hv_history.append(hv)
        state.sim_history.append(state.sniper_runs)
        state.pareto_size_history.append(len(state.pareto_front))

        new_pareto_dirs = {p.output_path for p in state.pareto_front if p.output_path}
        dropped = (old_pareto_dirs | {p.output_path for p in evaluated if p.output_path}) - new_pareto_dirs - {baseline_dir}
        n = cleanup_dirs(dropped)
        if n:
            print(f"  Deleted {n} non-Pareto output director{'y' if n == 1 else 'ies'}.")

        print(f"\n  Pareto front after iteration {iteration} "
              f"({len(state.pareto_front)} point{'s' if len(state.pareto_front) != 1 else ''}, HV={hv:.4f}):")
        print_pareto_table(state.pareto_front)
        print(f"  Ran sniper {runs_this_iter} time{'s' if runs_this_iter != 1 else ''} this iteration "
              f"({state.sniper_runs} total).")
        plot_pareto_front_on_asi(
            state.pareto_front, title=f"ASI Pareto Front (iteration {iteration})",
            save_path=outputdir / f"pareto_iter{iteration}.png", show=False,
        )
        print()

        state.iteration = iteration
        state.rng_state = rng_state_to_json(rng)
        state.save(outputdir)

        if hv_patience is not None and has_converged(state.hv_history, hv_patience):
            print(f"  No hypervolume improvement for {hv_patience} iterations — converged.\n")
            break

    all_cached_dirs = {p.output_path for p in state.global_cache.values() if p.output_path}
    live_dirs = {p.output_path for p in state.pareto_front if p.output_path} | {baseline_dir}
    n = cleanup_dirs(all_cached_dirs - live_dirs)
    if n:
        print(f"Final cleanup: removed {n} stale output director{'y' if n == 1 else 'ies'}.")

    print(f"Configurations evaluated: {state.sniper_runs}")
    print(f"Total sniper invocations: {state.sniper_invocations}")
    print(f"Final hypervolume: {hypervolume(state.pareto_front):.4f}\n")

    plot_pareto_fronts_on_asi(
        state.pareto_front_history, title="ASI Pareto Fronts by Iteration",
        sequence_label="Iteration",
        save_path=outputdir / "pareto_history.png", show=False,
    )
    plot_pareto_front_on_asi(
        state.pareto_front, title="Final ASI Pareto Front",
        save_path=outputdir / "pareto_final.png", show=False,
    )
    plot_hv_vs_simulations(
        state.sim_history, state.hv_history, state.pareto_size_history,
        title="Hypervolume & Pareto Front Size vs. Simulations (mesmo)",
        save_path=outputdir / "hv_vs_sims.png", show=False,
    )

    return state.pareto_front
