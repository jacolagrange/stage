"""Config-space operations shared by the search strategies: generating a
random fully-specified configuration, mutating one into a neighbor,
enumerating every configuration, and diffing a configuration against
DEFAULTS."""
import itertools
import random
from typing import Any, Iterator

from .config import (
    DEFAULTS, DEFAULT_BRANCH_PREDICTOR_TYPE, BRANCH_PREDICTOR_PARAMS,
    CONDITIONAL_PARAMS, active_params,
)


def random_entity(rng: random.Random, param_space: dict[str, list]) -> dict[str, Any]:
    """A fully-specified configuration: one value per param_space parameter
    relevant to the randomly chosen branch predictor type. Shared by
    mesmo.py, spea2.py and screening.py's initial-design/random-sampling
    code, all of which always call this with the full PARAM_SPACE."""
    entity = {
        param: rng.choice(values)
        for param, values in param_space.items()
        if param not in CONDITIONAL_PARAMS
    }
    for param in BRANCH_PREDICTOR_PARAMS.get(entity["branch_predictor_type"], ()):
        entity[param] = rng.choice(param_space[param])
    return entity


def random_variant(entity: dict[str, Any], rng: random.Random, param_space: dict[str, list]) -> dict[str, Any]:
    """One active parameter of entity reassigned to a new candidate value --
    spea2's mutation operator and mesmo's neighbor-generation step for local
    candidate-pool sampling are the same operation under different names.
    Takes param_space explicitly (like random_entity) rather than reading a
    module-level PARAM_SPACE, since callers may have their own (possibly
    pre-evaluation-pruned) PARAM_SPACE binding -- see cli.py's screening step."""
    child = dict(entity)
    bp_type = entity.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    param = rng.choice(sorted(active_params(param_space, bp_type)))
    child[param] = rng.choice(param_space[param])
    if param == "branch_predictor_type":
        for stale in CONDITIONAL_PARAMS - set(BRANCH_PREDICTOR_PARAMS.get(child[param], ())):
            child.pop(stale, None)
        for new_param in BRANCH_PREDICTOR_PARAMS.get(child[param], ()):
            child[new_param] = rng.choice(param_space[new_param])
    return child


def modified_params(params: dict[str, Any]) -> set[str]:
    return {p for p, v in params.items() if v != DEFAULTS[p]}


def full_factorial_configs(param_space: dict[str, list]) -> Iterator[dict[str, Any]]:
    """Every fully-specified configuration in param_space, exactly once --
    the full cross-product of every always-active parameter, times (for each
    branch_predictor_type) the cross-product of just that type's own
    conditional knobs. Order is deterministic (itertools.product over
    param_space's own key/value order), so a resumed titan full-factorial
    sweep can regenerate the same sequence and skip whatever's already in
    its completed_keys instead of persisting an enumeration index.

    rob_commit_width is excluded from the cross-product and forced equal to
    rob_dispatch_width instead: config_builder.py's build_runtime_config()
    always overwrites rob_commit_width with rob_dispatch_width's resolved
    value before generating the runtime config ("COUPLING", see its own
    comment), so enumerating rob_commit_width independently would yield up to
    4 raw entries per real configuration, all resolving to the identical
    simulated hardware."""
    base_params = [p for p in param_space if p not in CONDITIONAL_PARAMS and p != "rob_commit_width"]
    base_values = [param_space[p] for p in base_params]
    for combo in itertools.product(*base_values):
        base = dict(zip(base_params, combo))
        if "rob_commit_width" in param_space:
            base["rob_commit_width"] = base["rob_dispatch_width"]
        bp_type = base.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
        cond_params = [p for p in BRANCH_PREDICTOR_PARAMS.get(bp_type, ()) if p in param_space]
        if not cond_params:
            yield dict(base)
            continue
        cond_values = [param_space[p] for p in cond_params]
        for cond_combo in itertools.product(*cond_values):
            yield {**base, **dict(zip(cond_params, cond_combo))}


def full_factorial_size(param_space: dict[str, list]) -> int:
    """Count of full_factorial_configs(param_space)'s output, without
    materializing it (still an O(n) generator pass, just no per-config
    dict-building/storage cost)."""
    return sum(1 for _ in full_factorial_configs(param_space))
