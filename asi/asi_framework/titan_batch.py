"""Builds and runs a titan_controller batch experiment for a whole set of
design points at once, instead of one local Sniper run at a time. See
titan_controller/RUNBOOK.md's "Submitting a batch of ASI design points"."""

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import ROOT
from .config_builder import build_runtime_config
from .metrics import params_key
from .evaluation import cached_point, finalize_point
from .models import DesignPoint
from .runner import parse_sniper_output


def build_config(
    titan: bool, outputdir: Path, titan_benchmark_json: str | None, titan_dir: str | None,
    titan_host_dir: str | None, titan_sniper_mount: str, titan_benchmarks_mount: str,
    titan_poll_interval: float,
) -> dict[str, Any] | None:
    """None when titan=False -- callers fall back to local per-entity
    evaluation, unchanged. Shared by spea2.py, mesmo.py, screening.py."""
    if not titan:
        return None
    if not titan_benchmark_json:
        raise ValueError("titan=True requires titan_benchmark_json (the titan_controller "
                          "benchmark JSON covering the same benchmark names given via --).")
    return {
        "titan_controller_dir": Path(titan_dir) if titan_dir else ROOT / "titan_controller",
        "benchmark_json_path": titan_benchmark_json,
        "host_destination_path": Path(titan_host_dir) if titan_host_dir else outputdir / "titan",
        "sniper_mount": titan_sniper_mount,
        "benchmarks_mount": titan_benchmarks_mount,
        "poll_interval": titan_poll_interval,
    }


def _entity_overrides(entity: dict[str, Any], reference_config: str) -> str:
    """The exact Sniper override-flag string for one entity -- shared by
    entities_to_titan_experiment() (to build the submitted JSON) and
    evaluate_batch() (to look its result back up by content afterward, since
    duplicate entities make positional matching against Titan's own
    simulator_parameters order unreliable)."""
    knobs = dict(entity)
    cores = knobs.pop("cores", 1)
    flags = build_runtime_config(reference_config, **knobs)
    return " ".join(["-n", str(cores)] + flags)


def entities_to_titan_experiment(
    entities: list[dict[str, Any]],
    reference_config: str,
    benchmark_json_path: str,
    host_destination_path: str,
    *,
    job_name: str = "asi_batch",
    sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    core_per_experiment: int = 1,
    mem_per_core: int = 2048,
    vm_name: str = "sniper2404",
) -> dict[str, Any]:
    """Build a titan_controller experiment dict for one batch of (possibly
    sparse) params dicts, ready to json.dump() and hand to --submit. Give
    each batch its own host_destination_path -- see RUNBOOK.md."""
    parameters = [
        {
            "mix": "product",
            "include_first": "true",
            "values": {"overrides": [_entity_overrides(entity, reference_config)]},
        }
        for entity in entities
    ]

    return {
        "job": {
            "name": job_name,
            "core_per_experiment": core_per_experiment,
            "mem_per_core": mem_per_core,
            "vm_name": vm_name,
            "runs": 1,
        },
        "benchmarks": [benchmark_json_path],
        "vm_mount": {
            "input_mount": "None",
            "sniper_mount": sniper_mount,
            "benchmarks_mount": benchmarks_mount,
        },
        "sniper_parameters": {
            "arguments": ["-c", str(reference_config), "{overrides}"],
            "parameters": parameters,
        },
        "host_destination_path": host_destination_path,
    }


_JOBID_RE = re.compile(r"jobid (\S+)\)")


