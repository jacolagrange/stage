"""
Builds a titan_controller experiment JSON for a whole batch of design points
(e.g. one SPEA2 generation) in a single file, instead of evaluating them one
Sniper subprocess call at a time the way runner.run() does locally.

Each entity becomes its own `sniper_parameters.parameters[]` block using
`"mix": "product"` with every value list holding exactly one already-resolved
value -- the product of N one-element lists is just that one combination, so
one block = one exact entity, with no need for a new mix mode on the
titan_controller side. What actually varies per entity is a single
`{overrides}` placeholder whose value is the *entire* Sniper override-flag
string for that entity, built by calling config_builder.build_runtime_config()
directly -- the same function runner.run() already uses for local runs -- so
this can never drift from local behavior (branch-predictor-type-specific
knobs, ROB knobs and their conditional inclusion all stay correct without
reimplementing that logic here).
"""

from typing import Any

from .config_builder import build_runtime_config


def entities_to_titan_experiment(
    entities: list[dict[str, Any]],
    reference_config: str,
    benchmark_json_path: str,
    host_destination_path: str,
    *,
    job_name: str = "asi_batch",
    sniper_mount: str = "/mnt/perflab/exascience/src/jaco_sniper",
    benchmarks_mount: str = "/mnt/perflab/exascience/src/jaco_benchmarks",
    icount_stop: int = 2_000_000,
    core_per_experiment: int = 1,
    mem_per_core: int = 2048,
    vm_name: str = "sniper2404",
) -> dict[str, Any]:
    """Build a titan_controller experiment dict for one batch of design
    points (e.g. one SPEA2 generation's population, or greedy's per-round
    search set), ready to json.dump() and hand to `--submit`.

    entities: list of (possibly sparse) params dicts, same shape as what
    evaluate_point()/runner.run() already accept locally.
    reference_config: passed through to build_runtime_config() exactly like
    a local run -- must resolve inside the mounted Sniper's own config/
    directory (e.g. "nehalem.cfg").
    benchmark_json_path: path (relative to where the experiment JSON lives)
    to the benchmark suite JSON to run every entity against -- the same
    benchmarks for every entity in the batch.
    host_destination_path: where results land locally after --collect; give
    each batch its own path (e.g. per generation) -- see titan_controller's
    RUNBOOK.md, reusing one while a previous submission is still tracked as
    SUBMITTED causes --submit to silently no-op.
    """
    parameters = []
    for entity in entities:
        knobs = dict(entity)
        cores = knobs.pop("cores", 1)
        flags = build_runtime_config(reference_config, **knobs)
        overrides = " ".join(["-n", str(cores)] + flags)
        parameters.append({
            "mix": "product",
            "include_first": "true",
            "values": {"overrides": [overrides]},
        })

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
            "arguments": ["-c", str(reference_config), "-s", f"stop-by-icount:{icount_stop}", "{overrides}"],
            "parameters": parameters,
        },
        "host_destination_path": host_destination_path,
    }
