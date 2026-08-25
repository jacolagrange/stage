# Runbook: running ASI microbenchmarks on Titan

This documents the **actual working setup** in this repo, as opposed to the
generic tool documentation in [README.md](README.md). It explains how
everything is wired together, how to submit/collect a run, where results
land, and how to create or modify an experiment.

## TL;DR

```bash
cd ~/school/stage/titan_controller
cargo build --release

# Submit
cargo run --release -- --submit job --path test-run/experiment_c.json

# Check status
cargo run --release -- --list job

# Once it's done, collect (auto-retries anything that failed)
cargo run --release -- --collect job --path /tmp/asi_microbench_run
```

No `SNIPER_ROOT`/`BENCHMARK_ROOT` env vars needed for this config — see
[Why this setup bypasses git checkouts](#why-this-setup-bypasses-git-checkouts).

## How it actually works

`titan_controller`'s original design has Titan check out benchmark/sniper
source itself, from a git branch you name in the experiment JSON
(`checkout_git_repo` in `script-template/job_docker.sh`). **This project
doesn't use that anymore.** Two real problems with it surfaced during
testing:

1. Titan's job-execution filesystem (`~/sniper`, `~/benchmarks` under the
   shared `slurmslave` account) is **local to each compute node**
   (`titan01`, `titan02`, ... `titan11`+) — it is *not* shared with the
   login node (`bacchus`) you land on via `ssh titan`, nor even shared
   *between* compute nodes. Anything you set up via `ssh titan` only
   affects the login node and is invisible to actual jobs.
2. The pre-existing git checkouts on the compute nodes are shared lab
   infrastructure (a large internal benchmark/sniper repo at
   `/mnt/perflab/exascience/src/`, used by other lab members' own branches
   like `bench-jaime`). They don't have our branches, and at least one
   cached commit snapshot turned out to be genuinely corrupted (missing
   files relative to the live checkout at the same commit hash).

### Why this setup bypasses git checkouts

Instead, both Sniper and the benchmarks are plain files sitting on
`/mnt/perflab` — **confirmed shared across every node** (login and
compute) — and mounted directly into the container via `vm_mount` entries
in the experiment JSON, instead of via a `git` key:

```json
"vm_mount": {
    "sniper_mount": "/mnt/perflab/exascience/src/jaco_sniper",
    "benchmarks_mount": "/mnt/perflab/exascience/src/jaco_benchmarks"
}
```

This is why `test-run/experiment_c.json` and `test-run/c_bench.json` have
**no `"git"` key at all** — there's nothing to check out. The build step
(`execute_Sniper.sh`) just `make`s whatever's already sitting at the mount
point, same as always.

If you ever go back to the git-checkout convention for something else,
remember: any `ssh titan` setup you do only reaches the login node, and any
`git` key you use needs a branch that actually exists in whichever repo the
compute nodes' `~/sniper/master` / `~/benchmarks/master` are cloned from
(check with `srun --nodelist=<node> --qos=batch_qos --partition=batch
bash -c '...'`, not plain `ssh titan`).

### One more fix baked in

