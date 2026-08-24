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
| **Actual results** (`sim.out`, `sim.stats.sqlite3`, `power.txt`/`.xml`) | `~/.cache/titan_controller/<cache-hash>/<sim-hash>/<benchmark-name>/<run-idx>/` — see [Where results actually land](#where-results-actually-land) |
| Summary/tracking file (job IDs, not the actual results) | `host_destination_path` from the experiment JSON, e.g. `/tmp/asi_microbench_run/experiments.json` |

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

### Where results actually land

This is the one non-obvious part: `host_destination_path` (e.g.
`/tmp/asi_microbench_run`) only ever gets an `experiments.json` summary
file — the real per-benchmark output directories live under the **local
cache folder**, keyed by a hash you can read out of that summary file:

```bash
python3 -c "
import json
d = json.load(open('/tmp/asi_microbench_run/experiments.json'))
print(d['benchmark_suites'][0]['host_dst_path'])
"
```

Under that path you'll find `<sim-hash>/<benchmark-name>/<run-idx>/`, e.g.:

```
~/.cache/titan_controller/<hash>/<hash>/ML2/0/sim.out
~/.cache/titan_controller/<hash>/<hash>/ML2/0/sim.stats.sqlite3
~/.cache/titan_controller/<hash>/<hash>/ML2/0/power.txt
~/.cache/titan_controller/<hash>/<hash>/ML2/0/power.xml
```

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