def _run_titan(titan_controller_dir: Path, args: list[str]) -> str:
    result = subprocess.run(
        ["cargo", "run", "--release", "--"] + args,
        cwd=str(titan_controller_dir),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        raise RuntimeError(f"titan_controller {args} exited {result.returncode}:\n{result.stdout}")
    return result.stdout


def evaluate_batch(
    entities: list[tuple[dict[str, Any], Path, set[str]]],
    reference_config: str,
    benchmarks: dict[str, list[str]],
    baseline: DesignPoint,
    alpha: float,
    global_cache: dict[frozenset, DesignPoint],
    *,
    titan_controller_dir: Path,
    benchmark_json_path: str,
    host_destination_path: Path,
    sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    poll_interval: float = 30.0,
    job_name: str = "asi_batch",
) -> list[tuple[DesignPoint | None, bool, int]]:
    """Evaluates a whole batch of (params, output_path, modified_params)
    entities as one Titan job. Cache hits never touch Titan; the rest are
    submitted together, polled via --list job, collected, and parsed back
    with finalize_point() -- identical math to the local evaluate_point()
    path. Returns (point, ran, invocations) per entity, input order."""
    results: list[tuple[DesignPoint | None, bool, int] | None] = [None] * len(entities)
    pending = []
    for idx, (params, output_path, modified_params) in enumerate(entities):
        key = params_key(params)
        point = cached_point(key, modified_params, global_cache)
        if point is not None:
            results[idx] = (point, False, 0)
        else:
            pending.append((idx, params, output_path, modified_params, key))

    if not pending:
        return results

    host_destination_path = Path(host_destination_path)
    experiment = entities_to_titan_experiment(
        [params for _, params, _, _, _ in pending], reference_config, benchmark_json_path,
        str(host_destination_path), job_name=job_name, sniper_mount=sniper_mount,
        benchmarks_mount=benchmarks_mount,
    )
    host_destination_path.mkdir(parents=True, exist_ok=True)
    experiment_path = host_destination_path / "batch_experiment.json"
    experiment_path.write_text(json.dumps(experiment, indent=2))

    submit_out = _run_titan(titan_controller_dir, ["--submit", "job", "--path", str(experiment_path)])
    job_ids = set(_JOBID_RE.findall(submit_out))

    if job_ids:
        print(f"  Submitted {len(job_ids)} Titan job(s) for {len(pending)} entities; "
              f"polling every {poll_interval:.0f}s...")
        while True:
            list_out = _run_titan(titan_controller_dir, ["--list", "job"])
            if not any(job_id in list_out for job_id in job_ids):
                # --collect's --path is the host_destination_path dir, unlike --submit's
                # (the experiment file itself). --collect auto-retries failed jobs and
                # reprints their new "(jobid ...)".
                collect_out = _run_titan(titan_controller_dir, ["--collect", "job", "--path", str(host_destination_path)])
                if "All experiment downloads succeeded" in collect_out:
                    break
                job_ids = set(_JOBID_RE.findall(collect_out))
                if not job_ids:
                    break
            time.sleep(poll_interval)
    else:
        # titan_controller caches Sniper results globally by config hash, independent of
        # host_destination_path -- "nothing to do" here means every entity's exact config
        # was already submitted before, but that's ambiguous about *this* experiment's own
        # local tracking: it could mean the results were already collected (by us or a
        # completely unrelated earlier experiment), in which case they're already sitting
        # in the cache and --collect has nothing to do -- or it could mean they were
        # submitted and finished on Titan but never successfully collected (e.g. a crashed
        # earlier attempt), in which case --collect is exactly what's needed to fetch them.
        # Try it either way; a cross-experiment cache hit can make --collect itself error
        # (titan_controller's retry logic misreads that case as "never submitted"), which
        # is fine to ignore since the results are already on disk in that case anyway.
        print("  All entities already submitted; making sure results are collected.")
        try:
            _run_titan(titan_controller_dir, ["--collect", "job", "--path", str(host_destination_path)])
        except RuntimeError as exc:
            print(f"  --collect errored (likely a pre-existing cross-experiment cache hit, "
                  f"proceeding to read the cache directly): {exc}")

    map_path = host_destination_path / "experiments.json"
    exp_map = json.loads(map_path.read_text())
    suite = exp_map["benchmark_suites"][0]
    cache_root = Path(suite["host_dst_path"])
    # Matched by the entity's own override string, not by list position --
    # two entities with identical params (e.g. spea2 crossover/mutation
    # producing a duplicate child) submit as separate blocks but Titan's own
    # simulator_parameters order doesn't reliably mirror the submitted order
    # once duplicates are involved, so positional zip() silently misaligned
    # results for anything after the first duplicate.
    hash_by_overrides = {
        sp["variable_sniper_parameters"]["overrides"]: sp["simulator_dir_name"]
        for sp in suite["simulator_parameters"]
    }

    for idx, params, output_path, modified_params, key in pending:
        sim_hash = hash_by_overrides[_entity_overrides(params, reference_config)]
        results_root = cache_root / sim_hash
        areas: list[float] = []
        powers: list[float] = []
        per_benchmark: dict[str, dict[str, float]] = {}
        try:
            for name in benchmarks:
                area, peak_power, t = parse_sniper_output(results_root / name / "0")
                areas.append(area)
                powers.append(peak_power)
                per_benchmark[name] = {"time": t}
        except Exception as exc:
            print(f"    FAILED (titan batch, {output_path.name}): {exc}")
            results[idx] = (None, True, len(per_benchmark))
            continue
        point = finalize_point(params, modified_params, output_path, per_benchmark, areas, powers, baseline, alpha, global_cache, key)
        results[idx] = (point, True, len(benchmarks))

    return results
