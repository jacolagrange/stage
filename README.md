# ASI Framework

Design-space exploration for processor microarchitectures, guided by the
**Architectural Sustainability Indicator (ASI)**. Given a reference Sniper
config and one or more benchmarks, it searches a large, discrete space of
microarchitectural parameters (core type, cache hierarchy, branch predictor,
out-of-order core knobs, ...) for configurations that improve performance
(speedup) without a proportional cost in chip area and power, using
[Sniper](https://snipersim.org/) for timing simulation and
[McPAT](https://github.com/HewlettPackard/mcpat) (via Sniper's `--power`)
for area/power estimation.

One design point already costs a full Sniper run plus a McPAT estimate, and
the parameter space grows multiplicatively — simulating everything isn't
feasible. Four independent **search strategies**, picked via `--strategy`,
each try to find a good Pareto front (ASI vs. speedup) with as few real
simulations as possible: `greedy` (sensitivity-based hill climbing), `spea2`
(evolutionary), `mesmo` (Bayesian optimization), `hybrid` (mesmo, then spea2
seeded from mesmo's result).

## Repository layout

| Directory | What it is | Do you need it? |
|---|---|---|
| `asi/` | The framework itself: Python, search strategies, CLI (`asi.py`), benchmark suite (`asi/benchmarks/`). | Always. |
| `snipersim/` | [Sniper](https://snipersim.org/), the timing simulator, as a git submodule. | Always for real runs (see [Testing without Sniper](#testing-without-sniper) for the exception). |
| `asi/libs/TAGE` | Optional TAGE branch predictor source, as a submodule. | Only for `branch_predictor_type=tage`. |
| `titan_controller/` | Rust CLI, vendored (not a submodule), submits Sniper runs as Slurm jobs on UGent's Titan HPC cluster. | Only for running at real scale. |
| `.githooks/` | Optional hooks keeping the TAGE install in sync after `git submodule update`. | Optional. |

## Table of contents

- [Getting started](#getting-started)
- [Usage](#usage)
- [Testing without Sniper](#testing-without-sniper)
- [Running on Titan (HPC)](#running-on-titan-hpc)

---

## Getting started

**1. Clone**
```bash
git clone --recurse-submodules <this-repo-url> stage && cd stage
# already cloned without --recurse-submodules?
git submodule update --init --recursive
```

**2. Build Sniper**
```bash
cd snipersim && make
```
Builds `snipersim/run-sniper`, what every search actually drives. Takes a
while (bundled toolkits) — that's normal. See
[snipersim's README](snipersim/README.md) /
[snipersim.org/w/Getting_Started](https://snipersim.org/w/Getting_Started)
for platform issues.

**3. (Optional) TAGE branch predictor**
```bash
./scripts/install_tage_predictor.sh   # copies asi/libs/TAGE into snipersim, registers it
cd snipersim && make                  # rebuild to pick it up
```
Only touches `snipersim`'s working tree — safe to re-run after
`git submodule update`. Auto-run on every `submodule update` if you opt into
the repo's git hooks once: `git config core.hooksPath .githooks`.

**4. Python environment**
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r asi/requirements.txt   # numpy, scipy, matplotlib
```
No install step for the framework itself — run it directly (see below).

**5. (Optional) Titan, for real-scale runs** — see
[Running on Titan](#running-on-titan-hpc) below.

## Usage

Run from inside `asi/` (relative-import package, not pip-installed):

```
cd asi
python3 asi.py --config <reference.cfg> --strategy {greedy,spea2,mesmo,hybrid} \
    [--sniper PATH] [--outputdir DIR] [--alpha A] [--iterations N] \
    [--log [PATH]] [--save-plot [PATH]] \
    [strategy-specific flags...] [pre-evaluation flags...] \
    -- <benchmark command> [-- <another benchmark command> ...]
```

One or more benchmark commands **must** follow `--`; repeat `-- ./other_bench
...` to search across several at once (per-benchmark results are geomean'd
into one speedup). A benchmark's display name comes from its executable's
parent directory (matching `benchmarks/<NAME>/bench`, as in
[`asi/benchmarks/`](asi/benchmarks/)).

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
| `--outputdir`, `-d` | `asi/asi-output` | Where outputs, plots, and the resumable state JSON go. |
| `--alpha` | `0.5` | ASI weight (0 = power, 1 = area). |
| `--strategy` | `greedy` | `greedy`, `spea2`, `mesmo`, or `hybrid`. |
| `--iterations` | strategy's own default | Iterations/generations/BO-iterations. Unset lets the strategy pick its own (5 greedy, 30 others). |
| `--log [PATH]` | off | Tee output to `PATH` (default `outputdir/run.log`). |
| `--save-plot [PATH]` | off | Save final Pareto plot (default `outputdir/pareto.png`). |

### `spea2` flags

| Flag | Default | Meaning |
|---|---|---|
| `--populations` | 3 | Independent SPEA2 populations. |
| `--population-size` | 20 | Entities per population. |
| `--archive-size` | 10 | Pareto-archive size per population. |
| `--p-mutation` | 0.10 | Per-child mutation probability. |
| `--p-crossover` | 0.90 | Per-child crossover probability (vs. plain copy). |
| `--p-migration` | 0.10 | Per mating-pool slot, chance of drawing from another population's archive. |
| `--patience` | 5 | Stop after this many generations with no hypervolume gain. |
| `--seed` | 0 | RNG seed (also used by mesmo). |
| `--titan` | off | Evaluate each generation as one Titan batch job instead of local runs. Requires `--titan-benchmark-json`. |
| `--titan-benchmark-json` | — | titan_controller benchmark JSON covering the same benchmark names given after `--`. |
| `--titan-dir` | `titan_controller/` next to this repo | Path to the titan_controller checkout. |
| `--titan-host-dir` | `outputdir/titan` | Where Titan results land locally. |
| `--titan-sniper-mount` / `--titan-benchmarks-mount` | this project's own mounts | Sniper/benchmarks paths mounted on Titan. |
| `--titan-poll-interval` | 30 | Seconds between polls while waiting on a batch job. |

### `mesmo` flags

| Flag | Default | Meaning |
|---|---|---|
| `--num-initial-points` | 5 | Baseline + this-minus-one random configs to seed the GP. |
| `--candidate-pool-size` | 200 | Fresh candidates scored per iteration. |
| `--batch-size` | 1 | Top-ranked candidates evaluated per iteration before refitting the GP. |
| `--mc-samples` | 10 | Monte-Carlo samples for the acquisition function. |
| `--gp-features` | 250 | Random Fourier features per GP. |
| `--gp-lengthscale` | median-heuristic | Fix the GP kernel lengthscale instead of re-deriving it each iteration. |
| `--gp-noise` | 1e-4 | GP observation noise variance. |
| `--mesmo-patience` | unset (disabled) | Stop after this many iterations with no gain. Off by default — one iteration can be a single evaluation, too noisy for a short window. |

### `hybrid` flags

Mesmo-then-spea2: accepts every `spea2`/`mesmo` flag above (incl. `--seed`,
shared by both phases), plus its own mesmo-phase budget. `--iterations`
configures the **spea2** phase. `--mesmo-patience` defaults to **5** here
(unlike standalone mesmo's "unset") — plateau detection is this phase's
whole point.

| Flag | Default | Meaning |
|---|---|---|
| `--mesmo-phase-iterations` | 30 | Max MESMO iterations before switching to SPEA2 (usually stops earlier via `--mesmo-patience`). |

### Pre-evaluation screening flags

Independent of `--strategy` — runs first if `--preeval-samples > 0` or
`--preeval-cache`, and prunes unimportant parameters to their default-only
value before the chosen strategy starts.

| Flag | Default | Meaning |
|---|---|---|
| `--preeval-samples` | 0 | 0 disables screening. Sample count for `perceptron`; on/off switch for `plackett_burman`. |
| `--preeval-method` | `perceptron` | `perceptron` or `plackett_burman`. |
| `--preeval-threshold` | 0.1 | Keep a parameter only if its importance clears this fraction of the top parameter's. |
| `--preeval-seed` | 0 | RNG seed for perceptron sampling (`plackett_burman` is deterministic). |
| `--preeval-cache` | off | Reuse a previous screen's cache (`outputdir/preeval/screen_cache.json`) instead of re-screening. Errors if none exists; mutually exclusive with `--preeval-samples`. |

A completed screen is cached to `outputdir/preeval/screen_cache.json` and
reused automatically on a later matching call, so changing an unrelated
strategy flag doesn't re-pay for screening's own Sniper runs.

### Output layout

Under `--outputdir`:
- `baseline/` — the reference config's own run.
- Per-strategy working dirs per evaluated point (`iter{N}_run{i}` greedy,
  `gen{N}_pop{P}_ent{E}` spea2, `init{i}`/`iter{N}_cand{rank}` mesmo) —
  deleted once off the Pareto front, so disk use stays bounded.
- `search_state.json` — resumable checkpoint; re-running the same command
  against the same `--outputdir` continues instead of restarting (as long as
  config/benchmarks/alpha/param-space match).
- `pareto_history.png` / `pareto_final.png` — always written; `pareto.png`
  only with `--save-plot`.
- `hv_vs_sims.png` — always written: hypervolume + front size vs. cumulative
  real simulations spent.

**`--strategy hybrid`** writes into two sub-directories instead,
`outputdir/mesmo_phase/` and `outputdir/spea2_phase/`, each an independently
resumable ordinary run of that strategy (so one phase's checkpoint can't
overwrite the other's).

## Testing without Sniper

**File:** `tests/test.py`. Exercises `greedy.py`'s sensitivity/freezing
logic against a synthetic, seeded stand-in for Sniper — no simulator needed,
runs in seconds.

```bash
python3 asi/tests/test.py
```

The synthetic model's ground truth (which parameters "really" matter) is
known up front, so the printed final Pareto front can be checked by eye:
real-effect parameters stay active, null ones freeze within the first
couple of iterations.

## Running on Titan (HPC)

### Why

One design point = one full Sniper run per benchmark. A real search
(`spea2`, default 3×20 entities, 30 generations, several benchmarks) is
thousands of runs — days, sequentially, on a laptop. **Titan** is UGent
ELIS's HPC cluster: a login node (`bacchus`) plus compute nodes (`titan01`
... `titan11`+), scheduled by **Slurm**. `titan_controller/` (vendored Rust
CLI) packages a run description as a Slurm **job**, submits it over SSH, and
fetches results back. The ASI framework's `--titan` flag drives it
automatically — you don't normally touch `titan_controller` directly.

### One-time setup

1. **Titan account + SSH access** (UGent/ELIS-specific, ask your lab). Titan's
   login node is usually only reachable via an SSH **jump host** first;
   `ProxyJump` automates the hop:
   ```
   Host titan
       Hostname bacchus.ugent.be
       User <your-titan-account>
       IdentityFile ~/.ssh/<your-titan-key>
       IdentitiesOnly yes
       ProxyJump <your-jump-host-alias>

   Host <your-jump-host-alias>
       HostName <jump-host>.ugent.be
       User <your-jump-account>
       Port <port, if non-standard>
       IdentityFile ~/.ssh/<your-jump-key>
   ```
   Test with `ssh titan`. Prompted for a password on *every* command later
   (not just once)? Add connection multiplexing to both blocks above
   (`mkdir -p ~/.ssh/controlmasters` first):
   ```
       ControlMaster auto
       ControlPath ~/.ssh/controlmasters/%r@%h:%p
       ControlPersist 4h
   ```
2. **`.id` file** — `titan_controller` needs `~/.config/titan_controller/.id`
   (prints the exact expected path if missing). Get its contents from your
   Titan account admin.
3. **Build:**
   ```bash
   cd titan_controller && cargo build --release
   ```
   Needs `cargo` + SQLite dev headers (`apt install libsqlite3-dev
   pkg-config`). Run day-to-day via `cargo run --release -- <args>`.
4. **Your own Sniper + benchmarks, mounted where compute nodes can see
   them** — see next section.

### Architecture: no git checkouts

`titan_controller` can have Titan `git`-checkout your source per job, but
**this project doesn't use that** — compute nodes don't share a filesystem
with the login node or each other (a checkout done via `ssh titan` is
invisible to the actual job), and the shared lab checkouts don't have this
project's branches (one was found corrupted, too). Instead, plain files are
mounted directly via `vm_mount`, pointed at a location confirmed shared
across every node:
```json
"vm_mount": {
    "sniper_mount": "/mnt/perflab/exascience/src/jaco_sniper",
    "benchmarks_mount": "/mnt/perflab/exascience/src/jaco_benchmarks"
}
```
No `"git"` key anywhere in this project's experiment JSONs. **You're
responsible for keeping those two mounted dirs in sync with your local
`snipersim`/`asi/benchmarks`** — see
[Adding a benchmark](#adding-or-modifying-a-benchmark).

### The two JSON files

**Benchmark JSON** — what programs to run:
```json
{"suites": [{
    "suite": "asi_microbench", "type": "binaries", "suite_path": ".",
    "sniper_args": ["--roi"],
    "benchmarks": [
        {"name": "ML2", "bench_path": "ML2", "binary": "bench", "arguments": []},
        {"name": "CCl", "bench_path": "CCl", "binary": "bench", "arguments": []}
    ]
}]}
```
`bench_path` = directory name under the mounted benchmarks tree; `binary` =
executable inside it. This project's copy:
`titan_controller/test-run/c_bench.json` (ML2, ML2_orig, CCl, MIP, EI).

**Experiment JSON** — what Sniper config(s) to run those benchmarks under,
and where results land locally:
```json
{
    "job": {"name": "my_experiment", "core_per_experiment": 1, "mem_per_core": 2048, "vm_name": "sniper2404", "runs": 1},
    "benchmarks": ["./c_bench.json"],
    "vm_mount": {"input_mount": "None", "sniper_mount": "/mnt/perflab/exascience/src/jaco_sniper", "benchmarks_mount": "/mnt/perflab/exascience/src/jaco_benchmarks"},
    "sniper_parameters": {
        "arguments": ["-c", "gainestown", "-s", "stop-by-icount:2000000"],
        "parameters": [{"mix": "single", "include_first": "true", "values": {"in_order": ["true", "false"]}}]
    },
    "host_destination_path": "/tmp/my_experiment_run"
}
```
- `sniper_parameters.arguments` — flags always passed to `run-sniper`.
- `sniper_parameters.parameters` — array of sweep blocks; `"single"` varies
  one parameter at a time, `"product"` sweeps the full cross-product. Every
  block's combinations merge into one flat task list (the mechanism
  `--titan` repurposes below).
- `host_destination_path` — where results land **locally** after
  `--collect` (not the real cache storage, see below).

Starting point: `titan_controller/test-run/experiment_c.json`.

### Commands

```bash
cd titan_controller
cargo run --release -- --submit job --path test-run/experiment_c.json          # submit
cargo run --release -- --submit job --path test-run/experiment_c.json --dry    # validate only, no Titan
cargo run --release -- --list job                                              # what's queued/running
cargo run --release -- --collect job --path /tmp/my_experiment_run             # fetch results (dir, not the JSON file!)
```

`--submit` computes a hash per Sniper config (see below) and submits
whatever's new as **one Slurm job** that fans out into parallel tasks via a
Slurm **job array** — batching many design points into one experiment JSON
doesn't cost parallelism; Slurm still schedules every task independently
across free nodes.

`--collect` downloads finished tasks, checks they produced real output, and
auto-resubmits anything that failed (run `--collect` again to pick up the
retry). No push notification exists — anything driving this (including
`--titan`) has to poll `--list job`; every 20-60s is the practical default.

### Caching, results, and a sharp edge

Every Sniper config is identified by a **hash of its fully resolved
settings** (not the experiment or `host_destination_path`). Results live
under a cache root keyed by a **second hash** of the mounted source state:
```
~/.cache/titan_controller/<source-state-hash>/<sniper-config-hash>/<benchmark-name>/<run-idx>/
```
- Submitting an identical config twice — even from unrelated experiments —
  only computes it once. `Experiment is already fully done, nothing to
  do... bye` is a **cache hit, not a failure**.
- `<host_destination_path>/results/<hash>/<benchmark>/<run-idx>/` is a
  **symlink** into that cache, not a copy — a browsable path
  (`find ... -name sim.out`) without needing the hash up front.
- `sim.out` = Sniper's summary; `power.txt`/`power.xml` = McPAT
  area/power; `sim.stats.sqlite3` = full raw stats.

**Sharp edge:** the hash is computed from the *resolved* config, understood
via exactly two forms of Sniper's `-c` flag: `-c <configname>` (load a whole
`.cfg`) and `-c <section/key>=<value>` (single-knob override). Anything else
silently fails to parse and is **dropped from the hash without an error** —
this previously let differently-configured entities collide onto the same
cache directory and overwrite each other's results (now fixed; only matters
if you're writing `sniper_parameters` by hand instead of going through
`config_builder.build_runtime_config()`, which always produces one of the
two valid forms).

### Adding or modifying a benchmark

Two places to keep in sync: your local repo (source of truth) and the Titan
mount.

```bash
# 1. add/edit under asi/benchmarks/<NAME>/ (needs bench.c + Makefile with
#    `include ../make.rules`; see asi/benchmarks/ML2/)
# 2. build + smoke-test locally
cd asi/benchmarks && make && ./<NAME>/bench && make clean   # don't commit binaries
# 3. sync to Titan
tar cf - . | ssh titan "tar xf - -C /mnt/perflab/exascience/src/jaco_benchmarks"
# 4. add to the benchmark JSON, e.g. titan_controller/test-run/c_bench.json:
#    {"name": "MY_BENCH", "bench_path": "MY_BENCH", "binary": "bench", "arguments": []}
# 5. submit with a fresh host_destination_path
```

Updating Sniper on the mount: same idea, bigger (~1.5GB) — use
`tar`-over-`ssh`, not `scp -r`:
```bash
cd snipersim
tar cf - --exclude='.git' . | ssh titan "tar xf - -C /mnt/perflab/exascience/src/jaco_sniper"
```
Or `scp` a single changed file directly if that's all that changed.

### The `--titan` flag (spea2 only)

`asi/asi_framework/titan_batch.py` drives `titan_controller` directly, no
new `titan_controller` feature needed. `--titan --titan-benchmark-json
<path>` submits each generation as one batch job instead of evaluating
locally; without it, `spea2` behaves exactly as before.

- **One block per entity.** A generation is a list of already-decided
  points, not a sweep — `titan_batch.py` gives each its own
  `"mix": "product"` block with every value list holding exactly one value
  (product of one-element lists = that one combination), so N points become
  N single-combination blocks. The one thing that varies per block is a
  `{overrides}` placeholder — the *entire* Sniper override-flag string for
  that point, built via `config_builder.build_runtime_config()`, the same
  function local runs use, so Titan can never compute something a local run
  wouldn't.
- **Submit and wait.** Every entity is checked against the framework's own
  Python-side `global_cache` first — cache hits never touch Titan.
  Everything else submits together, polls `--list job` at
  `--titan-poll-interval`, then `--collect`s once the queue's clear (or
  skips straight to reading cached results if `--submit` already reported
  everything done).
- **Matching results back.** Two entities can end up with identical
  parameters (e.g. crossover producing a duplicate child) — `titan_batch.py`
  matches each one back to its result by its own override string (recorded
  per-entry in `experiments.json`), not by list position, so duplicates
  resolve correctly regardless of how `titan_controller` orders its output.
- Results are parsed with the same `runner.parse_sniper_output()` a local
  run uses — Titan and local points are interchangeable in `global_cache`.

### Troubleshooting

- **`Experiment is already fully done, nothing to do`** — not an error, a
  cache hit (see above); the exact config was already computed, maybe by an
  unrelated experiment.
- **Password prompt on every command** — set up SSH multiplexing (see
  [One-time setup](#one-time-setup)).
- **Job fails near-instantly (<10s)** — check
  `stderr_<jobid>_<task>.txt` in the result tarball for a git-branch
  mismatch; only relevant if using the git-checkout convention instead of
  `vm_mount`.
- **Job runs minutes then "did not pass the tests"** — build failed on the
  compute node; check `make_sniper.err`/`make_benchmarks.err`/`stderr_vm.txt`
  in the tarball.
- **`--delete job` says "Cannot remove a job using this account!"** — needs
  a privileged account this project doesn't have. Cancel directly:
  `ssh titan "scancel <jobid>"`.
- **See what a compute node actually sees** (not the login node):
  ```bash
  ssh titan "srun --nodelist=titan01 --qos=batch_qos --partition=batch --time=00:01:00 bash -c '<command>'"
  ```
- **Full local reset** if something looks stuck/corrupted:
  `rm -rf ~/.cache/titan_controller/` — purely local, affects no running
  jobs, everything regenerates (just without prior cached results).
