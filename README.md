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
possible: `greedy` (sensitivity-based hill climbing), `spea2` (an
evolutionary algorithm), `mesmo` (Bayesian optimization), and `hybrid`
(mesmo, then spea2 seeded from mesmo's result). Pick one via `--strategy`.

This document is split in two: **[Part 1](#part-1-using-the-framework)**
covers everything needed to install and run it; **[Part 2](#part-2-how-it-works)**
explains the algorithms and internals for anyone extending or debugging the
code. If you just want to run a search, Part 1 is all you need.

## Table of contents

**Part 1 — Using the framework**
- [Getting started](#getting-started)
  - [1. Clone the repository](#1-clone-the-repository)
  - [2. Build Sniper](#2-build-sniper)
  - [3. (Optional) TAGE branch predictor](#3-optional-tage-branch-predictor)
  - [4. Python environment](#4-python-environment)
  - [5. (Optional) Running at scale on Titan](#5-optional-running-at-scale-on-titan)
- [Usage](#usage)
  - [General flags](#general-flags)
  - [`spea2` flags](#spea2-flags)
  - [`mesmo` flags](#mesmo-flags)
  - [`hybrid` flags](#hybrid-flags)
  - [Pre-evaluation screening flags](#pre-evaluation-screening-flags)
  - [Output layout](#output-layout)
- [Testing without Sniper](#testing-without-sniper)

**Part 2 — How it works**
- [The ASI metric](#the-asi-metric)
- [Shared building blocks](#shared-building-blocks)
- [Hypervolume-vs-simulations plot](#hypervolume-vs-simulations-plot)
- [Strategy: `greedy`](#strategy-greedy-sensitivity-based-hill-climbing)
- [Strategy: `spea2`](#strategy-spea2-cole-style-evolutionary-search)
- [Strategy: `mesmo`](#strategy-mesmo-bayesian-optimization)
- [Strategy: `hybrid`](#strategy-hybrid-mesmo--spea2)
- [Optional pre-processing: parameter screening](#optional-pre-processing-parameter-screening)
- [Extending: adding a new strategy](#extending-adding-a-new-strategy)

---

# Part 1: Using the framework

## Getting started

### 1. Clone the repository

```bash
git clone --recurse-submodules <this-repo-url> stage
cd stage
```

This repo bundles everything needed to run locally: the ASI framework
(`asi/`), a curated benchmark set (`asi/benchmarks/`), the Titan HPC
submission tool (`titan_controller/`), and Sniper itself as a submodule
(`snipersim/`). If you already cloned without `--recurse-submodules`, fetch
the submodules with:

```bash
git submodule update --init --recursive
```

### 2. Build Sniper

```bash
cd snipersim
make
```

This builds `snipersim/run-sniper`, the binary the framework drives every
search through. A full build takes a while (Sniper links in several bundled
toolkits) — that's expected. See
[snipersim's own README](snipersim/README.md) and
[snipersim.org/w/Getting_Started](https://snipersim.org/w/Getting_Started)
for platform-specific build issues.

### 3. (Optional) TAGE branch predictor

The framework works with Sniper's stock branch predictors out of the box.
If you also want the [TAGE](asi/libs/TAGE) predictor available as a
`branch_predictor_type` choice:

```bash
./scripts/install_tage_predictor.sh
```

This copies `asi/libs/TAGE`'s source into `snipersim`'s branch predictor
directory and registers it in Sniper's predictor factory. It only touches
`snipersim`'s working tree (nothing is committed there), so it's safe to
re-run any time after a fresh `git submodule update`. Rebuild Sniper
afterward (`cd snipersim && make`) so the new predictor is actually
compiled in.

Optional convenience: this repo ships git hooks
(`.githooks/post-checkout`/`post-merge`) that re-run the install
automatically whenever both `snipersim` and the `TAGE` submodule are
present, so you don't need to remember to re-run it after every
`git submodule update`. They're not active by default on a fresh clone —
opt in once with:

```bash
git config core.hooksPath .githooks
```

### 4. Python environment

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r asi/requirements.txt
```

That's `numpy`, `scipy`, and `matplotlib` — everything the search strategies
and plotting need. No package installation step is required for the
framework itself; you run it directly with `python3 asi.py ...` (see
[Usage](#usage) below).

### 5. (Optional) Running at scale on Titan

Steps 1–4 are enough to run searches locally. For running at the scale a
full search actually needs — hundreds or thousands of Sniper invocations —
see [Running on Titan (HPC)](#running-on-titan-hpc) and
[titan_controller/RUNBOOK.md](titan_controller/RUNBOOK.md).

## Usage

Run from inside `asi/` (it's a relative-import Python package, not something
installed via pip):

```
cd asi
python3 asi.py --config <reference.cfg> --strategy {greedy,spea2,mesmo,hybrid} \
    [--sniper PATH] [--outputdir DIR] [--alpha A] [--iterations N] \
    [--log [PATH]] [--save-plot [PATH]] \
    [strategy-specific flags...] [pre-evaluation flags...] \
    -- <benchmark command> [-- <another benchmark command> ...]
```

One or more benchmark commands **must** follow `--`; repeat `-- ./other_bench
...` to search across several benchmarks at once (their per-benchmark
results are geomean'd into one speedup, see [The ASI metric](#the-asi-metric)).
Each benchmark's display name is derived from its executable's parent
directory (matching the `benchmarks/<NAME>/bench` layout used by
[`asi/benchmarks/`](asi/benchmarks/)).

Example:

```
cd asi
python3 asi.py --config nehalem.cfg --strategy spea2 --log --save-plot \
    -- ./benchmarks/ML2/bench
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

Independent of `--strategy` — runs first if `--preeval-samples > 0` or
`--preeval-cache` is passed, and prunes parameters judged unimportant to
their default-only value before the chosen strategy starts. See
[Optional pre-processing](#optional-pre-processing-parameter-screening).

| Flag | Default | Meaning |
|---|---|---|
| `--preeval-samples` | 0 | 0 disables screening. With `--preeval-method perceptron`, this is the sample count; with `plackett_burman` it's just an on/off switch. |
| `--preeval-method` | `perceptron` | `perceptron` or `plackett_burman`. |
| `--preeval-threshold` | 0.1 | Keep a parameter only if its importance is at least this fraction of the most important parameter's (perceptron; also used by plackett_burman's branch-predictor one-at-a-time screen). |
| `--preeval-seed` | 0 | RNG seed for perceptron sampling (plackett_burman is deterministic). |
| `--preeval-cache` | off | Reuse a previous `--preeval-samples` run's cached pruning + evaluated points from `outputdir/preeval/screen_cache.json` instead of screening again — lets you rerun the search strategy with different strategy flags without repeating screening's Sniper runs. Errors out if no matching cache exists yet; mutually exclusive with `--preeval-samples` (which already reuses a matching cache automatically, see below). |

A completed screen is itself cached to `outputdir/preeval/screen_cache.json` and reused automatically the next time `--preeval-samples`/`--preeval-method` is run with a matching config/benchmarks/alpha/method (and, for `perceptron`, matching sample count/threshold/seed too, since unlike `plackett_burman` its result depends on them) — so rerunning the same `--preeval-*` flags just to change an unrelated strategy flag doesn't re-pay for screening's own Sniper runs.

### Output layout

Under `--outputdir`:
- `baseline/` — the reference config's own Sniper/McPAT run.
- Per-strategy working directories for every evaluated point (e.g.
  `iter{N}_run{i}` for greedy, `gen{N}_pop{P}_ent{E}` for spea2,
  `init{i}` / `iter{N}_cand{rank}` for mesmo). Directories belonging to
  points that fall off the Pareto front are deleted as the search
  progresses, so disk usage stays bounded by the current front rather than
  every point ever tried.
- `search_state.json` — resumable checkpoint; re-running the same command
  against the same `--outputdir` continues instead of restarting, as long
  as the reference config, benchmarks, alpha, and parameter space are
  unchanged.
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

## Testing without Sniper

**File:** `tests/test.py`. A standalone harness that exercises `greedy.py`'s
sensitivity/freezing/probation logic with a synthetic, seeded stand-in for
Sniper — no simulator needed, runs in seconds. Useful for iterating on the
search logic itself without waiting on real simulations. Run it with:

```bash
python3 asi/tests/test.py
```

Since the synthetic model's ground truth (which parameters "really" matter)
is known up front, the printed final Pareto front can be checked by eye:
parameters with a real effect should stay active across iterations, while
null parameters should freeze within the first couple of iterations.

## Running on Titan (HPC)

Running full search strategies at scale means running Sniper hundreds or
thousands of times, which isn't practical on a single machine. `titan_controller/`
(vendored in this repo) submits/tracks/collects those runs as Slurm jobs on
the UGent ELIS Titan cluster. See
[titan_controller/RUNBOOK.md](titan_controller/RUNBOOK.md) for the actual
working setup — how to submit an experiment, where results land, and how to
add or modify a benchmark. [titan_controller/README.md](titan_controller/README.md)
covers generic install/build instructions for the tool itself.

---

# Part 2: How it works

The rest of this document explains the algorithms and internal design —
useful if you're extending a strategy, debugging unexpected search
behavior, or just curious how the numbers get produced. None of it is
required reading to run a search; see [Part 1](#part-1-using-the-framework)
for that.

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
  Returns `(point, ran_sniper, sniper_invocations)`: `ran_sniper` is `False`
  only on a cache hit, letting callers count *configurations evaluated*
  (what a search iteration/generation actually spends its budget on) apart
  from cache hits; `sniper_invocations` is the raw count of literal Sniper
  subprocess calls made for this point (`0` on a cache hit, otherwise one per
  benchmark).
- **`config_builder.build_runtime_config()`** / **`runner.run()`** — the
  actual simulation call: translates a `params` dict into Sniper
  command-line override flags (`SNIPER_KNOB_MAP`, plus
  `BRANCH_PREDICTOR_TYPE_KNOBS`/`SNIPER_ROB_KNOB_MAP` for knobs Sniper reads
  unconditionally once a type/core is selected), invokes `run-sniper --power`,
  then parses `power.txt` (McPAT's `Area`/`Peak Power`) and `sim.out`
  (`Time (ns)`) out of the run's output directory.
- **Pareto dominance & hypervolume** — `dominates(a, b)` (maximizing both ASI
  and speedup, strictly better in at least one), `update_pareto_front(front,
  points)` (non-domination filter, deduplicated by `params_key()`), and
  `hypervolume(front)` (2D area dominated by the front relative to the origin
  `(ASI=0, speedup=0)` — the shared scale every strategy reports progress on
  and, for spea2/mesmo, the signal their convergence checks watch). A point
  with a negative ASI or speedup still belongs on the Pareto front — it just
  contributes nothing to hypervolume on whichever axis it falls below the
  origin.
- **Resumability** (`state.py`) — each strategy owns a small dataclass
  (`GreedySearchState`, `Spea2SearchState`, `MesmoSearchState`) serialized to
  `search_state.json` after every iteration/generation. Besides its own
  search-specific fields, every one of these dataclasses also carries
  parallel `hv_history`/`sim_history`/`pareto_size_history` lists — what
  `plot_hv_vs_simulations()` plots into `hv_vs_sims.png` at the end of every
  run (see
  [Hypervolume-vs-simulations plot](#hypervolume-vs-simulations-plot)).
  `state.py` centralizes what's identical across strategies: `DesignPoint`
  JSON (de)serialization, atomic writes (so a kill mid-write can't corrupt
  the checkpoint), and RNG-state (de)serialization (so a resumed spea2/mesmo
  run continues the *exact* same pseudo-random sequence instead of
  reseeding).

## Hypervolume-vs-simulations plot

Every strategy ends its run by writing `hv_vs_sims.png`: two stacked
subplots sharing one x-axis, the cumulative number of *real* Sniper
simulations spent so far — the Pareto front's hypervolume on top and its
point count on the bottom — drawn as step functions, since both only
actually change at the recorded checkpoints, one per iteration/generation.

What those step functions look like differs *by design* across strategies,
because "one iteration" costs a very different number of simulations in
each:

- **`mesmo`** evaluates `--batch-size` (default **1**) real configuration
  per iteration before refitting its GP surrogate and picking the next one —
  so by default this curve is a near-continuous, smooth staircase.
- **`greedy`** and **`spea2`** evaluate a whole *batch* of configurations
  every iteration/generation before the front updates once — greedy's
  search set (every reachable one-parameter-away child of last iteration's
  new Pareto points) and spea2's populations (`--populations ×
  --population-size`, tens of points by default) — so their curves are flat
  plateaus punctuated by big jumps.

Neither behavior is a bug — it's the direct, visible consequence of *when*
each strategy is willing to spend a real Sniper run vs. just re-checking its
own bookkeeping, and it's exactly the tradeoff MESMO's own paper motivates
itself with (see [Strategy: `mesmo`](#strategy-mesmo-bayesian-optimization)):
fewer, more carefully chosen real simulations per unit of front improvement,
at the cost of extra (cheap) computation between them. Comparing two
strategies' `hv_vs_sims.png` side by side is the intended way to judge that
tradeoff empirically on your own reference config/benchmarks, rather than
taking the papers' claims on faith.

## Strategy: `greedy` (sensitivity-based hill climbing)

**File:** `greedy.py`, entry point `explore_pareto_front_with_sensitivity()`.
Default strategy, default 5 iterations.

**Theory.** Start from the baseline and grow the Pareto front one parameter
at a time: every iteration, take the points that were newly added to the
front last time and, for each of them, try changing exactly one
not-yet-modified, not-currently-frozen parameter to each of its other
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
gets re-frozen, so a parameter that looked unimportant early (when other
parameters were still at their defaults) gets periodically re-tested once
the rest of the configuration has moved, in case its effect only shows up in
combination with other changes.

**How a run proceeds:** resume from a compatible checkpoint if one exists
(restarts on any parameter-space change) → compute the baseline → each
iteration, build the search set from last iteration's new Pareto points,
evaluate it, update sensitivity tracking and freeze/unfreeze parameters
accordingly, fold results into the Pareto front, checkpoint → stop after
`max_iterations` or once the search set comes up empty.

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

**How a run proceeds:** resume from a compatible checkpoint → compute the
baseline → generation 0 seeds each population with the baseline plus random
entities, evaluates them, and builds each population's initial archive →
every later generation, fill a mating pool per population (tournament
selection from its archive, with a small chance of migrating in an entity
from another population's archive instead), breed the next generation via
crossover/mutation, evaluate it, and rebuild each population's archive from
(new population + old archive) → recombine every archive into the global
Pareto front, checkpoint → stop once hypervolume hasn't improved for
`--patience` generations, or at `--iterations`.

**SPEA2 fitness & selection**, used when rebuilding an archive each
generation: fitness combines *dominance strength* (how many pool members a
point dominates, and how dominated it is in turn — 0 is best, non-dominated)
with a *crowding/density* term that only breaks ties among non-dominated
points, favoring sparser regions of the front. When an archive holds more
than `--archive-size` non-dominated points, the most crowded ones (closest
to their nearest neighbor) are dropped first, so survivors spread out to
cover the objective range as widely as possible.

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

**How a run proceeds:** resume from a compatible checkpoint → compute the
baseline → an initial design of `--num-initial-points` random configurations
seeds real data for the GP (meaningless with zero or one observations) →
every iteration, refit a GP per objective on all real evaluations so far,
draw a fresh candidate pool (roughly half small mutations of a current
Pareto point, the rest fresh random draws), score every candidate with the
acquisition function (Monte-Carlo entropy-based scoring of how much a real
evaluation would likely narrow down the true Pareto front), and spend a real
Sniper run only on the top `--batch-size` candidates → fold results into the
Pareto front, checkpoint → runs the full `--iterations` budget by default
(early stopping via `--mesmo-patience` is opt-in, since one iteration can be
a single evaluation — too noisy a signal for a short patience window).

The GP kernel's lengthscale is re-derived from real training data every
iteration by default (`--gp-lengthscale` to fix it instead) — a naive fixed
guess made unrelated random configurations look nearly identical to the GP,
leaving the acquisition function with nothing meaningful to rank on.

## Strategy: `hybrid` (mesmo + spea2)

**File:** `hybrid.py`, entry point `explore_pareto_front_hybrid()`. Not a new
search algorithm in its own right — it runs `mesmo.py`'s and `spea2.py`'s
*existing* entry points back to back, unmodified, in two phases against two
sub-directories (`outputdir/mesmo_phase`, `outputdir/spea2_phase`).

**Theory.** MESMO's Bayesian-optimization surrogate is good at cheaply
narrowing in on promising regions of `PARAM_SPACE` with relatively few real
simulations, but it evaluates points one (or a small `--batch-size`) at a
time, and its GP surrogate gets more expensive to refit as more real points
accumulate. SPEA2, by contrast, is good at *spreading out* an already-decent
front once it has a population worth recombining and mutating, but starting
cold from spea2's usual fully-random generation 0 means it wastes early
generations re-discovering points MESMO's surrogate could have found far
more cheaply. `hybrid` tries to get the best of both: run MESMO first, for
as long as it keeps actually improving the front, then hand its result to
SPEA2 as a running start instead of a blank slate.

**How a run proceeds:** phase 1 runs plain MESMO (with `--mesmo-patience`
defaulted to 5, unlike standalone mesmo's "unset") until it plateaus → phase
2 runs SPEA2, seeded with MESMO's evaluated points as its starting
cache (so re-evaluating anything MESMO already tried is free) and MESMO's
final Pareto front as generation 0's population (round-robined across
populations, replacing the usual random draw) → returns SPEA2's final
front, same shape every other strategy returns.

Each phase gets its own sub-directory rather than sharing `--outputdir`
because each strategy's checkpoint file identifies its own owning strategy
and won't resume one written by the other — sharing a directory would let
SPEA2's first checkpoint write silently overwrite MESMO's, breaking the
ability to resume an *interrupted* hybrid run without redoing MESMO's real
simulations.

## Optional pre-processing: parameter screening

**File:** `screening.py`, entry points `screen_param_space()` and
`load_screening_cache()` (the latter backs `--preeval-cache`). Triggered by
`--preeval-samples > 0` or `--preeval-cache`, runs once before whichever
`--strategy` was chosen and prunes parameters judged unimportant down to
their default-only value, so the search strategy never spends evaluations
varying them. Not resumable — if interrupted, just rerun it. This is a rough
screen, not a substitute for greedy's own online sensitivity freezing or
spea2/mesmo's full-space search — it exists purely to shrink `PARAM_SPACE`
before a strategy starts, not to replace within-strategy adaptivity.

Two methods (`--preeval-method`):

**`perceptron`** (default): draws `--preeval-samples` random configurations,
evaluates and one-hot encodes them against a target (distance from the
baseline in ASI/speedup space), and trains a single linear unit online to
predict that target from the encoding — each feature's learned weight
magnitude is its importance signal, and a parameter is kept only if its most
important value's weight clears `--preeval-threshold` (as a fraction of the
overall most important parameter's weight).

Caveat: with only a few dozen samples spread across ~20 parameters, a
conditional parameter only active under a rarely-sampled
`branch_predictor_type` (e.g. `nn_learning_rate` under `nn`) can look
unimportant purely by chance. Keep `--preeval-samples` comfortably larger
than the parameter count, or skip screening when every parameter is known to
matter.

**`plackett_burman`**: follows Yi, Lilja & Hawkins, *"A Statistically
Rigorous Approach for Improving Simulation Methodology"* (Section 2). Every
always-relevant, non-branch-predictor parameter is tested at the minimum and
maximum of its listed values in a Plackett-Burman design (a fractional
factorial design that separates each parameter's main effect using far
fewer runs than testing every combination), padded with dummy columns bound
to no real parameter. A parameter's effect is kept only if it exceeds the
largest dummy column's effect — the paper's own significance rule, not an
arbitrary threshold. `branch_predictor_type` and its own knobs can't be
represented as a two-level factor and are excluded from the design (left
untouched, not pruned, for the search strategy to explore normally).

`cli.py` monkeypatches the pruned space into each strategy module's own
`PARAM_SPACE` and forwards the populated cache as `initial_cache`. If the
chosen strategy accepts `seed_entities` (currently only `spea2`), `cli.py`
also hands it every non-baseline point screening evaluated, the same idea as
`hybrid.py` seeding `spea2`'s generation 0 from `mesmo`'s final front — so
screening's own evaluations aren't wasted once the real search starts.

## Extending: adding a new strategy

`strategies.py` is a small registry so `cli.py` can dispatch on a single
`--strategy` flag without hardcoding per-strategy logic: it maps a strategy
name to a `run` function
(`(reference_config, sniper, outputdir, benchmarks, alpha, max_iterations,
**kwargs) -> list[DesignPoint]`). `cli.py` forwards any CLI flag whose
argparse `dest` matches one of that function's parameter names, so a new
strategy's own flags just need to be declared in `build_parser()` — no
further dispatch changes required.

To add one: write its `run` function plus a resumable state dataclass
following `GreedySearchState`/`Spea2SearchState`/`MesmoSearchState`'s pattern
(a `STRATEGY` class var, `to_dict`/`from_dict`, a `.matches()` that at least
checks `PARAM_SPACE` equality, and `.save()`/`.load()` built on `state.py`'s
shared primitives), then register it in `STRATEGIES`.

`hybrid.py` is a different kind of example worth knowing about: a strategy
doesn't have to write its own state dataclass at all if it's really just
composing existing ones. It calls `mesmo.explore_pareto_front_mesmo()` and
`spea2.explore_pareto_front_spea2()` as-is, each against its own
sub-directory, and reads the first phase's result back via that phase's own
`.load()` to feed the second. Worth following this pattern instead of a
from-scratch state dataclass if a new strategy is fundamentally "run
existing strategy A, then existing strategy B with A's result as a starting
point" rather than a genuinely new search loop.
