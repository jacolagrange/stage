#!/usr/bin/env python3
"""
Real (actually Titan/Sniper-evaluated, not resampled) random-search
baseline. Draws --budget distinct random configurations from the current
asi_framework/param_space.json and evaluates every one through Titan via
the same evaluate_batch() path every real search strategy (mesmo/spea2/
hybrid) uses, so cache hits are free and only genuinely new configs cost
Sniper time.

Unlike scripts/random_baseline_check.py (which resamples from an already
exhaustively-swept pool -- only possible on a space small enough to sweep
in full), this is for a space too large to ever exhaustively sweep, where
the only way to know whether a search run's hypervolume beats chance is to
actually run the random sample too, at the same footing (real simulations,
not resampling).

Run from this directory (same convention as asi.py):
    python3 random_search_baseline.py --config gainestown.cfg \\
        --budget 2165 --seed 0 \\
        --titan --titan-benchmark-json /home/jaco/school/stage/titan_controller/test-run/c_bench.json \\
        --outputdir asi-output/random_search_baseline \\
        --target-hv 2.0245 \\
        --log \\
        -- ./benchmarks/ML2/bench -- ./benchmarks/ML2_orig/bench -- ./benchmarks/CCl/bench \\
        -- ./benchmarks/MIP/bench -- ./benchmarks/EI/bench

--budget 2165 matches "Plackett-Burman + Hybrid: focus on SPEA2 (seed = 3)"
(results_titan.tex), the best-HV run (2.0245) among the seven pre-
Verification sections that used this same richer parameter space -- see
--target-hv above. That comparison is NOT a controlled same-space
experiment (this script draws from whatever PARAM_SPACE is live right now,
which may differ from what that run used) -- it is printed for reference
only, clearly labeled as such.

Resumable: the sampled configs are written to <outputdir>/sampled_configs.json
once, up front, and every finished chunk's results are appended to
<outputdir>/points.json as they complete. Rerunning the exact same command
after a crash or Ctrl-C reloads both files and only submits whatever chunks
are still missing -- nothing already-collected is resubmitted.

Add --dry-run to just sample and print the configs (no Sniper/Titan work at
all) as a quick sanity check before committing to the real run.
"""
from __future__ import annotations

import argparse
import contextlib
import json
import random
import sys
import time
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from asi_framework.cli import _benchmark_name
from asi_framework.config import DEFAULT_ALPHA, PARAM_SPACE, RUN_SNIPER
from asi_framework.display import print_pareto_table
from asi_framework.evaluation import compute_baseline
from asi_framework.metrics import hypervolume, params_key, update_pareto_front
from asi_framework.search_ops import modified_params, random_entity
from asi_framework.state import point_from_dict, point_to_dict, write_json_atomic
from asi_framework import titan_batch


class _Tee:
    """Write to multiple streams simultaneously -- mirrors asi.py's own _Tee."""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, data: str) -> None:
        for s in self._streams:
            s.write(data)

    def flush(self) -> None:
        for s in self._streams:
            s.flush()

    def isatty(self) -> bool:
        return False


