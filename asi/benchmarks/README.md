# Benchmarks

A small, hand-picked subset of Tony Nowatzki's
[microbench](https://github.com/VerticalResearchGroup/microbench) suite —
extremely simple microbenchmarks, each targeting one specific
micro-architectural feature or effect, useful for validating and
characterizing an out-of-order core. See [`README`](README) for the
original author's notes.

Only the benchmarks this project actually uses are vendored here (not the
full upstream suite):

| Name | What it stresses |
|---|---|
| `ML2` | L2-resident linked-list traversal, 4MiB working set |
| `ML2_orig` | Same, 1MiB working set |
| `CCl` | Hard-to-predict control flow with large basic blocks |
| `MIP` | Large instruction footprint / instruction-cache misses |
| `EI` | Independent integer execution (ILP-bound) |

## Building

```bash
cd asi/benchmarks
make            # builds every benchmark listed above into <name>/bench
make clean      # removes the compiled binaries
```

Each benchmark is standalone — no arguments, no external inputs beyond
what's already in its own directory (e.g. `CCl/rand_arr_args.txt` for its
generated input array).

## Adding another one

See [the main README's benchmark section](../../README.md) for the
framework side of this, and
[titan_controller/RUNBOOK.md](../../titan_controller/RUNBOOK.md#adding-or-modifying-a-benchmark)
if you also need it running on Titan.