`snipersim/tools/sniper_lib.py` optionally imports a legacy scheduler
helper (`intelqueue` etc., part of the *shared* benchmarks repo, not ours)
used only for an optional remote-results-fetch feature we don't use. That
helper is old Python 2 code and raises `SyntaxError` on import under
Python 3.12, which wasn't being caught (the code only caught
`ImportError`). Fixed to `except (ImportError, SyntaxError):` so it
degrades gracefully as originally intended. This fix lives in **our own**
`snipersim` copy (both locally and at `jaco_sniper` on Titan) — see
[Updating Sniper](#updating-sniper) if you ever need to re-sync it.

## Where everything lives

| What | Location |
|---|---|
| Experiment/benchmark JSON configs (edit these) | `test-run/*.json` (this repo) |
| `titan_controller` source | `src/` (this repo) |
| Your Sniper source (with TAGE), mounted read-write into every job | Titan: `/mnt/perflab/exascience/src/jaco_sniper` — synced from `~/school/stage/snipersim` |
| Your benchmark source, mounted read-write into every job | Titan: `/mnt/perflab/exascience/src/jaco_benchmarks` — synced from `~/school/stage/asi/benchmarks` |
| Local job-tracking database (don't edit by hand) | `~/.cache/titan_controller/job_info.sqlite3` |
| **Results** (`sim.out`, `sim.stats.sqlite3`, `power.txt`/`.xml`) | `<host_destination_path>/results/<sniper-config-hash>/<benchmark-name>/<run-idx>/` — symlinked here by `--collect`, see [Collecting results](#collecting-results) |
| Local job-tracking metadata (job IDs, not the results themselves) | `<host_destination_path>/experiments.json` |

## Submitting a job

```bash
cd ~/school/stage/titan_controller
cargo run --release -- --submit job --path test-run/experiment_c.json
```

Add `--dry` to validate the config locally (parses the JSON, resolves
paths) without touching Titan at all — always worth doing after editing a
config:

```bash
cargo run --release -- --submit job --path test-run/experiment_c.json --dry
```

Check on it:

```bash
cargo run --release -- --list job                    # currently queued/running
cargo run --release -- --list job --completed 1       # job history, last 1 day
```

## Collecting results

```bash
cargo run --release -- --collect job --path /tmp/asi_microbench_run
```

(`/tmp/asi_microbench_run` is whatever `host_destination_path` you set in
the experiment JSON.)

This downloads each finished task's result tarball, checks it actually
produced a non-empty `sim.out` or `sim.stats.sqlite3`, and **automatically
resubmits anything that failed the check**. Re-run `--collect` again after
a resubmit to pick up the retry.

Every successfully collected result is symlinked into
`<host_destination_path>/results/<sniper-config-hash>/<benchmark-name>/<run-idx>/`
— printed at the end of a successful collect. So for the example above:

```
/tmp/asi_microbench_run/results/4376462735085201861/ML2/0/sim.out
/tmp/asi_microbench_run/results/4376462735085201861/ML2/0/sim.stats.sqlite3
/tmp/asi_microbench_run/results/4376462735085201861/ML2/0/power.txt
/tmp/asi_microbench_run/results/4376462735085201861/ML2/0/power.xml
```

(The symlinks point back into a local cache directory keyed by a hash of
the experiment's mounts — that's what makes re-running an unchanged
experiment reuse cached results instead of resubmitting. `results/` is
just a friendly, stable way to reach the same files; you never need to
touch the cache path directly. The hash directory is there because
[batch experiments](#submitting-a-batch-of-asi-design-points) evaluate
several distinct Sniper configurations against the *same* benchmark names
— `<benchmark>/<run-idx>` alone isn't unique across those, only
`<hash>/<benchmark>/<run-idx>` is. With a `find` you rarely need to know the
hash up front: `find /tmp/asi_microbench_run/results -name sim.out`.)

`sim.out` is the human-readable Sniper summary (IPC, cache/branch-predictor
stats, DRAM). `power.txt`/`power.xml` are McPAT's area/power estimates.
`sim.stats.sqlite3` has the full raw stats if you need more than `sim.out`
shows. To load into pandas, see the `process_scripts` section in
[README.md](README.md#processing-results).

## Making a new ready-to-run experiment JSON

Copy `test-run/experiment_c.json` as a starting point — it's already a
working, minimal example. The fields that matter:

```json
{
    "job": {
        "name": "my_experiment",
        "core_per_experiment": 1,
        "mem_per_core": 2048,
        "vm_name": "sniper2404",
        "runs": 1
    },
    "benchmarks": ["./my_bench.json"],
    "vm_mount": {
        "input_mount": "None",
        "sniper_mount": "/mnt/perflab/exascience/src/jaco_sniper",
        "benchmarks_mount": "/mnt/perflab/exascience/src/jaco_benchmarks"
    },
    "sniper_parameters": {
        "arguments": ["-n", "1", "-s", "stop-by-icount:2000000"],
        "parameters": [{
            "mix": "single",
            "include_first": "true",
            "values": { "in_order": ["true"] }
        }]
    },
    "host_destination_path": "/tmp/my_experiment_run"
}
```

Notes:
- **Give every new/changed experiment a fresh `host_destination_path`.**
  The local tracking DB keys status by a hash of the mounts/git config, not
  by this path — if you change what's mounted but reuse an old
  `host_destination_path` whose tasks are still marked `SUBMITTED`, a fresh
  `--submit` will just say `Experiment is already fully done, nothing to
  do` and silently do nothing. If that happens, either wait for the
  in-flight job to fail and `--collect` it (drains the retry), or reset
  local state entirely with `rm ~/.cache/titan_controller/job_info.sqlite3`
  (purely local, affects no running Titan jobs).
- `sniper_parameters.arguments`: raw `run-sniper` flags applied to every
  benchmark. `-s stop-by-icount:2000000` caps the simulated instruction
  count — raise it for longer/more representative runs.
- `sniper_parameters.parameters[].values`: swept microarchitectural knobs
  (passed as `-g --perf_model/...=value`), e.g. see the commented-out
  `in_order`/`timer`/etc. keys in `script-template/experiment_template.json`
  for the full set this cluster's Sniper build understands.
- `mix: "single"` varies one parameter at a time from a baseline; `"mix":
  "product"` sweeps the full cross-product instead.

## Submitting a batch of ASI design points

Running the ASI framework's search strategies (`greedy`/`spea2`/`mesmo`)
locally evaluates one design point at a time — call Sniper, block, get a
result, decide the next point. That's fine locally, but SPEA2's whole
generation (or greedy's per-round search set) is naturally a *batch* of
independent points, and submitting them together as one Titan job array
lets Slurm actually run them in parallel across compute nodes instead of
one at a time.

**File:** `asi/asi_framework/titan_batch.py`, function
`entities_to_titan_experiment()`.

### How it avoids needing a new titan_controller feature

A batch is a list of (possibly sparse) `params` dicts — the exact same
shape `evaluate_point()`/`runner.run()` already take locally, e.g. a SPEA2
population before evaluation. The obvious way to express "N specific,
already-chosen configurations" doesn't fit titan_controller's existing
`"mix": "product"` (full cross-product of value lists) or `"single"` (vary
one parameter at a time) — neither generates an arbitrary, non-combinatorial
list of points.

The fix needs no new mix mode: `sniper_parameters.parameters` is already an
*array* of blocks, and every block's generated combinations get merged into
one flat list. Give **each entity its own block**, `"mix": "product"`, with
every value list holding exactly one value — the product of one-element
lists is just that one combination, so N entities become N blocks, each one
exact configuration. Verified empirically (`--dry`, inspecting the generated
`<ARGUMENTS>` per task) before trusting it — see the git history for the
test that proved it.

What actually varies per entity is a single `{overrides}` placeholder, whose
value is the *entire* Sniper override-flag string for that entity — built by
calling `config_builder.build_runtime_config()` directly, the exact function
`runner.run()` already uses for local runs. This is deliberate: that
function has real conditional logic (branch-predictor-type-specific knobs,
ROB knobs only supplied when relevant, defaults filled in for a sparse
dict) that a static per-key template can't safely replicate. Reusing it
directly means the Titan path can never drift from local behavior — one
flag string per entity means it doesn't need to.

**One correction from the ASI framework's own convention**: `runner.py`
builds override flags as `-c path=value` (not `-g`, which is what
`script-template/experiment_template.json`'s generic example uses) — verify
which one applies before assuming; they aren't interchangeable.

### Usage

```python
from asi_framework.titan_batch import entities_to_titan_experiment
import json

entities = [point.params for point in population]  # e.g. one SPEA2 generation
experiment = entities_to_titan_experiment(
    entities,
    reference_config="nehalem.cfg",
    benchmark_json_path="./c_bench.json",
    host_destination_path="/tmp/asi_gen_0",  # give each batch its own path
)
json.dump(experiment, open("gen_0.json", "w"))
```

Then submit/poll/collect exactly like any other experiment (see
[Submitting a job](#submitting-a-job) /
[Collecting results](#collecting-results)) — `--collect` symlinks every
entity's result under `results/<sniper-config-hash>/<benchmark-name>/<run-idx>/`,
one sub-tree per entity, so a batch of N entities × M benchmarks resolves
to N distinct hash directories, each containing that entity's M benchmark
results. Parse them back with
`asi_framework.runner.parse_sniper_output(outputdir)` — the exact same
parser `runner.run()` uses locally, so a Titan-collected result and a local
one produce identical `(area, peak_power, time_ns)` tuples.

### No "done" signal — poll lightly, don't busy-loop

titan_controller has no push notification of its own — no webhook, no
callback. The practical options, roughly in order of effort:

1. **Sparse polling** — check `--list job` every minute or so, sleeping
   between checks. This costs essentially nothing; a script asleep 59
   seconds out of 60 isn't "tying up" anything meaningfully.
2. **Slurm's own email notification** (`--mail-type=END
   --mail-user=you@ugent.be` added to the job template) — a real push, but
   to a human, not something that can automatically trigger submitting the
   next generation on its own.
3. **Run the orchestration loop on Titan itself** instead of your laptop —
   the actual answer if you want your laptop uninvolved after kickoff, but
   real infrastructure work (something needs to run persistently on Titan's
   login node or as its own job), not a quick addition.

Start with (1) to get a working batch-generation loop end to end; only move
to (3) once that's proven and the extra infrastructure is worth it.

### What this does *not* do yet

`entities_to_titan_experiment()` and the collection path above are a
general "run a batch of entities on Titan, get raw measurements back"
utility. Rewiring `spea2.py`/`greedy.py`/`mesmo.py`'s own search loops to
actually call this instead of `evaluate_point()`'s one-at-a-time local
calls is a separate, bigger change — each strategy's loop structure,
caching, and resumability all currently assume synchronous one-point-at-a-time
evaluation. That rewiring is future work, not done here.

## Adding or modifying a benchmark

Benchmarks live in **two places that must be kept in sync**: your local
`stage` repo (source of truth, use for all edits) and the Titan mount
(what jobs actually run against).

1. **Edit/add locally** under `~/school/stage/asi/benchmarks/<NAME>/` —
   needs a `bench.c` and a `Makefile` (`include ../make.rules` is enough
   for a plain single-file microbenchmark; see `asi/benchmarks/ML2/` for
   the simplest example, or `asi/benchmarks/CCl/` if it needs a generated
   input array via `rand_arr_args.txt`).

2. **Build and smoke-test it locally first**:
   ```bash
   cd ~/school/stage/asi/benchmarks
   make            # builds every benchmark dir
   ./<NAME>/bench  # should run and exit 0 natively
   make clean      # don't commit compiled binaries
   ```

3. **Sync the whole benchmarks tree to Titan** (fast, it's small):
   ```bash
   cd ~/school/stage/asi/benchmarks
   tar cf - . | ssh titan "tar xf - -C /mnt/perflab/exascience/src/jaco_benchmarks"
   ```

4. **Add it to `test-run/c_bench.json`** (or a new suite file), matching
   the existing entries:
   ```json
   {
       "name": "MY_BENCH",
       "bench_path": "MY_BENCH",
       "binary": "bench",
       "arguments": []
   }
   ```
   `bench_path` must match the directory name under
   `asi/benchmarks/`/`jaco_benchmarks/`. `binary` is the compiled
   executable's name relative to that directory (always `bench` for these
   microbenchmarks, since `make.rules` always outputs `bench`).

5. **Submit with a fresh `host_destination_path`** — remember, reusing an
   old one while jobs are still tracked as `SUBMITTED` from a previous
   attempt silently skips submission (see the note above).

### Updating Sniper

Same idea, just bigger (~1.5GB, ~20,000 files — use `tar`-over-`ssh`, not
`scp -r`, or per-file negotiation makes it painfully slow):

```bash
cd ~/school/stage/snipersim
tar cf - --exclude='.git' . | ssh titan "tar xf - -C /mnt/perflab/exascience/src/jaco_sniper"
```

If you only changed one file, sync just that file instead — much faster:

```bash
scp ~/school/stage/snipersim/tools/sniper_lib.py \
    titan:/mnt/perflab/exascience/src/jaco_sniper/tools/sniper_lib.py
```

## Troubleshooting

- **`--collect` keeps resubmitting a job that actually succeeded** — was a
  real bug (fixed): `retry_experiment()` resubmitted without first seeding
  the local tracking DB with rows for the new paths, so every
  collect-triggered resubmit went untracked and the *next* `--collect` saw
  "never submitted" and resubmitted again, indefinitely, regardless of
  whether the run actually succeeded. If you're on a binary built before
  this fix, rebuild (`cargo build --release`) to pick it up.
- **`Experiment is already fully done, nothing to do`** on a fresh
  `--submit` — see the `host_destination_path` note above. Either collect
  the in-flight job first, or `rm ~/.cache/titan_controller/job_info.sqlite3`.
- **Job fails near-instantly** (under ~10s) — almost always a
  git-checkout branch mismatch, if you're using the `git` convention
  instead of `vm_mount`. Check `stderr_<jobid>_<task>.txt` in the
  downloaded result tarball (`/home/slurmslave/results/results_<jobid>_
  <task>.tar.gz` on Titan) for `branch '...' not found`.
- **Job runs for minutes then fails, "did not pass the tests"** — the
  build itself failed. Check `make_sniper.err`/`make_benchmarks.err` and
  `stderr_vm.txt` in the result tarball.
- **`--delete job` says "Cannot remove a job using this account!"** — the
  tool's delete path needs a privileged account we don't have. Cancel
  directly instead: `ssh titan "scancel <jobid>"`.
- **Need to inspect what a compute node actually sees** (not the login
  node) — `ssh titan` alone isn't enough:
  ```bash
  ssh titan "srun --nodelist=titan01 --qos=batch_qos --partition=batch \
      --time=00:01:00 bash -c '<command>'"
  ```
