"""Config-space operations shared by the search strategies: generating a
random fully-specified configuration, mutating one into a neighbor, and
diffing a configuration against DEFAULTS."""
import random
from typing import Any

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
