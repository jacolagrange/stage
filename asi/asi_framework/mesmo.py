"""
MESMO (Max-value Entropy Search for Multi-objective Optimization) search
strategy -- a Bayesian-optimization strategy, as opposed to the evolutionary
one in spea2.py.

Adapted from Belakaria, Deshwal & Doppa, "Max-value Entropy Search for
Multi-Objective Bayesian Optimization" (NeurIPS'19), Section 4 / Algorithm 1
of the JAIR 2021 journal version ("Output Space Entropy Search Framework for
Multi-Objective Bayesian Optimization"). MESMO treats the two Sniper
microarchitecture objectives -- ASI and speedup -- as K=2 black-box
functions f1, f2 and picks, every iteration, the configuration whose
evaluation is expected to reveal the most information (per Monte-Carlo
sample) about the true Pareto front, without ever evaluating the whole
(combinatorially huge) PARAM_SPACE.

Unlike SPEA2 (spea2.py) -- which evolves whole populations of configurations
generation over generation using only their measured ASI/speedup -- MESMO
fits a cheap probabilistic surrogate model (a Gaussian process per
objective, approximated here via random Fourier features so its posterior
stays closed-form and fast) from every configuration evaluated so far, and
uses that surrogate to *rank* a fresh pool of not-yet-evaluated candidates by
how informative they would be, before spending a real (expensive) Sniper run
on only the best-ranked one(s). See explore_pareto_front_mesmo()'s docstring
for the per-iteration algorithm, and the accompanying conversation writeup
for the full derivation of every step below.
"""
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

import numpy as np
from scipy.stats import norm

from .models import DesignPoint
from .config import (
    PARAM_SPACE, DEFAULT_ALPHA, DEFAULTS, BRANCH_PREDICTOR_PARAMS, CONDITIONAL_PARAMS,
    DEFAULT_BRANCH_PREDICTOR_TYPE, active_params,
)
from .greedy import (
    evaluate_point, update_pareto_front, print_pareto_table,
    print_evaluated_point, params_key, compute_baseline, hypervolume,
)
from .plot import plot_pareto_front_on_asi, plot_pareto_fronts_on_asi, plot_hv_vs_simulations
from .state import (
    point_to_dict, point_from_dict, state_path, write_json_atomic, read_raw_state,
    cleanup_dirs, rng_state_to_json, rng_state_from_json,
)


# ---------------------------------------------------------------------------
# Candidate generation (mirrors spea2._random_entity/_modified_params --
# duplicated rather than imported across strategy modules, same as
# screening._random_entity already does, since both are a handful of lines
# tied only to config.py's PARAM_SPACE/CONDITIONAL_PARAMS).
# ---------------------------------------------------------------------------

def _random_entity(rng: random.Random) -> dict[str, Any]:
    """A fully-specified configuration: one value per PARAM_SPACE parameter
    that's relevant to the randomly chosen branch predictor type. Predictor-
    specific knobs outside that type are left out rather than varied for no
    functional effect -- see BRANCH_PREDICTOR_PARAMS."""
    entity = {
        param: rng.choice(values)
        for param, values in PARAM_SPACE.items()
        if param not in CONDITIONAL_PARAMS
    }
    for param in BRANCH_PREDICTOR_PARAMS.get(entity["branch_predictor_type"], ()):
        entity[param] = rng.choice(PARAM_SPACE[param])
    return entity


def _modified_params(params: dict[str, Any]) -> set[str]:
    return {p for p, v in params.items() if v != DEFAULTS[p]}


def _neighbor(entity: dict[str, Any], rng: random.Random) -> dict[str, Any]:
    """One PARAM_SPACE parameter of `entity` reassigned to a different
    candidate value -- a local move, mirroring spea2._mutate exactly, used
    to generate candidates *near* an already-good configuration (typically a
    current Pareto-front point) rather than uniformly at random. Without
    this, a purely random candidate pool almost never lands close (in
    Hamming distance) to a point already known to be good, so the
    acquisition function never gets a genuine near-optimal neighbor to
    weigh against fresh exploration -- see _candidate_pool."""
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