@contextlib.contextmanager
def _tee_stdout(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        old = sys.stdout
        sys.stdout = _Tee(old, f)
        try:
            yield
        finally:
            sys.stdout = old


def _sample_distinct_configs(budget: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    seen: set[frozenset] = set()
    configs: list[dict] = []
    while len(configs) < budget:
        entity = random_entity(rng, PARAM_SPACE)
        key = params_key(entity)
        if key in seen:
            continue
        seen.add(key)
        configs.append(entity)
    return configs


def _config_key(params: dict) -> str:
    return json.dumps(params, sort_keys=True)


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default="gainestown.cfg",
                         help="Reference Sniper config file (bare filename).")
    parser.add_argument("--sniper", default=str(RUN_SNIPER), help="Path to run-sniper.")
    parser.add_argument("--outputdir", "-d", default="asi-output/random_search_baseline")
    parser.add_argument("--alpha", type=float, default=DEFAULT_ALPHA)
    parser.add_argument("--budget", type=int, required=True,
                         help="Distinct random configs to evaluate, e.g. 2165 to match an "
                              "existing search run's Configurations-evaluated count.")
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--chunk-size", type=int, default=50,
                         help="Configs per Titan job chunk (matches full_factorial's own default).")
    parser.add_argument("--titan-max-concurrent", dest="titan_max_concurrent", type=int, default=8,
                         help="Chunks kept submitted to Titan at once -- the instant one finishes, "
                              "its slot is immediately refilled with the next chunk (rolling window, "
                              "same mechanism explore_full_factorial uses for the big sweeps; 8 "
                              "matches what full_factorial_run_xl used).")
    parser.add_argument("--target-hv", type=float, default=None,
                         help="An existing run's own hypervolume, printed alongside this run's "
                              "for reference. NOT a controlled same-space comparison.")
    parser.add_argument("--log", nargs="?", const="auto", default=None,
                         help="Tee stdout to <outputdir>/run.log (or a given path).")
    parser.add_argument("--dry-run", action="store_true",
                         help="Only sample+save the configs, no Sniper/Titan work.")

    titan_group = parser.add_argument_group("titan")
    titan_group.add_argument("--titan", action="store_true")
    titan_group.add_argument("--titan-benchmark-json", dest="titan_benchmark_json")
    titan_group.add_argument("--titan-dir", dest="titan_dir")
    titan_group.add_argument("--titan-host-dir", dest="titan_host_dir")
    titan_group.add_argument("--titan-sniper-mount", dest="titan_sniper_mount",
                              default="/mnt/perflab/exascience/src/jaco_sniper")
    titan_group.add_argument("--titan-benchmarks-mount", dest="titan_benchmarks_mount",
                              default="/mnt/perflab/exascience/src/jaco_benchmarks")
    titan_group.add_argument("--titan-poll-interval", dest="titan_poll_interval",
                              type=float, default=30.0)

    argv = sys.argv[1:]
    separators = [i for i, a in enumerate(argv) if a == "--"]
    args = parser.parse_args(argv[:separators[0]] if separators else argv)

    if not args.dry_run:
        if not separators:
            parser.error("no benchmark command given after '--' (required unless --dry-run)")
        bounds = separators + [len(argv)]
        segments = [argv[s + 1:e] for s, e in zip(bounds[:-1], bounds[1:])]
        segments = [seg for seg in segments if seg]
        if not segments:
            parser.error("no benchmark command given after '--'")
        used_names: set[str] = set()
        benchmarks = {_benchmark_name(seg, used_names): seg for seg in segments}

        if args.titan and not args.titan_benchmark_json:
            parser.error("--titan requires --titan-benchmark-json")

        sniper = Path(args.sniper).expanduser().resolve()
        if not sniper.exists():
            parser.error(f"run-sniper not found: {sniper}")

    outputdir = Path(args.outputdir).expanduser().resolve()
    outputdir.mkdir(parents=True, exist_ok=True)

    log_path = None
    if args.log is not None and not args.dry_run:
        log_path = outputdir / "run.log" if args.log == "auto" else Path(args.log)

    with (_tee_stdout(log_path) if log_path else contextlib.nullcontext()):
        if log_path:
            print(f"Logging to {log_path}\n")

        sampled_path = outputdir / "sampled_configs.json"
        if sampled_path.exists():
            configs = json.loads(sampled_path.read_text())
            print(f"Reusing {len(configs)} previously-sampled configs from {sampled_path}")
            if len(configs) != args.budget:
                print(f"  NOTE: --budget {args.budget} differs from the {len(configs)} configs "
                      f"already sampled there -- using the {len(configs)} on disk. Delete "
                      f"{sampled_path} first to resample at a new budget.")
        else:
            print(f"Sampling {args.budget} distinct random configurations (seed={args.seed})...")
            configs = _sample_distinct_configs(args.budget, args.seed)
            write_json_atomic(sampled_path, configs)
            print(f"  -> saved to {sampled_path}")
        print()

        if args.dry_run:
            print(f"--dry-run: sampled {len(configs)} configs, no Sniper/Titan work done.")
            return 0

        start = time.monotonic()
        print("Benchmarks: " + ", ".join(
            f"{name} ({' '.join(cmd)})" for name, cmd in benchmarks.items()
        ) + "\n")

        titan_config = titan_batch.build_config(
            args.titan, outputdir, args.titan_benchmark_json, args.titan_dir, args.titan_host_dir,
            args.titan_sniper_mount, args.titan_benchmarks_mount, args.titan_poll_interval,
        )

        points_path = outputdir / "points.json"
        done_records = json.loads(points_path.read_text()) if points_path.exists() else []
        done_by_key: dict[str, tuple] = {}
        for rec in done_records:
            point = point_from_dict(rec["point"]) if rec["point"] is not None else None
            done_by_key[rec["key"]] = (point, rec["ran"], rec["invocations"])
        if done_records:
            print(f"Resuming: {len(done_records)} configs already recorded in {points_path}\n")

        print("Running baseline...")
        baseline = compute_baseline(args.config, sniper, outputdir / "baseline", benchmarks)
        print(f"  Area={baseline.area:.2f} mm^2  PeakPow={baseline.peak_power:.2f} W\n")

        # Rolling window of up to --titan-max-concurrent chunks in flight at
        # once (same mechanism greedy.explore_full_factorial uses): the
        # instant any one chunk finishes, its slot is refilled with the next
        # chunk immediately, instead of waiting for the whole chunk-of-50 to
        # drain before the next chunk is even submitted.
        chunks = [configs[i:i + args.chunk_size] for i in range(0, len(configs), args.chunk_size)]
        chunk_pending: dict[int, list[tuple[int, dict, str]]] = {}
        pending_chunk_queue: list[int] = []
        for chunk_idx, chunk_params in enumerate(chunks):
            pending = [
                (i, params, key) for i, (params, key) in
                enumerate(zip(chunk_params, (_config_key(p) for p in chunk_params)))
                if key not in done_by_key
            ]
            if pending:
                chunk_pending[chunk_idx] = pending
                pending_chunk_queue.append(chunk_idx)
            else:
                print(f"Chunk {chunk_idx + 1}/{len(chunks)}: already fully done, skipping.")

        @dataclass
        class _InFlight:
            chunk_idx: int
            titan_chunk: titan_batch.PendingChunk

        def submit_next() -> "_InFlight | None":
            if not pending_chunk_queue:
                return None
            chunk_idx = pending_chunk_queue.pop(0)
            pending = chunk_pending[chunk_idx]
            entities = [
                (params, outputdir / f"cfg{chunk_idx}_{i}", modified_params(params))
                for i, params, _key in pending
            ]
            print(f"Submitting chunk {chunk_idx + 1}/{len(chunks)} ({len(pending)} new config(s))...")
            titan_chunk = titan_batch.submit_chunk(
                entities, args.config,
                titan_controller_dir=titan_config["titan_controller_dir"],
                benchmark_json_path=titan_config["benchmark_json_path"],
                host_destination_path=titan_config["host_destination_path"] / f"chunk{chunk_idx}",
                sniper_mount=titan_config["sniper_mount"],
                benchmarks_mount=titan_config["benchmarks_mount"],
                job_name=f"asi_randbase_chunk{chunk_idx}",
            )
            return _InFlight(chunk_idx=chunk_idx, titan_chunk=titan_chunk)

        in_flight: list[_InFlight] = []
        for _ in range(max(1, args.titan_max_concurrent)):
            ic = submit_next()
            if ic is None:
                break
            in_flight.append(ic)
        total_chunks_to_run = len(pending_chunk_queue) + len(in_flight)
        completed_chunks = 0
        print(f"\n{total_chunks_to_run} chunk(s) to run, up to {args.titan_max_concurrent} "
              f"in flight at once.\n")

        while in_flight:
            progressed = False
            list_out = titan_batch.list_jobs(titan_config["titan_controller_dir"])
            still_running: list[_InFlight] = []
            for ic in in_flight:
                if ic.titan_chunk.job_ids and any(jid in list_out for jid in ic.titan_chunk.job_ids):
                    still_running.append(ic)
                    continue
                results = titan_batch.try_collect_chunk(
                    ic.titan_chunk, args.config, benchmarks, baseline, args.alpha,
                    titan_controller_dir=titan_config["titan_controller_dir"],
                )
                if results is None:
                    still_running.append(ic)  # --collect auto-resubmitted a failed task
                    continue
                progressed = True
                completed_chunks += 1
                for (i, _params, key), result in zip(chunk_pending[ic.chunk_idx], results):
                    done_by_key[key] = result
                    point, ran, invocations = result
                    done_records.append({
                        "key": key,
                        "point": point_to_dict(point) if point is not None else None,
                        "ran": ran, "invocations": invocations,
                    })
                write_json_atomic(points_path, done_records)
                elapsed = time.monotonic() - start
                print(f"  Chunk {ic.chunk_idx + 1}/{len(chunks)} collected "
                      f"({completed_chunks}/{total_chunks_to_run} chunks done), "
                      f"{len(done_by_key)}/{len(configs)} configs recorded, elapsed {elapsed:.0f}s\n")

                next_ic = submit_next()
                if next_ic is not None:
                    still_running.append(next_ic)
            in_flight = still_running
            if not progressed and in_flight:
                time.sleep(titan_config["poll_interval"])

        # Every config is now on record (either just-collected, or already
        # done from a prior run of this same command) -- one final pass
        # builds the true front/totals rather than accumulating them
        # incrementally out of chunk-completion order.
        pareto_front: list = []
        total_runs = 0
        total_invocations = 0
        for params in configs:
            point, ran, invocations = done_by_key[_config_key(params)]
            total_runs += int(ran)
            total_invocations += invocations
            if point is not None:
                pareto_front = update_pareto_front(pareto_front, [point])

        print("=== Final Pareto Front ===")
        print_pareto_table(pareto_front)
        final_hv = hypervolume(pareto_front)
        elapsed = time.monotonic() - start
        print(f"\nConfigurations evaluated: {total_runs}")
        print(f"Total sniper invocations: {total_invocations}")
        print(f"Final hypervolume: {final_hv:.4f}")
        print(f"Total elapsed time: {elapsed:.0f}s")

        if args.target_hv is not None:
            beats = "beats" if final_hv > args.target_hv else "does not beat"
            print(f"\nReference: this random baseline (HV={final_hv:.4f}, {len(configs)} configs) "
                  f"{beats} the reference run's HV {args.target_hv:.4f}. NOT a controlled "
                  f"same-space comparison -- see this script's docstring.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
