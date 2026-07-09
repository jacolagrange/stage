# ASI Framework

Design-space exploration for processor microarchitectures, guided by the
**Architectural Sustainability Indicator (ASI)**. Given a reference Sniper
config and one or more benchmarks, the framework searches over a large,
discrete space of microarchitectural parameters (core type, cache hierarchy,
branch predictor, out-of-order core knobs, ...) for configurations that
improve performance (speedup) without a proportional cost in chip area and
power, using [Sniper](https://snipersim.org/) for timing simulation and
[McPAT](https://github.com/HewlettPackard/mcpat) (invoked by Sniper via
`--power`) for area/power estimation.

Simulating every possible configuration is not feasible — one design point
already costs a full Sniper run plus a McPAT estimate, and the parameter
space grows multiplicatively with every added knob. The framework therefore
implements several independent **search strategies** that each try to find a
good Pareto front (ASI vs. speedup) while spending as few real simulations as
possible. Pick one via `--strategy`. Currently available: `greedy`
(sensitivity-based hill climbing), `spea2` (an evolutionary algorithm),
`mesmo` (Bayesian optimization), and `hybrid` — the one exception to
"alternatives, not a pipeline": it *is* a small pipeline, running `mesmo`
until its hypervolume plateaus and then `spea2` seeded from `mesmo`'s final
Pareto front instead of a random one. More strategies are planned; see
[Extending: adding a new strategy](#extending-adding-a-new-strategy).

## Table of contents

- [The ASI metric](#the-asi-metric)
- [Usage](#usage)
  - [General flags](#general-flags)
  - [`spea2` flags](#spea2-flags)
  - [`mesmo` flags](#mesmo-flags)
  - [`hybrid` flags](#hybrid-flags)
  - [Pre-evaluation screening flags](#pre-evaluation-screening-flags)
  - [Output layout](#output-layout)
- [Shared building blocks](#shared-building-blocks)
- [Hypervolume-vs-simulations plot](#hypervolume-vs-simulations-plot)
- [Strategy: `greedy`](#strategy-greedy-sensitivity-based-hill-climbing)
- [Strategy: `spea2`](#strategy-spea2-cole-style-evolutionary-search)
- [Strategy: `mesmo`](#strategy-mesmo-bayesian-optimization)
- [Strategy: `hybrid`](#strategy-hybrid-mesmo--spea2)
- [Optional pre-processing: parameter screening](#optional-pre-processing-parameter-screening)
- [Extending: adding a new strategy](#extending-adding-a-new-strategy)

## The ASI metric

Every evaluated configuration is compared against a **baseline**: every
parameter forced to its `DEFAULTS` value (`config.py`). For a candidate point
with area `Ax` and peak power `Px`, against baseline area `Ay` and peak power
`Py`:

```
ASI = (1 - alpha * (Ax / Ay)) / ((1 - alpha) * (Px / Py))
```

`alpha` (`--alpha`, default 0.5) is the trade-off weight between the
*embodied* cost of a design (chip area, a proxy for manufacturing footprint)
and its *operational* cost (power draw while running): `alpha=0` weighs
purely on power, `alpha=1` weighs purely on area. `ASI=1` at the baseline
itself.

**Speedup** is `baseline.time / point.time` per benchmark, geometric-meaned
across benchmarks when more than one is given (`geomean()` in `greedy.py`) —
the same aggregation SPEC-style suites use, so one workload can't silently
dominate the score.

Every point is classified into one of the ASI paper's sustainability regions
(`sustainability_label()` in `greedy.py`, plotted as background bands in
`plot.py`) by comparing `ASI` against `1/speedup`:

| Region              | Condition                          |
|---------------------|-------------------------------------|
| Strongly Sustainable | both ASI and speedup improve enough that ASI clears `max(1, 1/speedup)` |
| Weakly Sustainable (fewer resources / FW) | ASI < 1 but still above the lower bound |
| Weakly Sustainable (faster / FT)          | speedup > 1 but ASI still above the lower bound |
| Unsustainable        | ASI falls below `min(1, 1/speedup)` |
| Reference             | exactly the baseline (1, 1)         |

## Usage

```
python asi.py --config <reference.cfg> --strategy {greedy,spea2,mesmo,hybrid} \
    [--sniper PATH] [--outputdir DIR] [--alpha A] [--iterations N] \
    [--log [PATH]] [--save-plot [PATH]] \
    [strategy-specific flags...] [pre-evaluation flags...] \
    -- <benchmark command> [-- <another benchmark command> ...]
```

One or more benchmark commands **must** follow `--`; repeat `-- ./other_bench
...` to search across several benchmarks at once (their per-benchmark
results are geomean'd into one speedup, see [above](#the-asi-metric)). Each
benchmark's display name is derived from its executable's parent directory
(matching the `libs/benchmarks/<NAME>/bench` layout).

Example:

```
python asi.py --config nehalem.cfg --strategy spea2 --log --save-plot \
    -- ./libs/benchmarks/MM/bench arg1
```

### General flags

| Flag | Default | Meaning |
|---|---|---|
| `--config` (required) | — | Reference Sniper config file. |
| `--sniper` | `snipersim/run-sniper` | Path to `run-sniper`. |
| `--outputdir`, `-d` | `asi/asi-output` | Where Sniper/McPAT outputs, plots, and the resumable search-state JSON are written. |
| `--alpha` | `0.5` | ASI weight (0 = operational/power, 1 = embodied/area). |
| `--strategy` | `greedy` | `greedy`, `spea2`, `mesmo`, or `hybrid`. |
| `--iterations` | strategy's own default | Iterations (greedy), generations (spea2, and hybrid's spea2 phase), or BO iterations (mesmo). Leaving it unset lets the chosen strategy pick its own default (5 for greedy, 30 for spea2/mesmo/hybrid) instead of forcing one number on all of them. For hybrid's mesmo-phase iteration budget, see `--mesmo-phase-iterations` in [`hybrid` flags](#hybrid-flags). |
| `--log [PATH]` | off | Tee terminal output to `PATH` (default `outputdir/run.log`). |
| `--save-plot [PATH]` | off | Save the final Pareto-front plot to `PATH` (default `outputdir/pareto.png`). |

### `spea2` flags

| Flag | Default | Meaning |
|---|---|---|
| `--populations` | 3 | Number of independent SPEA2 populations. |
| `--population-size` | 20 | Entities per population. |
| `--archive-size` | 10 | Pareto-archive size per population. |
| `--p-mutation` | 0.10 | Per-child probability of mutating one parameter. |
| `--p-crossover` | 0.90 | Per-child probability of crossover (vs. a plain copy). |
| `--p-migration` | 0.10 | Per mating-pool slot, probability of drawing from *another* population's archive instead of its own. |
| `--patience` | 5 | Stop after this many generations without hypervolume improvement. |
| `--seed` | 0 | RNG seed (also used by mesmo). |

### `mesmo` flags

| Flag | Default | Meaning |
|---|---|---|
| `--num-initial-points` | 5 | Baseline + this-minus-one random configs evaluated up front to seed the GP surrogate. |
| `--candidate-pool-size` | 200 | Fresh candidates scored by the acquisition function each iteration. |
| `--batch-size` | 1 | Top-ranked candidates evaluated per iteration before refitting the GP (1 = sequential MESMO as in the paper). |
| `--mc-samples` | 10 | Monte-Carlo samples used to estimate the acquisition function per candidate. |
| `--gp-features` | 250 | Random Fourier features approximating each objective's GP. |
| `--gp-lengthscale` | median-heuristic | Fixes the GP kernel lengthscale instead of re-deriving it from real data each iteration. |
| `--gp-noise` | 1e-4 | GP observation noise variance (standardized units). |
| `--mesmo-patience` | unset (disabled) | Stop after this many iterations without hypervolume improvement. Off by default because one MESMO iteration can be a single evaluation — too noisy for a short patience window. |

### `hybrid` flags

`hybrid` is mesmo-then-spea2 (see [Strategy: `hybrid`](#strategy-hybrid-mesmo--spea2)), so it accepts *every* `spea2` and `mesmo` flag above (including `--seed`, shared by both phases) plus one flag of its own for its mesmo phase's iteration budget. `--iterations` (see [General flags](#general-flags)) configures its **spea2** phase's generations, matching plain `--strategy spea2`.

One difference from plain `mesmo`: `--mesmo-patience` still configures the mesmo phase's early-stop patience, but hybrid gives it a concrete default of **5** instead of mesmo's own "unset" default — detecting the hypervolume plateau is this phase's entire reason for existing, so hybrid can't leave it disabled the way a standalone `mesmo` run reasonably can.

| Flag | Default | Meaning |
|---|---|---|
| `--mesmo-phase-iterations` | 30 | Max MESMO iterations in the exploration phase before switching to SPEA2 — the phase usually stops earlier once `--mesmo-patience` (default 5 here) detects a plateau. |

### Pre-evaluation screening flags

Independent of `--strategy` — runs first if `--preeval-samples > 0`, and
prunes parameters judged unimportant to their default-only value before the
chosen strategy starts. See [Optional pre-processing](#optional-pre-processing-parameter-screening).

| Flag | Default | Meaning |
|---|---|---|
| `--preeval-samples` | 0 | 0 disables screening. With `--preeval-method perceptron`, this is the sample count; with `plackett_burman` it's just an on/off switch. |
| `--preeval-method` | `perceptron` | `perceptron` or `plackett_burman`. |
| `--preeval-threshold` | 0.1 | Keep a parameter only if its importance is at least this fraction of the most important parameter's (perceptron; also used by plackett_burman's branch-predictor one-at-a-time screen). |
| `--preeval-seed` | 0 | RNG seed for perceptron sampling (plackett_burman is deterministic). |

### Output layout

Under `--outputdir`:
- `baseline/` — the reference config's own Sniper/McPAT run.
- Per-strategy working directories for every evaluated point (e.g.
  `iter{N}_run{i}` for greedy, `gen{N}_pop{P}_ent{E}` for spea2,
  `init{i}` / `iter{N}_cand{rank}` for mesmo). Directories belonging to
  points that fall off the Pareto front are deleted as the search
  progresses (`cleanup_dirs()`), so disk usage stays bounded by the current
  front rather than every point ever tried.
- `search_state.json` — resumable checkpoint (see [Resumability](#resumability)); re-running the
  same command against the same `--outputdir` continues instead of
  restarting, as long as the reference config, benchmarks, alpha, and
  parameter space are unchanged.
- `pareto_history.png` / `pareto_final.png` — always written; `pareto.png`
  (or `--save-plot`'s path) only if `--save-plot` was passed.
- `hv_vs_sims.png` — always written: hypervolume and Pareto front size
  against the cumulative count of real Sniper simulations spent to reach
  them (see
  [Hypervolume-vs-simulations plot](#hypervolume-vs-simulations-plot)).

**`--strategy hybrid` is the exception**: instead of writing straight into
`--outputdir`, it creates two sub-directories, `mesmo_phase/` and
`spea2_phase/`, and runs an entire ordinary `mesmo` / `spea2` search inside
each — so everything above (`baseline/`, working directories,
`search_state.json`, the plots) exists once *per phase*, under
`outputdir/mesmo_phase/...` and `outputdir/spea2_phase/...` respectively,
each independently resumable. See
[Strategy: `hybrid`](#strategy-hybrid-mesmo--spea2) for why.

## Shared building blocks

These live mostly in `greedy.py`, `models.py`, `config.py`, `config_builder.py`,
`runner.py`, and `state.py`, and are reused by every strategy so that adding a
new one doesn't mean reimplementing any of this.

- **`DesignPoint`** (`models.py`) — one evaluated configuration: `params`
  (sparse dict — only entries that differ from the reference config's own
  value are present, see below), `area`/`peak_power`/`time` (averaged across
  benchmarks if more than one), `asi`/`speedup`, `modified_params` (which
  keys were actually varied — used by both search strategies to avoid
  re-varying an already-modified parameter), `output_path`, and
  `per_benchmark` (each benchmark's own time/speedup, so a config that trades
  a loss on one workload for a bigger win on another stays inspectable
  instead of vanishing into a blended number).
- **Sparse-dict convention**: a config's `params` dict only holds parameters
  that deviate from the reference config's own value (`DEFAULTS` in
  `config.py`, the first entry of each `PARAM_SPACE[param]` list). A missing
  key always means "use the reference config's value" — this is what lets
  `params_key()` (a `frozenset` of `params.items()`) double as a stable cache
  key and what `active_params()` / `BRANCH_PREDICTOR_PARAMS` /
  `CONDITIONAL_PARAMS` (`config.py`) build on to know which knobs are even
  meaningful for a given `branch_predictor_type` (e.g. `nn_learning_rate`
  only matters when the predictor type is `nn`).
- **`compute_baseline()`** — runs every benchmark once with every parameter
  forced to its `DEFAULTS` value; `asi=speedup=1.0` by definition (compared
  against itself).
- **`evaluate_point()`** — the single function every strategy calls to turn a
  `params` dict into a `DesignPoint`: checks `global_cache` first
  (`params_key()`-keyed, shared by every strategy in a run — including
  pre-evaluation screening, via `initial_cache`), and only if it's a miss
  does it call `runner.run()` for every benchmark, then compute `asi`
  (`calculate_asi()`) and `speedup` (`geomean()`) against the baseline.
  Returns `(point, ran_sniper)` so callers can count real Sniper executions
  separately from cache hits.
- **`config_builder.build_runtime_config()`** / **`runner.run()`** — the
  actual simulation call: translates a `params` dict into Sniper
  command-line override flags (`SNIPER_KNOB_MAP`, plus
  `BRANCH_PREDICTOR_TYPE_KNOBS`/`SNIPER_ROB_KNOB_MAP` for knobs Sniper reads
  unconditionally once a type/core is selected), invokes `run-sniper --power`,
  then parses `power.txt` (McPAT's `Area`/`Peak Power`) and `sim.out`
  (`Time (ns)`) out of the run's output directory.
- **Pareto dominance & hypervolume** — `dominates(a, b)` (maximizing both ASI
  and speedup, strictly better in at least one), `update_pareto_front(front,
  points)` (non-domination filter over `front + points`), and
  `hypervolume(front)` (2D area dominated by the front relative to the origin
  `(ASI=0, speedup=0)` — the shared scale every strategy reports progress on
  and, for spea2/mesmo, the signal their convergence checks watch). A point
  with a negative ASI or speedup still belongs on the Pareto front (and is
  still drawn on the plots, see [The ASI metric](#the-asi-metric)) — it just
  contributes nothing to hypervolume on whichever axis it falls below the
  origin, since `hypervolume()` clamps each axis's contribution to
  `max(0.0, ...)`.
- **Resumability** (`state.py`) — each strategy owns a small dataclass
  (`GreedySearchState`, `Spea2SearchState`, `MesmoSearchState`) serialized to
  `search_state.json` after every iteration/generation via
  `write_json_atomic()`. Besides its own search-specific fields, every one of
  these dataclasses also carries parallel `hv_history`/`sim_history`/
  `pareto_size_history` lists — one triple of entries per iteration/
  generation, `hv_history[i]`/`pareto_size_history[i]` the Pareto front's
  hypervolume/point count once `sim_history[i]` real simulations had been
  spent reaching it — which is what `plot_hv_vs_simulations()` (`plot.py`)
  plots at the end of every run into `hv_vs_sims.png`; see
  [Hypervolume-vs-simulations plot](#hypervolume-vs-simulations-plot).
  `state.py` centralizes the parts that are identical across strategies:
  turning a `DesignPoint` to/from JSON (`point_to_dict`/`point_from_dict`),
  atomic writes (so a kill mid-write can't corrupt the checkpoint), reading
  back which strategy a saved file belongs to, RNG-state (de)serialization
  (`rng_state_to_json`/`_from_json`, so a resumed spea2/mesmo run continues
  the *exact* same pseudo-random sequence instead of reseeding), and
  `cleanup_dirs()`.

## Hypervolume-vs-simulations plot

Every strategy ends its run by writing `hv_vs_sims.png`
(`plot_hv_vs_simulations()` in `plot.py`): two stacked subplots sharing one
x-axis, the cumulative number of *real* Sniper simulations spent so far —
the Pareto front's hypervolume on top and its point count (`len(pareto
front)`) on the bottom — drawn as step functions, since both only actually
change at the recorded checkpoints, one per iteration/generation.

What those step functions look like differs *by design* across strategies,
because "one iteration" costs a very different number of simulations in
each:

- **`mesmo`** evaluates `--batch-size` (default **1**) real configuration
  per iteration before refitting its GP surrogate and picking the next one —
  so by default this curve is a near-continuous, smooth staircase: one tiny
  step per simulation, because that's genuinely how often mesmo's front can
  change.
- **`greedy`** and **`spea2`** evaluate a whole *batch* of configurations
  every iteration/generation before the front updates once — greedy's
  search set (every reachable one-parameter-away child of last iteration's
  new Pareto points) and spea2's populations (`--populations ×
  --population-size`, tens of points by default) — so their curves are flat
  plateaus (no data between updates) punctuated by big jumps (a whole
  batch's worth of simulations landing at once).

None of this is a bug in either strategy — it's the direct, visible
consequence of *when* each one is willing to spend a real Sniper run vs.
just re-checking its own bookkeeping, and it's exactly the tradeoff MESMO's
own paper motivates itself with (see
[Strategy: `mesmo`](#strategy-mesmo-bayesian-optimization)): fewer, more
carefully chosen real simulations per unit of front improvement, at the
cost of extra (cheap, non-simulation) computation between them. Comparing
two strategies' `hv_vs_sims.png` side by side is the intended way to judge
that tradeoff empirically on your own reference config/benchmarks, rather
than taking the papers' claims on faith.

## Strategy: `greedy` (sensitivity-based hill climbing)

**File:** `greedy.py`, entry point `explore_pareto_front_with_sensitivity()`.
Default strategy, default 5 iterations.

**Theory.** Start from the baseline and grow the Pareto front one parameter
at a time: every iteration, take the points that were newly added to the
front last time (`newly_added`) and, for each of them, try changing exactly
one not-yet-modified, not-currently-frozen parameter to each of its other
candidate values. This is a local hill-climb, not a global search — it never
considers a configuration that differs from some already-good point by more
than the parameters accumulated one at a time — so it is cheap per iteration
(only points reachable from the current front are tried) but can miss good
configurations that only pay off when several parameters change together.

To avoid wasting evaluations, the search tracks each parameter's recent
effect on ASI and speedup (**sensitivity**) and **freezes** a parameter once
its last few observed effects are all below a threshold, skipping it in
later search-set construction. A frozen parameter isn't frozen forever: it
comes off probation after a backoff window that **doubles** every time it
gets re-frozen (`PROBATION_LENGTH * 2 ** (freeze_count - 1)`), so a parameter
that looked unimportant early (when other parameters were still at their
defaults) gets periodically re-tested once the rest of the configuration has
moved, in case its effect only shows up in combination with other changes.

**Execution order:**

1. `GreedySearchState.load()` / `.matches()` — resume if a compatible
   `search_state.json` exists (same reference config, benchmarks, alpha, and
   `PARAM_SPACE` — a `PARAM_SPACE` change must restart, since a resumed
   search only ever reaches a brand-new parameter/value through this same
   narrow one-at-a-time expansion, which would essentially never surface it).
2. `compute_baseline()` (or reuse the pre-evaluation screening cache's
   baseline, see `initial_cache`) — seeds `pareto_set = [baseline]` and
   `newly_added = [baseline]`.
3. Per iteration, build the **search set**: for every parent in
   `newly_added`, for every parameter that's active for that parent's branch
   predictor type (`active_params()`) and neither already in
   `parent.modified_params` nor currently frozen
   (`frozen_until[param] >= iteration`), for every candidate value other than
   the parent's current one, construct a child `params` dict (dropping stale
   predictor-specific knobs if `branch_predictor_type` itself is the varied
   parameter). Children already in `global_cache` or already queued this
   iteration are skipped.
4. `evaluate_point()` every search-set entry (cache-aware Sniper run); record
   each result's relative ASI/speedup deltas from its parent into
   `sensitivity_history[varied_param]`.
5. **Sensitivity freezing**: for every parameter with at least
   `SENSITIVITY_MIN_SAMPLES` (3) recorded deltas, if the last
   `SENSITIVITY_WINDOW` (6) ASI deltas *and* the last 6 speedup deltas are
   all below `SENSITIVITY_THRESHOLD` (0.05), freeze it with the doubling
   backoff described above.
6. `update_pareto_front()` folds this iteration's evaluated points into
   `pareto_set`; `cleanup_dirs()` deletes the Sniper output directories of
   any point that fell off the front. `newly_added` becomes the evaluated
   points that survived onto the new front — next iteration's parents.
7. `state.save()` checkpoints progress.
8. Stops after `max_iterations` (default 5) or once a search set comes up
   empty (every reachable child already cached and/or every parameter
   frozen); final cleanup, `pareto_history.png` (front evolution across
   iterations) and `pareto_final.png` written via `plot.py`.

Formatting/reporting helpers used throughout (shared with the other two
strategies): `fmt_params()`, `print_evaluated_point()`, `print_pareto_table()`,
`sustainability_label()`.

## Strategy: `spea2` (COLE-style evolutionary search)

**File:** `spea2.py`, entry point `explore_pareto_front_spea2()`. Adapted
from Hoste & Eeckhout, *"COLE: Compiler Optimization Level Exploration"*
(CGO'08), itself built on SPEA2 (Zitzler, Laumanns & Thiele, 2001). Default
30 generations.

**Theory.** Unlike greedy's local hill-climb from the baseline, SPEA2 draws
*fully-specified* random configurations directly from the whole
`PARAM_SPACE` and evolves several independent populations generation over
generation via selection, crossover, and mutation — a global rather than
local search, better suited to finding Pareto-optimal points that differ
from any already-known good point in several parameters at once. Multiple
populations run somewhat independently (helping preserve diversity) but
occasionally exchange individuals (**migration**), and each population keeps
an elitist **archive** of its best-found points across generations so good
solutions are never simply forgotten if a later generation's random
variation doesn't reproduce them.

**Execution order:**

1. `Spea2SearchState.load()` / `.matches()` — same resume rule as greedy
   (restart on any `PARAM_SPACE` change): only generation 0 samples
   parameters uniformly at random; every later generation reaches new values
   only through mutation of an existing population, far too rare to
   introduce a brand-new parameter/value in any reasonable number of
   generations.
2. `compute_baseline()`.
3. **Generation 0**: for each of `--populations` populations, seed
   `population_size` entities — one is the baseline (`DEFAULTS`, i.e. every
   parameter at the reference config's own value), the rest are
   `_random_entity()` draws (one random value
   per always-relevant parameter, plus the drawn `branch_predictor_type`'s
   own knobs). Evaluate every entity (`_evaluate_entity()` /
   `evaluate_point()`), then run `_environmental_selection()` per population
   to build its initial archive. The combined Pareto front is
   `update_pareto_front()` over every archive's union; hypervolume logged.
4. **Per generation** (1..max_iterations):
   1. **Mating pool** (`_make_mating_pool()`, per population): fill
      `population_size` slots. Each slot either migrates in a random entity
      from *another* population's archive (probability `p_migration`) or is
      chosen via `_binary_tournament()` from the population's own archive
      (sample two archive members, keep the one with lower/better SPEA2
      fitness).
   2. **Reproduction**: repeatedly sample two mating-pool parents; with
      probability `p_crossover` produce a child via `_crossover()` (uniform
      crossover — each parameter independently inherited from either parent,
      with careful handling so a child that ends up with a different
      `branch_predictor_type` than either parent draws fresh values for that
      type's own knobs rather than inheriting a now-irrelevant one),
      otherwise the child is a plain copy of one parent; then with
      probability `p_mutation`, `_mutate()` reassigns one random
      currently-active parameter of the child to a new value. This produces
      the next generation's population.
   3. Evaluate every child (`_evaluate_entity()`/`evaluate_point()`,
      cache-aware — same `global_cache` as greedy).
   4. `_environmental_selection()` rebuilds each population's archive from
      (new population + old archive).
   5. Recombine every population's archive into the global `pareto_front`
      (`update_pareto_front()`), append to `hv_history`.
   6. `cleanup_dirs()` deletes output directories no longer referenced by any
      population, archive, or the Pareto front.
   7. `state.save()`.
   8. `_has_converged()` checks whether the combined hypervolume has stopped
      improving for `--patience` generations — if so, stop early (otherwise
      stop at `--iterations`).
5. Final cleanup; `pareto_history.png` / `pareto_final.png` written.

**SPEA2 fitness & selection internals** (used inside step 4.i and 4.iv above,
in the order they act):

- `_normalized_objectives()` / `_distance()` — min-max normalize (ASI,
  speedup) to `[0, 1]` across the current pool before computing Euclidean
  distances, so neither objective's natural scale distorts crowding/density.
- `_spea2_fitness()` — the SPEA2 fitness assignment itself (Zitzler et al.
  2001): `strength[p]` = how many other pool members `p` dominates; `raw[p]`
  = sum of the strengths of everything that dominates `p` (0 means `p` is
  non-dominated within the pool); `density[p]` = `1 / (distance to the
  k-th-nearest neighbor + 2)`, with `k = sqrt(pool size)`. `fitness = raw +
  density` — **lower is better**; density only breaks ties among
  non-dominated (`raw == 0`) points, favoring points in sparser regions of
  the front.
- `_environmental_selection()` — combine `population + archive`; keep every
  non-dominated point; if there are fewer than `archive_size`, pad with the
  best-fitness dominated points (ties broken toward *fewer* modified
  parameters — simpler configurations preferred); if there are more, hand
  them to `_truncate()`.
- `_truncate()` — SPEA2's crowding-based truncation: while the non-dominated
  set is larger than `archive_size`, repeatedly drop the point closest to
  its nearest neighbor (the most "crowded" one), so survivors spread out to
  cover the objective range as widely as possible; ties again broken toward
  dropping the point with *more* modified parameters.
- `_binary_tournament()` — pick two archive members at random, return
  whichever has the lower (better) fitness.

## Strategy: `mesmo` (Bayesian optimization)

**File:** `mesmo.py`, entry point `explore_pareto_front_mesmo()`. Adapted
from Belakaria, Deshwal & Doppa, *"Max-value Entropy Search for
Multi-Objective Bayesian Optimization"* (NeurIPS'19; JAIR'21 journal
version). Default 30 iterations.

**Theory.** SPEA2 needs to evaluate whole populations of real configurations
every generation to make progress. MESMO instead fits a cheap **probabilistic
surrogate model** — one Gaussian Process (GP) per objective (ASI, speedup),
approximated here with Random Fourier Features (Rahimi & Recht, 2008) so its
posterior mean/variance are closed-form and fast to compute — to every real
evaluation seen so far, and uses it to *rank* a fresh pool of unevaluated
candidates by how much information a real evaluation of each one would
likely reveal about the true Pareto front, before spending a real (expensive)
Sniper run only on the top-ranked candidate(s). This is intended to reach a
comparable Pareto front with substantially fewer real simulations than
SPEA2's population-based search, at the cost of extra per-iteration
computation (GP fitting + Monte-Carlo acquisition scoring — all cheap
relative to a Sniper run).

**Execution order:**

1. `MesmoSearchState.load()` / `.matches()` — same restart-on-`PARAM_SPACE`-change
   rule as the other two strategies (the GP's feature encoding is derived
   directly from `PARAM_SPACE`).
2. `compute_baseline()`.
3. **Initial design**: baseline + `num_initial_points - 1` `_random_entity()`
   draws, each evaluated via `_evaluate_candidate()`/`evaluate_point()` — a
   GP posterior is meaningless with zero or one observations, so this seeds
   real data before any acquisition-guided iteration runs.
   `update_pareto_front()` and `hypervolume()` are logged as the state before
   iteration 1.
4. **Per iteration** (1..max_iterations):
   1. Encode every real evaluation in `global_cache` into fixed-length
      one-hot feature vectors: `_feature_names()` fixes one column per
      non-default `(param, value)` pair across `PARAM_SPACE` (same convention
      as `screening._encode_features`), `_encode_all()`/`_encode()` build the
      actual `(n_points, n_features)` matrix.
   2. `_median_heuristic_lengthscale()` re-derives the GP kernel lengthscale
      from that real training data every iteration (median pairwise squared
      distance / 2, Gretton et al.'s standard RBF default) unless
      `--gp-lengthscale` fixes one — a naive fixed guess measured on this
      project's real `PARAM_SPACE` made any two unrelated random
      configurations ~0.85 kernel-similar, leaving both the GP's mean and
      variance nearly flat across the whole candidate pool and the
      acquisition function with nothing to rank on.
   3. `_fit_rff_model()`, once per objective (ASI, speedup): draws a random
      feature basis `(W, b)`, standardizes the targets, and computes the
      closed-form Bayesian-linear-regression posterior over the RFF weights
      (`_RFFModel`) — this doubles as both a posterior mean/variance
      predictor (`.predict()`) and a way to draw whole posterior-sampled
      functions (`.sample_weights()` + `.evaluate()`).
   4. `_candidate_pool()` draws `candidate_pool_size` not-yet-evaluated
      configurations to score this iteration — standing in for MESMO's
      argmax over the combinatorially huge full space, the same way SPEA2's
      populations do. Roughly half are `_neighbor()` single-parameter
      mutations of a randomly chosen current-Pareto-front anchor (so the
      acquisition function gets genuine near-optimal candidates to weigh, not
      just points scattered uniformly across the whole space); the rest are
      fresh `_random_entity()` global draws.
   5. `_acquisition_scores()` scores every pool candidate: for
      `num_mc_samples` Monte-Carlo draws, sample one posterior "function" per
      objective (`_RFFModel.sample_weights()`/`.evaluate()`), find *that
      sample's own* Pareto front over the candidate pool
      (`_sample_pareto_front()` — brute-force non-domination filtering,
      reusing `greedy.update_pareto_front()` via the lightweight `_Sample`
      stand-in, since the pool itself already stands in for the input space),
      and read off each objective's maximum across that sample's front
      (`y*_asi`, `y*_speedup` — paper eq. 4.9). Each candidate's per-sample,
      per-objective contribution is a truncated-Gaussian entropy term
      (`_entropy_term()`, paper eq. 4.13) computed from the GP's real
      posterior mean/std at that candidate versus `y*`; averaging over
      samples gives the final acquisition score.
   6. The top `batch_size` (default 1 — fully sequential, as in the paper)
      scored candidates are evaluated for real
      (`_evaluate_candidate()`/`evaluate_point()`).
   7. `update_pareto_front()`, `hv_history` append, `cleanup_dirs()` for any
      point dropped from the front, `state.save()`.
   8. If `--mesmo-patience` is set, `_has_converged()` can stop early; unset
      (the default) runs the full `--iterations` budget, matching the
      paper's fixed-budget algorithm — a short patience window would trigger
      on ordinary exploration noise long before the surrogate has seen
      enough real data to be useful, since one MESMO iteration can be a
      single evaluation (`batch_size=1`) rather than a whole SPEA2-style
      generation.
5. Final cleanup; `pareto_history.png` / `pareto_final.png` written.

## Strategy: `hybrid` (mesmo + spea2)

**File:** `hybrid.py`, entry point `explore_pareto_front_hybrid()`. Not a new
search algorithm in its own right — it runs `mesmo.py`'s and `spea2.py`'s
*existing* entry points back to back, unmodified, in two phases against two
sub-directories (`outputdir/mesmo_phase`, `outputdir/spea2_phase`). The only
change made to either strategy for this was adding one new parameter to
`explore_pareto_front_spea2()` (`seed_entities`, see below) — everything else
`hybrid.py` does is call those two functions with the right arguments and
hand phase 1's results to phase 2.

**Theory.** MESMO's Bayesian-optimization surrogate is good at cheaply
narrowing in on promising regions of `PARAM_SPACE` with relatively few real
simulations, but it evaluates points one (or a small `--batch-size`) at a
time, and its GP surrogate gets more expensive to refit as more real points
accumulate — see [Hypervolume-vs-simulations
plot](#hypervolume-vs-simulations-plot) for how that shows up in practice.
SPEA2, by contrast, is good at *spreading out* an already-decent front once
it has a population worth recombining and mutating, but starting cold from
`spea2`'s usual fully-random generation 0 means it wastes early generations
re-discovering points MESMO's surrogate could have found far more cheaply.
`hybrid` tries to get the best of both: run MESMO first, for as long as it
keeps actually improving the front, then hand its result to SPEA2 as a
running start instead of a blank slate.

**Execution order:**

1. **Phase 1 — MESMO until plateau.** Calls
   `mesmo.explore_pareto_front_mesmo()` completely unchanged, against
   `outputdir/mesmo_phase`, for up to `--mesmo-phase-iterations` (default 30)
   iterations, with `hv_patience` defaulted to **5** instead of mesmo's own
   "unset" default (see [`hybrid` flags](#hybrid-flags)) — this is the
   "plateau" the strategy is named for: as soon as 5 consecutive MESMO
   iterations haven't meaningfully improved hypervolume
   (`mesmo._has_converged()`, unchanged), MESMO stops, exactly as it would if
   you'd passed `--strategy mesmo --mesmo-patience 5` yourself.
2. **Handoff.** `mesmo.MesmoSearchState.load(mesmo_phase_dir)` reads back the
   checkpoint MESMO just wrote (the same `search_state.json` it uses for its
   own resumability — no new state format), giving `hybrid.py` MESMO's final
   `global_cache` (every `params -> DesignPoint` it evaluated, including the
   shared baseline) and its final `pareto_front`.
3. **Phase 2 — SPEA2 seeded from MESMO's front.** Calls
   `spea2.explore_pareto_front_spea2()` against `outputdir/spea2_phase`,
   passing:
   - `initial_cache=<MESMO's global_cache>` — the same mechanism
     `screening.py` already uses to seed a fresh strategy's cache (see
     [Shared building blocks](#shared-building-blocks)): SPEA2's own baseline
     run, and any generation-0 entity that happens to coincide with
     something MESMO already evaluated, is served from cache instead of
     re-simulated.
   - `seed_entities=<MESMO's final Pareto front's params>` — this is the
     part that actually changes what SPEA2 evaluates, and the one small
     addition made to `spea2.py` for this strategy: every population's
     generation 0 is now `[baseline] + seed_entities`, padded with the usual
     `_random_entity()` draws if that's shorter than `--population-size`
     (truncated if longer), instead of `[baseline] +
     (population_size - 1) random draws`. From generation 1 onward, SPEA2
     proceeds completely unmodified — mating pool, crossover, mutation,
     environmental selection all work on whatever population resulted from
     that seeded generation 0, exactly as if a human had hand-picked
     generation 0's starting entities.
4. Returns SPEA2's final Pareto front — the same return type/shape every
   other strategy returns, so `cli.py`'s final reporting/plotting needed no
   changes to support `hybrid`.

**Why two sub-directories, not one shared `outputdir`.** `MesmoSearchState`
and `Spea2SearchState` (`state.py`) each stamp their own `strategy` name into
`search_state.json` and refuse to resume a file written by the other one
(`.load()` prints "starting fresh" instead — see
[Resumability](#resumability)). If both phases wrote to the same directory,
SPEA2's first checkpoint write in phase 2 would silently overwrite MESMO's
saved state with its own. That would not affect a single uninterrupted run
(the handoff in step 2 above happens *before* SPEA2's first write, so the
values are already read out), but it would break resuming an *interrupted*
hybrid run: restarting the same command would find no MESMO checkpoint left
at all, and would have to redo every MESMO iteration — burning real Sniper
simulations a second time — before even reaching SPEA2 again. Giving each
phase its own sub-directory (`hybrid.py` creates both upfront, mirroring
`cli.py`'s own `outputdir.mkdir()` before dispatching to any strategy) keeps
each phase independently resumable, exactly as if you'd run `--strategy
mesmo` and then `--strategy spea2` by hand against two different
`--outputdir`s.

## Optional pre-processing: parameter screening

**File:** `screening.py`, entry point `screen_param_space()`. Triggered by
`--preeval-samples > 0`, runs once before whichever `--strategy` was chosen
and prunes parameters judged unimportant down to their default-only value
(`{param: [DEFAULTS[param]]}`), so the search strategy never spends
evaluations varying them. Not resumable — if interrupted, just rerun it.
This is a rough screen, not a substitute for greedy's own online sensitivity
freezing or spea2/mesmo's full-space search — it exists purely to shrink
`PARAM_SPACE` before a strategy starts, not to replace within-strategy
adaptivity.

Two methods (`--preeval-method`):

**`perceptron`** (default):
1. `_random_entity()` draws `--preeval-samples` fully-specified random
   configurations.
2. Each is evaluated (`evaluate_point()`) and one-hot encoded
   (`_encode_features()` — one binary feature per non-default `(param,
   value)` pair) against a target: Euclidean distance from the baseline in
   `(ASI, speedup)` space, `sqrt((asi-1)^2 + (speedup-1)^2)`.
3. `_train_perceptron()` trains a single linear unit online (delta-rule
   gradient descent with L2 regularization) to predict that target from the
   one-hot features; each feature's learned `|weight|` is its importance
   signal.
4. A parameter's importance is the max `|weight|` across its own
   non-default values; it's kept if that's at least `--preeval-threshold`
   fraction of the most important parameter's, else pruned to default-only.

Caveat documented in the module: with only a few dozen samples spread across
~20 parameters, a conditional parameter only active under a rarely-sampled
`branch_predictor_type` (e.g. `nn_learning_rate` under `nn`) can look
unimportant purely by chance. Keep `--preeval-samples` comfortably larger
than the parameter count, or skip screening when every parameter is known to
matter.

**`plackett_burman`**: follows Yi, Lilja & Hawkins, *"A Statistically
Rigorous Approach for Improving Simulation Methodology"* (Section 2).
1. Every always-relevant, non-branch-predictor parameter is treated as a
   two-level factor, tested at the **minimum and maximum** of its listed
   values (`_pb_entities()`).
2. `_next_pb_size()` finds the smallest valid Plackett-Burman design size (a
   multiple of 4, via the Paley construction) that fits every real parameter
   plus at least two spare **dummy** columns bound to no real parameter.
   `_pb_base_design()` builds the base Hadamard-derived design (quadratic
   residues mod a prime `q = size - 1`, `q % 4 == 3`); `_pb_design_with_foldover()`
   appends its sign-flipped mirror (the paper's foldover, which improves the
   design's ability to separate main effects).
3. Every design row becomes one fully-specified configuration
   (`_pb_entities()`), evaluated the same way as the perceptron method.
4. A parameter's **effect** is the sum, over every run, of its assigned
   `±1` level times that run's distance-from-baseline target; a dummy
   column's effect is computed identically despite representing nothing, so
   its magnitude *is* what pure noise looks like at this sample size
   (`_screen_plackett_burman()`). A real parameter is kept only if its
   `|effect|` **exceeds the largest dummy-column `|effect|`** — the paper's
   own significance rule (Table 6), not an arbitrary fraction of the largest
   real effect.
5. `branch_predictor_type` and its own predictor-specific knobs can't be
   represented as a two-level factor (they're a >2-valued categorical
   choice) and are excluded from the design entirely — always pruned to
   default-only in this mode, with no evaluations spent judging them.

`screen_param_space()` orchestrates both methods: it computes (or reuses) the
baseline, runs the samples/design, and returns
`(pruned_param_space, global_cache)`. `cli.py` then monkeypatches the pruned
space into `greedy`/`spea2`/`mesmo`'s own module-level `PARAM_SPACE` (each
strategy module imports its own copy at import time, so patching
`config.PARAM_SPACE` alone wouldn't reach them) and forwards the populated
cache as `initial_cache`, so the baseline (and any screening sample the
search happens to re-encounter, most commonly the baseline itself) is a cache
hit instead of a re-run.

## Extending: adding a new strategy

`strategies.py` is a small registry so `cli.py` can dispatch on a single
`--strategy` flag without hardcoding per-strategy logic: it maps a strategy
name to a `run` function
(`(reference_config, sniper, outputdir, benchmarks, alpha, max_iterations,
**kwargs) -> list[DesignPoint]`). `cli.py` forwards any CLI flag whose
argparse `dest` matches one of that function's parameter names
(`inspect.signature`), so a new strategy's own flags (like spea2's
`--populations` or mesmo's `--mc-samples`) just need to be declared in
`build_parser()` — no further dispatch changes required.

To add one: write its `run` function plus a resumable state dataclass
following `GreedySearchState`/`Spea2SearchState`/`MesmoSearchState`'s pattern
(a `STRATEGY` class var, `to_dict`/`from_dict`, a `.matches()` that at least
checks `PARAM_SPACE` equality, and `.save()`/`.load()` built on `state.py`'s
shared primitives), then register it in `STRATEGIES`.

`hybrid.py` (see [Strategy: `hybrid`](#strategy-hybrid-mesmo--spea2)) is a
different kind of example worth knowing about: a strategy doesn't have to
write its own state dataclass at all if it's really just composing existing
ones. It calls `mesmo.explore_pareto_front_mesmo()` and
`spea2.explore_pareto_front_spea2()` as-is, each against its own
sub-directory (so each keeps using its own `MesmoSearchState`/
`Spea2SearchState` and stays independently resumable), and reads the first
phase's result back via that phase's own `.load()` to feed the second. Worth
following this pattern instead of a from-scratch state dataclass if a new
strategy is fundamentally "run existing strategy A, then existing strategy
B with A's result as a starting point" rather than a genuinely new search
loop.
