# asi_framework Method Reference

Every function/method defined in each module, generated from the source (signatures via `ast`); the quoted text under an entry is that entry's own docstring, included only when one exists.

## Files

- [`config.py`](#configpy)
- [`config_builder.py`](#config_builderpy)
- [`models.py`](#modelspy)
- [`runner.py`](#runnerpy)
- [`state.py`](#statepy)
- [`plot.py`](#plotpy)
- [`metrics.py`](#metricspy)
- [`display.py`](#displaypy)
- [`search_ops.py`](#search_opspy)
- [`evaluation.py`](#evaluationpy)
- [`greedy.py`](#greedypy)
- [`spea2.py`](#spea2py)
- [`mesmo.py`](#mesmopy)
- [`hybrid.py`](#hybridpy)
- [`screening.py`](#screeningpy)
- [`titan_batch.py`](#titan_batchpy)
- [`strategies.py`](#strategiespy)
- [`cli.py`](#clipy)
- [`__init__.py`](#__init__py)

---

## `config.py`

### `active_params`

```python
def active_params(param_space: dict[str, list], bp_type: str) -> set[str]
```

> Keys of param_space meaningful for the given branch_predictor_type.


---

## `config_builder.py`

> Translates a params dict into Sniper command-line override flags.

### `build_runtime_config`

```python
def build_runtime_config(reference_config: str, *, core_type: Optional[str] = DEFAULT_CORE_TYPE, frequency: Optional[float] = None, logical_cpus: Optional[int] = None, l1i_size: Optional[int] = None, l1i_assoc: Optional[int] = None, l1d_size: Optional[int] = None, l1d_assoc: Optional[int] = None, l2_size: Optional[int] = None, l2_assoc: Optional[int] = None, l3_size: Optional[int] = None, l3_assoc: Optional[int] = None, branch_predictor_type: Optional[str] = DEFAULT_BRANCH_PREDICTOR_TYPE, branch_predictor_size: Optional[int] = None, num_history_registers: Optional[int] = None, rob_outstanding_loads: Optional[int] = None, rob_outstanding_stores: Optional[int] = None, **kwargs) -> List[str]
```

> Turns a params dict into Sniper -c override flags; None values fall
> through to reference_config's own value.


---

## `models.py`

### class `DesignPoint`

`@dataclass` 

> One evaluated design point; area/peak_power/time are means across
> benchmarks, per_benchmark keeps each benchmark's own values.


---

## `runner.py`

### `run`

```python
def run(reference_config: str, sniper: Path, outputdir: Path, cmd: list[str], design_knobs: dict = None) -> tuple[float, float, float]
```

> Run the Sniper simulator and return (area_mm2, peak_power_W, time_ns).


### `parse_sniper_output`

```python
def parse_sniper_output(outputdir: Path, fail = None) -> tuple[float, float, float]
```

> Parses a completed Sniper/McPAT output directory into
> (area_mm2, peak_power_W, time_ns). fail: optional (exc_cls, msg) ->
> NoReturn callback for extra context on failure; defaults to raise.


---

## `state.py`

> Shared checkpoint/serialization primitives used by every search
> strategy's resumable-state dataclass

### class `SearchStateBase`

`@dataclass` 

> Common resumable-state contract shared by every search strategy's
> checkpoint dataclass (GreedySearchState, MesmoSearchState,
> Spea2SearchState): identity check against the run's config/benchmarks/
> alpha/param-space, and save/load through state_path()'s JSON file.
> Subclasses set STRATEGY and implement to_dict()/from_dict() for their
> own (differing) set of fields.

#### `matches`

```python
def matches(self, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> bool
```


#### `_current_param_space`

`@staticmethod` 

```python
def _current_param_space() -> dict[str, list]
```

> Live PARAM_SPACE to compare a loaded checkpoint's param_space
> against. Overridden per strategy module (each returns that module's
> own PARAM_SPACE binding) so cli.py's pre-evaluation-screening
> PARAM_SPACE monkeypatch (greedy.PARAM_SPACE = pruned_param_space,
> etc.) is respected instead of always reading config.py's original.


#### `to_dict`

```python
def to_dict(self) -> dict
```


#### `from_dict`

`@classmethod` 

```python
def from_dict(cls, d: dict) -> 'SearchStateBase'
```


#### `save`

```python
def save(self, outputdir: Path) -> None
```


#### `load`

`@classmethod` 

```python
def load(cls, outputdir: Path) -> 'SearchStateBase | None'
```



### `state_path`

```python
def state_path(outputdir: Path) -> Path
```


### `point_to_dict`

```python
def point_to_dict(p: DesignPoint) -> dict
```


### `point_from_dict`

```python
def point_from_dict(d: dict) -> DesignPoint
```


### `write_json_atomic`

```python
def write_json_atomic(path: Path, data: dict) -> None
```


### `read_raw_state`

```python
def read_raw_state(outputdir: Path) -> dict | None
```


### `rng_state_to_json`

```python
def rng_state_to_json(rng) -> list
```


### `rng_state_from_json`

```python
def rng_state_from_json(data: list) -> tuple
```


### `cleanup_dirs`

```python
def cleanup_dirs(dirs: set[Path]) -> int
```


---

## `plot.py`

### `_asi_region_bounds`

```python
def _asi_region_bounds(speedups: list[float], asi_values: list[float]) -> tuple[float, float, float, float]
```


### `_draw_asi_regions`

```python
def _draw_asi_regions(ax, x_min: float, x_max: float, y_min: float, y_max: float) -> None
```

> Shared background for both plot functions: the ASI sustainability
> regions (Fig. 1 of the paper) and the (1,1) reference point.


### `_finish`

```python
def _finish(fig, ax, title: str, save_path: Path | None, show: bool) -> None
```


### `plot_pareto_front_on_asi`

```python
def plot_pareto_front_on_asi(front: list[DesignPoint], title: str = 'ASI Pareto Front', save_path: Path | None = None, show: bool = True) -> None
```

> Plot ASI sustainability regions with a single Pareto front overlaid.


### `plot_pareto_fronts_on_asi`

```python
def plot_pareto_fronts_on_asi(fronts: list[list[DesignPoint]], title: str = 'ASI Pareto Fronts', sequence_label: str = 'Generation', save_path: Path | None = None, show: bool = True) -> None
```

> Plot a sequence of Pareto fronts (e.g. one per generation), color-coded
> by position; the last front is drawn on top as large, solid points.


### `plot_hv_vs_simulations`

```python
def plot_hv_vs_simulations(sim_history: list[int], hv_history: list[float], size_history: list[int], title: str = 'Hypervolume & Pareto Front Size vs. Simulations', save_path: Path | None = None, show: bool = True, switch_sims: list[int] | None = None, switch_label: str = 'Strategy switch') -> None
```

> Two stacked step-plots vs. cumulative configurations evaluated:
> hypervolume on top, Pareto front size on the bottom. switch_sims (used by
> hybrid) draws a vertical line + annotation at each strategy handoff.


---

## `metrics.py`

> Pure math over DesignPoints: the ASI formula, Pareto dominance/front
> bookkeeping, hypervolume, and search-convergence detection. No I/O, no
> Sniper -- see evaluation.py for that.

### `calculate_asi`

```python
def calculate_asi(Ay: float, Ax: float, Py: float, Px: float, alpha: float) -> float
```


### `geomean`

```python
def geomean(values: list[float]) -> float
```


### `params_key`

```python
def params_key(params: dict[str, Any]) -> frozenset
```


### `dominates`

```python
def dominates(a: DesignPoint, b: DesignPoint) -> bool
```


### `update_pareto_front`

```python
def update_pareto_front(front: list[DesignPoint], points: list[DesignPoint]) -> list[DesignPoint]
```

> Non-dominated points from front + points, deduplicated by params.


### `hypervolume`

```python
def hypervolume(front: list[DesignPoint]) -> float
```

> 2D hypervolume of a maximizing Pareto front relative to the origin
> (ASI=0, speedup=0).


### `has_converged`

```python
def has_converged(hv_history: list[float], patience: int, rel_tol: float = 0.001) -> bool
```

> True if hypervolume hasn't meaningfully improved in the last patience
> iterations/generations. Shared convergence check for mesmo (iterations)
> and spea2 (generations).


---

## `display.py`

> Terminal-output formatting for design points and Pareto fronts.

### `fmt_params`

```python
def fmt_params(params: dict[str, Any]) -> str
```

> Renders a point's (possibly sparse) params dict for terminal output.
> branch_predictor_type is always shown; every other key only when it
> deviates from its default.


### `sustainability_label`

```python
def sustainability_label(asi: float, speedup: float) -> str
```


### `print_evaluated_point`

```python
def print_evaluated_point(params: dict[str, Any], point: DesignPoint, prefix: str = '') -> None
```


### `print_pareto_table`

```python
def print_pareto_table(pareto_set: list[DesignPoint]) -> None
```


---

## `search_ops.py`

> Config-space operations shared by the search strategies: generating a
> random fully-specified configuration, mutating one into a neighbor, and
> diffing a configuration against DEFAULTS.

### `random_entity`

```python
def random_entity(rng: random.Random, param_space: dict[str, list]) -> dict[str, Any]
```

> A fully-specified configuration: one value per param_space parameter
> relevant to the randomly chosen branch predictor type. Shared by
> mesmo.py, spea2.py and screening.py's initial-design/random-sampling
> code, all of which always call this with the full PARAM_SPACE.


### `random_variant`

```python
def random_variant(entity: dict[str, Any], rng: random.Random, param_space: dict[str, list]) -> dict[str, Any]
```

> One active parameter of entity reassigned to a new candidate value --
> spea2's mutation operator and mesmo's neighbor-generation step for local
> candidate-pool sampling are the same operation under different names.
> Takes param_space explicitly (like random_entity) rather than reading a
> module-level PARAM_SPACE, since callers may have their own (possibly
> pre-evaluation-pruned) PARAM_SPACE binding -- see cli.py's screening step.


### `modified_params`

```python
def modified_params(params: dict[str, Any]) -> set[str]
```


---

## `evaluation.py`

> Running a configuration through Sniper (or reading it back from the
> global cache) and turning the raw measurements into a DesignPoint. Shared
> by every search strategy plus screening.py and titan_batch.py's batch path.

### `cached_point`

```python
def cached_point(key: frozenset, modified_params: set[str], global_cache: dict[frozenset, DesignPoint]) -> DesignPoint | None
```

> Reconstructs a DesignPoint from a global_cache hit with this call's own
> modified_params (which vary per caller even for the same params key).
> Shared by evaluate_point() and titan_batch.py's batch evaluator.


### `finalize_point`

```python
def finalize_point(params: dict[str, Any], modified_params: set[str], output_path: Path, per_benchmark: dict[str, dict[str, float]], areas: list[float], powers: list[float], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], key: frozenset) -> DesignPoint
```

> Aggregates one fully-measured point's per-benchmark (area, power, time)
> into a DesignPoint (mean area/power, ASI, geomean speedup) and caches it.
> Shared by evaluate_point() (local runs) and titan_batch.py (Titan-collected
> runs), so both paths produce identical DesignPoints from the same raw
> measurements.


### `evaluate_point`

```python
def evaluate_point(params: dict[str, Any], modified_params: set[str], output_path: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint]) -> tuple[DesignPoint | None, bool, int]
```

> Returns (point, ran_sniper, sniper_invocations), first checking the global_cache for a previously-evaluated point with the same params.


### `evaluate_and_print`

```python
def evaluate_and_print(params: dict[str, Any], out: Path, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], prefix: str) -> tuple[DesignPoint | None, bool, int]
```

> evaluate_point() + print_evaluated_point() on success -- shared by
> mesmo._evaluate_candidate and spea2._evaluate_entity, which were
> otherwise identical single-entity evaluation wrappers.


### `compute_baseline`

```python
def compute_baseline(reference_config: str, sniper: Path, baseline_dir: Path, benchmarks: dict[str, list[str]]) -> DesignPoint
```

> Runs every benchmark once with every parameter forced to DEFAULTS to
> get the reference-config baseline DesignPoint (asi=speedup=1.0).


---

## `greedy.py`

### class `GreedySearchState`

`@dataclass`  (inherits: SearchStateBase)

> Resumable snapshot of an in-progress greedy/sensitivity search, checkpointed to JSON.

#### `_current_param_space`

`@staticmethod` 

```python
def _current_param_space() -> dict[str, list]
```


#### `to_dict`

```python
def to_dict(self) -> dict
```


#### `from_dict`

`@classmethod` 

```python
def from_dict(cls, d: dict) -> 'GreedySearchState'
```



### `explore_pareto_front_with_sensitivity`

```python
def explore_pareto_front_with_sensitivity(reference_config: str, sniper: Path, outputdir: Path, benchmarks: dict[str, list[str]], alpha: float = DEFAULT_ALPHA, max_iterations: int = 5, initial_cache: dict[frozenset, DesignPoint] | None = None) -> list[DesignPoint]
```

> Iterative Pareto-front exploration with sensitivity-based parameter
> freezing. Initial_cache seeds
> global_cache on a fresh start, ignored when resuming.


---

## `spea2.py`

> COLE-style multi-objective evolutionary (SPEA2) search strategy.

### class `Spea2SearchState`

`@dataclass`  (inherits: SearchStateBase)

> Resumable snapshot of an in-progress SPEA2/COLE search, checkpointed to JSON.

#### `_current_param_space`

`@staticmethod` 

```python
def _current_param_space() -> dict[str, list]
```


#### `to_dict`

```python
def to_dict(self) -> dict
```


#### `from_dict`

`@classmethod` 

```python
def from_dict(cls, d: dict) -> 'Spea2SearchState'
```



### `_crossover`

```python
def _crossover(a: dict[str, Any], b: dict[str, Any], rng: random.Random) -> dict[str, Any]
```

> Uniform crossover: each always-relevant parameter independently comes
> from parent a or b; the resulting type's own knobs are filled in from
> whichever parent(s) used that type, or drawn fresh otherwise.


### `_normalized_objectives`

```python
def _normalized_objectives(points: list[DesignPoint]) -> dict[int, tuple[float, float]]
```

> id(point) -> (asi, speedup) min-max normalized to [0, 1] across `points`,
> so density/distance isn't skewed by ASI and speedup living on different scales.


### `_distance`

```python
def _distance(obj: dict[int, tuple[float, float]], a: DesignPoint, b: DesignPoint) -> float
```


### `_spea2_fitness`

```python
def _spea2_fitness(pool: list[DesignPoint]) -> tuple[dict[int, float], dict[int, int]]
```

> SPEA2 fitness assignment (Zitzler et al. 2001): raw fitness (dominance
> strength) plus a density term (distance to the k-th nearest neighbor) as
> a tiebreaker. Lower is better; raw==0 means non-dominated within `pool`.
> Returns (fitness, raw) keyed by id(point).


### `_truncate`

```python
def _truncate(nondominated: list[DesignPoint], size: int) -> list[DesignPoint]
```

> Repeatedly drop the most crowded point until size non-dominated
> points remain, so survivors spread out across the objective range.


### `_environmental_selection`

```python
def _environmental_selection(population: list[DesignPoint], archive: list[DesignPoint], archive_size: int) -> list[DesignPoint]
```

> Build the next archive from the current population + current archive:
> keep all non-dominated points; if there are too few, pad with the
> best-fitness dominated ones; if too many, truncate by crowding.


### `_binary_tournament`

```python
def _binary_tournament(archive: list[DesignPoint], fitness: dict[int, float], rng: random.Random) -> DesignPoint
```


### `_make_mating_pool`

```python
def _make_mating_pool(pop_idx: int, archives: list[list[DesignPoint]], pool_size: int, p_migration: float, rng: random.Random) -> list[DesignPoint]
```


### `_titan_config`

```python
def _titan_config(titan: bool, outputdir: Path, titan_benchmark_json: str | None, titan_dir: str | None, titan_host_dir: str | None, titan_sniper_mount: str, titan_benchmarks_mount: str, titan_poll_interval: float) -> dict[str, Any] | None
```

> None when titan=False -- callers fall back to local per-entity
> evaluation, unchanged.


### `_evaluate_populations`

```python
def _evaluate_populations(populations_params: list[list[dict[str, Any]]], out_prefix: str, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], outputdir: Path, titan_config: dict[str, Any] | None) -> tuple[list[list[DesignPoint]], int, int]
```

> Evaluates every entity across all populations for one generation --
> locally one at a time, or (titan_config given) as one titan_batch job.


### `_live_output_dirs`

```python
def _live_output_dirs(state: 'Spea2SearchState', baseline_dir: Path) -> set[Path]
```


### `explore_pareto_front_spea2`

```python
def explore_pareto_front_spea2(reference_config: str, sniper: Path, outputdir: Path, benchmarks: dict[str, list[str]], alpha: float = DEFAULT_ALPHA, max_iterations: int = 30, num_populations: int = 3, population_size: int = 20, archive_size: int = 10, p_mutation: float = 0.1, p_crossover: float = 0.9, p_migration: float = 0.1, patience: int = 5, seed: int = 0, initial_cache: dict[frozenset, DesignPoint] | None = None, seed_entities: list[dict[str, Any]] | None = None, preeval_runs: int = 0, preeval_invocations: int = 0, titan: bool = False, titan_benchmark_json: str | None = None, titan_dir: str | None = None, titan_host_dir: str | None = None, titan_sniper_mount: str = '/mnt/perflab/exascience/src/jaco_sniper', titan_benchmarks_mount: str = '/mnt/perflab/exascience/src/jaco_benchmarks', titan_poll_interval: float = 30.0) -> list[DesignPoint]
```

> COLE-style multi-objective evolutionary (SPEA2) exploration of the ASI
> versus speedup design space.
> 
> Generation 0's population depends on how this is called:
>   - hybrid + preeval: seeded from MESMO's final front (itself started
>     from the preeval cache) -- no random fill.
>   - hybrid only: seeded from MESMO's final front (grown from a random
>     initial design) -- no random fill.
>   - preeval only (no hybrid): seeded directly from the preeval cache's
>     points -- no random fill.
>   - neither: baseline + randomly generated entities.
> Either way, initial_cache primes global_cache so nothing gets
> re-simulated, and preeval_runs/preeval_invocations fold pre-spent Sniper
> cost into generation 0's own totals.


---

## `mesmo.py`

> MESMO (Max-value Entropy Search for Multi-objective Optimization)
> Bayesian-optimization search strategy; adapted from Belakaria, Deshwal &
> Doppa, "Max-value Entropy Search for Multi-Objective Bayesian Optimization"
> (NeurIPS'19 / JAIR'21).

### class `_RFFModel`

`@dataclass` 

> Random-Fourier-Features approximation of a single objective's GP
> posterior (Rahimi & Recht 2008).

#### `_phi`

```python
def _phi(self, X: np.ndarray) -> np.ndarray
```


#### `predict`

```python
def predict(self, X: np.ndarray) -> tuple[np.ndarray, np.ndarray]
```

> (mu, sigma) at every row of X, in original (ASI/speedup) units.


#### `sample_weights`

```python
def sample_weights(self, rng: random.Random) -> np.ndarray
```

> One draw theta ~ N(theta_mean, noise_var * A_inv).


#### `evaluate`

```python
def evaluate(self, theta: np.ndarray, X: np.ndarray) -> np.ndarray
```

> f(x) = phi(x)^T theta for a sampled weight vector.



### class `_Sample`

`@dataclass` 

> Lightweight stand-in for DesignPoint, just what dominates()/
> update_pareto_front() need to run on sampled (not real) objective values.


### class `MesmoSearchState`

`@dataclass`  (inherits: SearchStateBase)

> Resumable snapshot of an in-progress MESMO search, checkpointed to JSON.

#### `_current_param_space`

`@staticmethod` 

```python
def _current_param_space() -> dict[str, list]
```


#### `to_dict`

```python
def to_dict(self) -> dict
```


#### `from_dict`

`@classmethod` 

```python
def from_dict(cls, d: dict) -> 'MesmoSearchState'
```



### `_candidate_pool`

```python
def _candidate_pool(rng: random.Random, pool_size: int, exclude: set[frozenset], anchors: list[dict[str, Any]]) -> list[dict[str, Any]]
```

> Fresh pool of not-yet-evaluated configs to score this iteration --
> up to _LOCAL_POOL_FRACTION are neighbors of anchors (e.g. the current
> Pareto front), the rest fresh random draws. Falls back to pure random
> when anchors is empty. May return short if PARAM_SPACE is nearly exhausted.


### `_feature_names`

```python
def _feature_names(param_space: dict[str, list]) -> list[tuple[str, Any]]
```

> Ordered (param, value) pairs, one per non-default candidate value --
> a fixed column order for _encode()'s one-hot vectors.


### `_encode`

```python
def _encode(params: dict[str, Any], feature_names: list[tuple[str, Any]]) -> np.ndarray
```

> params -> one-hot vector over feature_names.


### `_encode_all`

```python
def _encode_all(points_params: list[dict[str, Any]], feature_names: list[tuple[str, Any]]) -> np.ndarray
```


### `_rng_array`

```python
def _rng_array(rng: random.Random, n: int, kind: str) -> np.ndarray
```


### `_safe_cholesky`

```python
def _safe_cholesky(cov: np.ndarray, jitter: float = 1e-10, max_tries: int = 5) -> np.ndarray
```

> Cholesky factor of cov, adding diagonal jitter if not quite PSD.


### `_median_heuristic_lengthscale`

```python
def _median_heuristic_lengthscale(X: np.ndarray, fallback: float = 1.0) -> float
```

> Median-heuristic kernel lengthscale: l^2 = (median squared pairwise
> distance) / 2, recomputed from real training data every iteration.


### `_fit_rff_model`

```python
def _fit_rff_model(X: np.ndarray, y: np.ndarray, num_features: int, lengthscale: float, noise_var: float, rng: random.Random) -> _RFFModel
```

> Fit an _RFFModel to (X, y): fresh random feature basis, y
> standardized to zero-mean/unit-variance, closed-form Bayesian linear
> regression posterior over theta in that feature space.


### `_sample_pareto_front`

```python
def _sample_pareto_front(asi_values: np.ndarray, speedup_values: np.ndarray) -> tuple[float, float]
```

> One Monte-Carlo sample's Pareto front (brute-force non-domination
> filtering over the candidate pool); returns each objective's maximum
> across that front.


### `_entropy_term`

```python
def _entropy_term(gamma: np.ndarray) -> np.ndarray
```

> Truncated-Gaussian entropy term of MESMO's acquisition function
> (Belakaria et al. eq. 4.13).


### `_acquisition_scores`

```python
def _acquisition_scores(pool_X: np.ndarray, model_asi: _RFFModel, model_speedup: _RFFModel, num_mc_samples: int, rng: random.Random) -> np.ndarray
```

> MESMO's acquisition function evaluated at every row of pool_X: mean
> over num_mc_samples posterior draws of each objective's entropy term.


### `_evaluate_candidates`

```python
def _evaluate_candidates(params_list: list[dict[str, Any]], out_prefix: str, reference_config: str, sniper: Path, benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], outputdir: Path, titan_config: dict[str, Any] | None, prefix: str) -> tuple[list[DesignPoint], int, int]
```

> Evaluates every candidate in params_list -- locally one at a time, or
> (titan_config given) as one titan_batch job. Mirrors
> spea2._evaluate_populations, flattened for mesmo's single list of
> candidates per step (initial design, or one iteration's batch).


### `explore_pareto_front_mesmo`

```python
def explore_pareto_front_mesmo(reference_config: str, sniper: Path, outputdir: Path, benchmarks: dict[str, list[str]], alpha: float = DEFAULT_ALPHA, max_iterations: int = 30, num_initial_points: int = 5, candidate_pool_size: int = 200, batch_size: int = 1, num_mc_samples: int = 10, gp_features: int = 250, gp_lengthscale: float | None = None, gp_noise: float = 0.0001, hv_patience: int | None = None, seed: int = 0, initial_cache: dict[frozenset, DesignPoint] | None = None, titan: bool = False, titan_benchmark_json: str | None = None, titan_dir: str | None = None, titan_host_dir: str | None = None, titan_sniper_mount: str = '/mnt/perflab/exascience/src/jaco_sniper', titan_benchmarks_mount: str = '/mnt/perflab/exascience/src/jaco_benchmarks', titan_poll_interval: float = 30.0) -> list[DesignPoint]
```

> MESMO Bayesian-optimization exploration of the ASI versus speedup
> design space. hv_patience defaults to None (disabled), unlike spea2's aggressive default, since a
> mesmo iteration can evaluate as few as one point (batch_size=1) -- too
> noisy a signal for a short patience window. initial_cache seeds
> global_cache on a fresh start, ignored when resuming. With titan=True,
> the initial design and each iteration's batch_size candidates are
> submitted as one titan_batch job instead of evaluated one at a time --
> batch_size=1 (the default) gains nothing from titan past the initial
> design, since there's only ever one candidate per iteration to batch.


---

## `hybrid.py`

> Hybrid strategy: MESMO until its hypervolume plateaus, then SPEA2 seeded
> from MESMO's final Pareto front.

### `explore_pareto_front_hybrid`

```python
def explore_pareto_front_hybrid(reference_config: str, sniper: Path, outputdir: Path, benchmarks: dict[str, list[str]], alpha: float = DEFAULT_ALPHA, max_iterations: int = 30, mesmo_max_iterations: int = 30, num_initial_points: int = 5, candidate_pool_size: int = 200, batch_size: int = 1, num_mc_samples: int = 10, gp_features: int = 250, gp_lengthscale: float | None = None, gp_noise: float = 0.0001, hv_patience: int | None = None, num_populations: int = 3, population_size: int = 20, archive_size: int = 10, p_mutation: float = 0.1, p_crossover: float = 0.9, p_migration: float = 0.1, patience: int = 5, seed: int = 0, initial_cache: dict[frozenset, DesignPoint] | None = None, titan: bool = False, titan_benchmark_json: str | None = None, titan_dir: str | None = None, titan_host_dir: str | None = None, titan_sniper_mount: str = '/mnt/perflab/exascience/src/jaco_sniper', titan_benchmarks_mount: str = '/mnt/perflab/exascience/src/jaco_benchmarks', titan_poll_interval: float = 30.0) -> list[DesignPoint]
```

> Hybrid MESMO -> SPEA2 exploration.
> titan (and the rest of the titan_* flags) are forwarded to both phases
> unchanged -- each phase's own outputdir (mesmo_phase/, spea2_phase/) keeps
> their Titan job submissions under separate host_destination_path trees.
> 
> If a previous invocation already reached the SPEA2 phase (a matching
> spea2_phase/ checkpoint exists), the MESMO phase is skipped entirely on
> resume rather than re-entered. MESMO's own hv_patience convergence isn't
> persisted in its checkpoint -- only the last completed iteration is --
> so resuming explore_pareto_front_mesmo after it already converged and
> handed off to SPEA2 would otherwise just run more (wasted) MESMO
> iterations before ever reaching the SPEA2 resume check below.


---

## `screening.py`

> Parameter-importance pre-screen (perceptron or Plackett-Burman), run once
> before any search strategy.

### `_encode_features`

```python
def _encode_features(params: dict[str, Any], param_space: dict[str, list]) -> dict[str, float]
```

> One binary feature per (param, non-default value) pair.


### `_train_perceptron`

```python
def _train_perceptron(rows: list[dict[str, float]], targets: list[float], rng: random.Random, epochs: int = 300, lr: float = 0.05, l2: float = 0.01) -> dict[str, float]
```

> Single linear unit trained online (delta rule + L2) to predict each
> sample's distance-from-baseline target from its one-hot features.


### `_is_prime`

```python
def _is_prime(n: int) -> bool
```


### `_next_pb_size`

```python
def _next_pb_size(min_factors: int, min_dummy: int = 2) -> int
```

> Smallest Plackett-Burman design size (x = q+1, q prime, q%4==3) that
> fits min_factors two-level factors plus min_dummy noise-reference columns.


### `_pb_base_design`

```python
def _pb_base_design(x: int) -> list[list[int]]
```

> Base (no foldover) Plackett-Burman design via the Paley construction
> of a Hadamard matrix of order x (q = x-1 prime, q%4==3).


### `_pb_design_with_foldover`

```python
def _pb_design_with_foldover(x: int) -> list[list[int]]
```


### `_pb_entities`

```python
def _pb_entities(param_space: dict[str, list]) -> tuple[list[dict[str, Any]], dict[str, tuple[Any, Any]], list[list[int]], int]
```

> One config per Plackett-Burman-with-foldover design row, min/max per
> parameter (branch_predictor_type and its own knobs excluded, left at
> default). Returns (entities, levels, design, num_real_columns) -- levels
> records which value was which level; design columns >= num_real_columns
> are dummy (noise-reference) columns.


### `_screen_cache_path`

```python
def _screen_cache_path(outputdir: Path) -> Path
```


### `_screen_identity`

```python
def _screen_identity(reference_config: str, benchmarks: dict[str, list[str]], alpha: float, method: str, num_samples: int, keep_threshold: float, seed: int) -> dict
```

> Identifies a screening run for cache matching. plackett_burman is
> deterministic given (config, benchmarks, alpha, PARAM_SPACE) alone;
> perceptron's result also depends on num_samples/keep_threshold/seed.


### `_read_screen_cache_file`

```python
def _read_screen_cache_file(outputdir: Path) -> dict | None
```


### `_load_screen_cache`

```python
def _load_screen_cache(outputdir: Path, identity: dict) -> tuple[dict[str, list], dict[frozenset, DesignPoint]] | None
```


### `_save_screen_cache`

```python
def _save_screen_cache(outputdir: Path, identity: dict, pruned_param_space: dict[str, list], global_cache: dict[frozenset, DesignPoint]) -> None
```


### `load_screening_cache`

```python
def load_screening_cache(outputdir: Path, reference_config: str, benchmarks: dict[str, list[str]], alpha: float) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]
```

> Loads a screen_param_space() cache without re-screening (backs
> --preeval-cache). Raises FileNotFoundError/ValueError if no cache
> matching config/benchmarks/alpha/PARAM_SPACE exists.


### `screen_param_space`

```python
def screen_param_space(reference_config: str, sniper: Path, outputdir: Path, benchmarks: dict[str, list[str]], alpha: float, num_samples: int, keep_threshold: float = 0.1, seed: int = 0, method: str = 'perceptron') -> tuple[dict[str, list], dict[frozenset, DesignPoint]]
```

> Returns (pruned_param_space, global_cache) -- pruned_param_space
> reduces every unimportant parameter to its default-only value; method
> is "perceptron" or "plackett_burman". Cached to disk and reused on a
> later call with matching identity (_screen_identity).


### `_screen_plackett_burman`

```python
def _screen_plackett_burman(param_space: dict[str, list], pb_levels: dict[str, tuple[Any, Any]], pb_effects_asi: dict[str, float], pb_effects_speedup: dict[str, float], pb_dummy_effects_asi: list[float], pb_dummy_effects_speedup: list[float], pb_successes: int, global_cache: dict[frozenset, DesignPoint]) -> tuple[dict[str, list], dict[frozenset, DesignPoint]]
```

> Keeps a parameter if either objective's |effect| beats that
> objective's own dummy-column noise ceiling (Yi, Lilja & Hawkins Table 6).
> branch_predictor_type and its own knobs are left untouched (unpruned).


---

## `titan_batch.py`

> Builds and runs a titan_controller batch experiment for a whole set of
> design points at once, instead of one local Sniper run at a time. See
> titan_controller/RUNBOOK.md's "Submitting a batch of ASI design points".

### `_entity_overrides`

```python
def _entity_overrides(entity: dict[str, Any], reference_config: str) -> str
```

> The exact Sniper override-flag string for one entity -- shared by
> entities_to_titan_experiment() (to build the submitted JSON) and
> evaluate_batch() (to look its result back up by content afterward, since
> duplicate entities make positional matching against Titan's own
> simulator_parameters order unreliable).


### `entities_to_titan_experiment`

```python
def entities_to_titan_experiment(entities: list[dict[str, Any]], reference_config: str, benchmark_json_path: str, host_destination_path: str, *, job_name: str = 'asi_batch', sniper_mount: str = '/mnt/perflab/exascience/src/jaco_sniper', benchmarks_mount: str = '/mnt/perflab/exascience/src/jaco_benchmarks', core_per_experiment: int = 1, mem_per_core: int = 2048, vm_name: str = 'sniper2404') -> dict[str, Any]
```

> Build a titan_controller experiment dict for one batch of (possibly
> sparse) params dicts, ready to json.dump() and hand to --submit. Give
> each batch its own host_destination_path -- see RUNBOOK.md.


### `_run_titan`

```python
def _run_titan(titan_controller_dir: Path, args: list[str]) -> str
```


### `evaluate_batch`

```python
def evaluate_batch(entities: list[tuple[dict[str, Any], Path, set[str]]], reference_config: str, benchmarks: dict[str, list[str]], baseline: DesignPoint, alpha: float, global_cache: dict[frozenset, DesignPoint], *, titan_controller_dir: Path, benchmark_json_path: str, host_destination_path: Path, sniper_mount: str = '/mnt/perflab/exascience/src/jaco_sniper', benchmarks_mount: str = '/mnt/perflab/exascience/src/jaco_benchmarks', poll_interval: float = 30.0, job_name: str = 'asi_batch') -> list[tuple[DesignPoint | None, bool, int]]
```

> Evaluates a whole batch of (params, output_path, modified_params)
> entities as one Titan job. Cache hits never touch Titan; the rest are
> submitted together, polled via --list job, collected, and parsed back
> with finalize_point() -- identical math to the local evaluate_point()
> path. Returns (point, ran, invocations) per entity, input order.


---

## `strategies.py`

> Registry of available search strategies

### class `StrategySpec`

`@dataclass(frozen=True)` 


---

## `cli.py`

### class `_Tee`

> Write to multiple streams simultaneously.

#### `__init__`

```python
def __init__(self, *streams)
```


#### `write`

```python
def write(self, data: str) -> None
```


#### `flush`

```python
def flush(self) -> None
```


#### `isatty`

```python
def isatty(self) -> bool
```



### `_tee_stdout`

`@contextlib.contextmanager` 

```python
def _tee_stdout(path: Path)
```


### `build_parser`

```python
def build_parser() -> argparse.ArgumentParser
```


### `_benchmark_name`

```python
def _benchmark_name(cmd: list[str], used: set[str]) -> str
```

> Derive a display/output-dir name for a benchmark command from its
> executable's parent directory (matching the benchmarks/<NAME>/bench
> layout), de-duplicating if the same name would be used twice.


### `_format_elapsed`

```python
def _format_elapsed(seconds: float) -> str
```


### `main`

```python
def main() -> int
```


---

## `__init__.py`

_No functions or classes defined at module level._