_LOCAL_POOL_FRACTION = 0.5  # fraction of each candidate pool drawn as neighbors of `anchors` rather than globally at random


def _candidate_pool(
    rng: random.Random, pool_size: int, exclude: set[frozenset], anchors: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """A fresh pool of not-yet-evaluated configurations to score with the
    acquisition function this iteration. MESMO's argmax_{x in X} alpha(x) is
    over the whole (combinatorially huge) PARAM_SPACE, so -- exactly like
    SPEA2's populations -- a finite sample stands in for it rather than
    enumerating every combination.

    Up to `_LOCAL_POOL_FRACTION` of the pool is generated by mutating a
    randomly chosen anchor (typically the current Pareto front) one
    parameter at a time, so the acquisition function gets genuine
    near-optimal neighbors to weigh against fresh global exploration --
    pure i.i.d. random draws from a combinatorial space this size almost
    never land close to any particular already-good point, which otherwise
    starves the search of any real local exploitation. The rest of the pool
    is still fresh global random draws (_random_entity), so entirely new
    regions of the space keep getting considered too. Falls back to pure
    random draws (no local component) when `anchors` is empty, e.g. before
    any Pareto front has ever been established.

    Stops early (returning a possibly-short pool) if PARAM_SPACE is small
    enough that fresh unique draws become hard to find."""
    seen = set(exclude)
    pool: list[dict[str, Any]] = []
    attempts = 0
    max_attempts = max(pool_size * 20, 200)
    num_local = int(pool_size * _LOCAL_POOL_FRACTION) if anchors else 0
    while len(pool) < pool_size and attempts < max_attempts:
        entity = _neighbor(rng.choice(anchors), rng) if len(pool) < num_local else _random_entity(rng)
        attempts += 1
        key = params_key(entity)
        if key in seen:
            continue
        seen.add(key)
        pool.append(entity)
    return pool


# ---------------------------------------------------------------------------
# Feature encoding: turn a (possibly sparse) params dict into a fixed-length
# numeric vector the GP kernel operates on.
# ---------------------------------------------------------------------------

def _feature_names(param_space: dict[str, list]) -> list[tuple[str, Any]]:
    """One (param, value) pair per *non-default* candidate value across all
    of PARAM_SPACE -- the same one-hot convention as
    screening._encode_features, just returned as an ordered list rather than
    dict keys so it can double as a fixed column order for numpy vectors."""
    return [(param, value) for param, values in param_space.items() for value in values[1:]]


def _encode(params: dict[str, Any], feature_names: list[tuple[str, Any]]) -> np.ndarray:
    """params -> one-hot vector over `feature_names`: 1.0 at (param, value)
    if this point uses that value for that param, else 0.0. A missing key
    (default value, or a conditional param inactive for this point's branch
    predictor type) reads as "at default" everywhere for that param, matching
    the codebase's sparse-dict convention."""
    vec = np.zeros(len(feature_names), dtype=np.float64)
    for i, (param, value) in enumerate(feature_names):
        if params.get(param, DEFAULTS[param]) == value:
            vec[i] = 1.0
    return vec


def _encode_all(points_params: list[dict[str, Any]], feature_names: list[tuple[str, Any]]) -> np.ndarray:
    if not points_params:
        return np.zeros((0, len(feature_names)))
    return np.stack([_encode(p, feature_names) for p in points_params])


# ---------------------------------------------------------------------------
# Random draws via Python's random.Random (not a second, separately-seeded
# numpy RNG stream), so a resumed search continues the exact same
# pseudo-random sequence as the rest of this strategy -- see
# state.rng_state_to_json, reused unchanged from spea2/greedy.
# ---------------------------------------------------------------------------

def _rng_array(rng: random.Random, n: int, kind: str) -> np.ndarray:
    if kind == "normal":
        return np.array([rng.gauss(0.0, 1.0) for _ in range(n)])
    if kind == "uniform_2pi":
        return np.array([rng.uniform(0.0, 2 * np.pi) for _ in range(n)])
    raise ValueError(kind)


def _safe_cholesky(cov: np.ndarray, jitter: float = 1e-10, max_tries: int = 5) -> np.ndarray:
    """Cholesky factor of `cov`, adding successively larger diagonal jitter
    if it isn't quite numerically PSD (cov here is a matrix product,
    noise_var * A_inv, not a literal covariance matrix someone constructed
    to be exactly symmetric-PSD in floating point)."""
    n = cov.shape[0]
    eye = np.eye(n)
    cur = jitter
    for _ in range(max_tries):
        try:
            return np.linalg.cholesky(cov + cur * eye)
        except np.linalg.LinAlgError:
            cur *= 10
    return np.linalg.cholesky(cov + cur * eye)


# ---------------------------------------------------------------------------
# Random-Fourier-Features GP surrogate (one instance per objective).
# ---------------------------------------------------------------------------

@dataclass
class _RFFModel:
    """Random-Fourier-Features approximation of a single objective's GP
    posterior (Rahimi & Recht 2008's kernel approximation, used for sampling
    posterior functions by Hernandez-Lobato et al. 2014 and this paper's
    Sec 4.1). A finite-dimensional Bayesian linear model phi(x)^T theta over
    `num_features` random cosine features approximates a squared-exponential
    kernel; its closed-form posterior mean/variance double as this
    objective's mu_j(x)/sigma_j(x) in the acquisition function (eq. 4.7),
    while drawing a fresh theta from that same posterior is exactly the
    paper's "sample a function ˜f from the posterior GP" step (eq. 4.8)."""
    W: np.ndarray            # (num_features, num_dims) ~ N(0, 1/lengthscale^2)
    b: np.ndarray            # (num_features,) ~ Uniform(0, 2*pi)
    feature_scale: float     # sqrt(2/num_features), the RFF normalizer
    noise_var: float
    theta_mean: np.ndarray   # (num_features,) posterior mean of theta
    A_inv: np.ndarray        # (num_features, num_features); Cov(theta) == noise_var * A_inv
    y_mean: float
    y_std: float

    def _phi(self, X: np.ndarray) -> np.ndarray:
        return self.feature_scale * np.cos(X @ self.W.T + self.b)

    def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """(mu, sigma) at every row of X, de-standardized back into this
        objective's original (ASI or speedup) units."""
        phi = self._phi(X)
        mu = self.y_mean + self.y_std * (phi @ self.theta_mean)
        var = self.noise_var * np.einsum("ij,jk,ik->i", phi, self.A_inv, phi)
        sigma = self.y_std * np.sqrt(np.maximum(var, 1e-12))
        return mu, sigma

    def sample_weights(self, rng: random.Random) -> np.ndarray:
        """One draw theta_s ~ N(theta_mean, noise_var * A_inv) -- the
        finite-dimensional stand-in for "sample a function ˜f ~ GP" (paper
        Sec 4.1). Called once per Monte-Carlo sample, each independent."""
        L = _safe_cholesky(self.noise_var * self.A_inv)
        z = _rng_array(rng, len(self.theta_mean), "normal")
        return self.theta_mean + L @ z

    def evaluate(self, theta: np.ndarray, X: np.ndarray) -> np.ndarray:
        """˜f(x) = phi(x)^T theta for a previously-sampled weight vector,
        de-standardized back into original (ASI or speedup) units."""
        return self.y_mean + self.y_std * (self._phi(X) @ theta)


def _median_heuristic_lengthscale(X: np.ndarray, fallback: float = 1.0) -> float:
    """Median-heuristic kernel lengthscale (Gretton et al.'s standard default
    for RBF-kernel methods): l^2 = (median squared pairwise distance) / 2, so
    a "typical" pair of points sits at kernel value exp(-1) ~= 0.37. This
    replaces a naive sqrt(number of one-hot columns) guess, which turned out
    to badly overestimate the right scale for this encoding: under it, two
    literally unrelated random configurations already carried ~0.85 kernel
    similarity (measured on this project's real PARAM_SPACE), meaning the GP
    could barely distinguish any two candidates -- both mu_j(x) and
    sigma_j(x) went nearly flat across the whole candidate pool, so the
    acquisition function had almost nothing to rank on. Recomputed from the
    real training data every iteration (rather than fixed once from feature
    count alone) so it adapts as more points are evaluated."""
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
    """Fit an _RFFModel to (X, y): draw a fresh random feature basis (W, b)
    from `rng`, standardize y to zero-mean/unit-variance (so a single fixed
    noise_var/lengthscale pair is sensible whether the objective is ASI or
    speedup, which live on very different natural scales), then compute the
    closed-form Bayesian-linear-regression posterior over theta in that
    feature space (Rasmussen & Williams, "Gaussian Processes for Machine
    Learning", eq. 2.8-2.12's weight-space view of GP regression -- exact,
    given phi, rather than an approximation in its own right)."""
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


# ---------------------------------------------------------------------------
# Acquisition function (eq. 4.13): output space entropy search.
# ---------------------------------------------------------------------------

@dataclass
class _Sample:
    """Lightweight stand-in for DesignPoint carrying just the two attributes
    greedy.dominates()/update_pareto_front() actually read -- lets the
    "cheap MO solver" step (paper Sec 4.1, step 1) reuse that exact same
    dominance logic on *sampled* (not real) objective values, instead of
    reimplementing non-domination filtering here."""
    asi: float
    speedup: float


def _sample_pareto_front(asi_values: np.ndarray, speedup_values: np.ndarray) -> tuple[float, float]:
    """One Monte-Carlo sample's Pareto front, found by brute-force
    non-domination filtering over the whole candidate pool (paper Sec 4.1's
    "cheap multi-objective optimization", normally solved with NSGA-II --
    here the "input space" being optimized over *is* the finite candidate
    pool itself, so exhaustive filtering finds exactly the Pareto front
    NSGA-II would converge to, for free). Returns (y*_asi, y*_speedup): the
    per-objective maxima across that sample's Pareto front (eq. 4.9)."""
    samples = [_Sample(a, s) for a, s in zip(asi_values, speedup_values)]
    front = update_pareto_front([], samples)
    return max(p.asi for p in front), max(p.speedup for p in front)


def _entropy_term(gamma: np.ndarray) -> np.ndarray:
    """Per-candidate, per-sample summand of MESMO's acquisition function
    (eq. 4.13): gamma*phi(gamma)/(2*Phi(gamma)) - ln(Phi(gamma)) -- the
    truncated-Gaussian entropy correction left over once H(y|D,x) and
    H(y|D,x,Y*_s)'s common (1+ln(2*pi))/2 and ln(sigma_j(x)) terms cancel
    (see the paper's eq. 4.7/4.12 -> 4.13 derivation). Phi is clamped away
    from 0 since gamma -> -inf (a sample Pareto front's y* far below this
    candidate's predicted mean) would otherwise blow the ratio/log up."""
    cdf = np.clip(norm.cdf(gamma), 1e-12, 1.0)
    pdf = norm.pdf(gamma)
    return gamma * pdf / (2.0 * cdf) - np.log(cdf)


def _acquisition_scores(
    pool_X: np.ndarray, model_asi: _RFFModel, model_speedup: _RFFModel,
    num_mc_samples: int, rng: random.Random,
) -> np.ndarray:
    """MESMO's acquisition function (eq. 4.13) evaluated at every row of
    pool_X: the average, over `num_mc_samples` independent posterior
    function samples, of the truncated-Gaussian entropy term for each
    objective. See this module's docstring / explore_pareto_front_mesmo()
    for the full per-sample recipe (draw ˜f_asi, ˜f_speedup -> their sample
    Pareto front -> gamma)."""
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


# ---------------------------------------------------------------------------
# Hypervolume-based convergence check (mirrors spea2._has_converged --
# duplicated for the same reason _random_entity/_modified_params are above).
# hypervolume() itself lives in greedy.py so every strategy shares one
# definition.
# ---------------------------------------------------------------------------

def _has_converged(hv_history: list[float], patience: int, rel_tol: float = 1e-3) -> bool:
    """True if the best hypervolume seen in the last `patience` iterations
    hasn't meaningfully exceeded the best hypervolume from everything before
    that window -- i.e. the Pareto frontier has stopped improving."""
    if len(hv_history) <= patience:
        return False
    best_before = max(hv_history[:-patience])
    recent_best = max(hv_history[-patience:])
    return recent_best <= best_before * (1 + rel_tol)


# ---------------------------------------------------------------------------
# Resumable state
# ---------------------------------------------------------------------------

@dataclass
class MesmoSearchState:
    """Resumable snapshot of an in-progress MESMO search, checkpointed to JSON."""
    STRATEGY: ClassVar[str] = "mesmo"

    reference_config: str
    benchmarks: dict[str, list[str]]
    alpha: float
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
    param_space: dict[str, list]

    def matches(self, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> bool:
        """Also checked against the live PARAM_SPACE: the GP surrogate's
        feature encoding (_feature_names) is derived directly from
        PARAM_SPACE, so a resumed search whose PARAM_SPACE has since changed
        (e.g. a different --preeval-* pruning) would otherwise score
        candidates against a stale/mismatched feature space -- must restart
        from iteration 0 instead of resuming."""
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

    def save(self, outputdir: Path) -> None:
        write_json_atomic(state_path(outputdir), self.to_dict())

    @classmethod
    def load(cls, outputdir: Path) -> "MesmoSearchState | None":
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

def _evaluate_candidate(
    params: dict[str, Any], out: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]],
    baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], prefix: str,
) -> tuple[DesignPoint | None, bool, int]:
    point, ran, invocations = evaluate_point(params, _modified_params(params), out, reference_config, sniper, benchmarks, baseline, alpha, global_cache)
    if point is not None:
        print_evaluated_point(params, point, prefix=prefix)
    return point, ran, invocations


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
) -> list[DesignPoint]:
    """
    MESMO (Max-value Entropy Search for Multi-objective Optimization,
    Belakaria, Deshwal & Doppa 2019/2021) Bayesian-optimization exploration
    of the ASI versus speedup design space.

    Per iteration:
      1. Fit one random-Fourier-features GP surrogate per objective (ASI,
         speedup) to every real (params -> DesignPoint) evaluation seen so
         far -- both the closed-form posterior mean/variance used directly
         below, and (by drawing a fresh weight vector per Monte-Carlo
         sample) a cheap way to sample whole candidate functions from that
         same posterior. The kernel lengthscale is re-derived from the real
         training data every iteration via the median-heuristic (see
         _median_heuristic_lengthscale) unless `gp_lengthscale` fixes it.
      2. Draw a fresh candidate pool of not-yet-evaluated configurations
         (standing in for the argmax's search over the whole combinatorial
         PARAM_SPACE, the same way SPEA2's populations do) -- part of it
         drawn as local neighbors of the current Pareto front, part of it
         fresh globally-random draws (see _candidate_pool).
      3. For each of `num_mc_samples` draws: sample one function per
         objective, find its Pareto front *over the candidate pool* (the
         paper's "cheap multi-objective optimization", solved here by
         brute-force non-domination filtering since the pool already stands
         in for the input space), and read off each objective's maximum
         across that sample Pareto front (y*_asi, y*_speedup).
      4. Score every pool candidate with MESMO's acquisition function (the
         average, over samples, of a truncated-Gaussian entropy term per
         objective -- eq. 4.13) and evaluate the top `batch_size` of them
         for real.
      5. Update the running Pareto front (over every real evaluation, not
         just this iteration's batch) and hypervolume history.

    Termination: `max_iterations` iterations (or an exhausted candidate
    pool), matching the paper's Algorithm 1, which runs for a fixed budget
    with no plateau-detection heuristic. `hv_patience` is an optional,
    not-paper-prescribed opt-in: if set, stops early once the Pareto
    front's hypervolume hasn't meaningfully improved for that many
    consecutive iterations. It defaults to None (disabled) rather than
    reusing spea2's aggressive default, because a spea2 *generation*
    evaluates dozens of points per hypervolume update while a mesmo
    *iteration* evaluates as few as one (batch_size=1) -- a short patience
    window would trigger on ordinary exploration noise long before the
    surrogate has seen enough real data to be useful.

    batch_size > 1 evaluates the top-`batch_size` acquisition-ranked
    candidates per iteration instead of re-fitting the GP between each --
    a practical (not paper-prescribed) concession to how expensive each real
    Sniper run is, exactly like spea2 evaluating whole populations per
    generation rather than one entity at a time.

    initial_cache seeds global_cache on a fresh (non-resumed) start -- e.g.
    with screening.screen_param_space's cache, so the baseline and any
    already-evaluated points it found are reused instead of re-run. Ignored
    when resuming a saved search, which already has its own global_cache.
    """
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
            # Pre-evaluation screening (cli.py's --preeval-samples, plumbed in
            # here as initial_cache) already spent real Sniper runs on these
            # configurations -- reuse them as MESMO's initial design instead
            # of drawing num_initial_points fresh random configurations that
            # would just re-derive the same kind of information at additional
            # simulation cost. num_initial_points itself is ignored in this
            # branch, same as spea2's seed_entities (see its docstring): an
            # "initial design" only exists to seed the first GP fit, and
            # screening already provides a far richer, already-paid-for one.
            evaluated: list[DesignPoint] = [baseline] + preeval_points
            runs_this_iter = 0
            invocations_this_iter = 0
            print(f"=== Initial design: baseline + {len(preeval_points)} pre-evaluation "
                  f"screening point{'s' if len(preeval_points) != 1 else ''} (reused, no new simulations) ===")
        else:
            print(f"=== Initial design ({num_initial_points} points: baseline + "
                  f"{num_initial_points - 1} random configurations) ===")
            initial_params = [dict(DEFAULTS)] + [_random_entity(rng) for _ in range(num_initial_points - 1)]
            evaluated = []
            runs_this_iter = 0
            invocations_this_iter = 0
            for idx, params in enumerate(initial_params):
                out = outputdir / f"init{idx}"
                point, ran, invocations = _evaluate_candidate(
                    params, out, reference_config, sniper, benchmarks, baseline, alpha, global_cache,
                    prefix="[mesmo] ",
                )
                runs_this_iter += ran
                invocations_this_iter += invocations
                if point is not None:
                    evaluated.append(point)

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
        evaluated = []
        runs_this_iter = 0
        invocations_this_iter = 0
        for rank, idx in enumerate(chosen):
            params = pool_params[int(idx)]
            out = outputdir / f"iter{iteration}_cand{rank}"
            point, ran, invocations = _evaluate_candidate(
                params, out, reference_config, sniper, state.benchmarks, state.baseline, alpha, state.global_cache,
                prefix="[mesmo] ",
            )
            runs_this_iter += ran
            invocations_this_iter += invocations
            if point is not None:
                evaluated.append(point)
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
        print()

        state.iteration = iteration
        state.rng_state = rng_state_to_json(rng)
        state.save(outputdir)

        if hv_patience is not None and _has_converged(state.hv_history, hv_patience):
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
