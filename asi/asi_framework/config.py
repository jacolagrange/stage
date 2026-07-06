import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RUN_SNIPER = ROOT / "snipersim" / "run-sniper"
DEFAULT_OUTPUT_DIR = ROOT / "asi" / "asi-output"
DEFAULT_CORE_MODEL = "nehalem"
DEFAULT_CORE_TYPE = "rob"
DEFAULT_FREQUENCY = 2.66
DEFAULT_LOGICAL_CPUS = 1
DEFAULT_L1I_SIZE = 32
DEFAULT_L1D_SIZE = 32
DEFAULT_L2_SIZE = 256
DEFAULT_L3_SIZE = 8192
DEFAULT_L1I_ASSOC = 4
DEFAULT_L1D_ASSOC = 8
DEFAULT_L2_ASSOC = 8
DEFAULT_L3_ASSOC = 16
DEFAULT_BRANCH_PREDICTOR_TYPE = "a53"
DEFAULT_BRANCH_MISPREDICT_PENALTY = 10
DEFAULT_BRANCH_PREDICTOR_SIZE = 1024
DEFAULT_NUM_HISTORY_REGISTERS = 3
DEFAULT_NN_BATCH_LENGTH = 32
DEFAULT_NN_LEARNING_RATE = 0.001

# Out-of-order / ROB timer knobs (only meaningful when core_type == "rob").
# window_size/dispatch_width live under perf_model/core/interval_timer
# (shared with the interval core model) but are the ROB's actual size and
# dispatch width; the rest are perf_model/core/rob_timer-only. Sniper's
# RobTimer reads every one of these unconditionally, and the reference
# configs used here (e.g. nehalem.cfg) don't define a [rob_timer] section at
# all, so all of them must always be supplied once core_type is "rob" --
# see config_builder.py's SNIPER_ROB_DEFAULTS.
DEFAULT_ROB_WINDOW_SIZE = 128
DEFAULT_ROB_DISPATCH_WIDTH = 4
DEFAULT_ROB_RS_ENTRIES = 36
DEFAULT_ROB_OUTSTANDING_LOADS = 48
DEFAULT_ROB_OUTSTANDING_STORES = 32
DEFAULT_ROB_COMMIT_WIDTH = 128
DEFAULT_ROB_IN_ORDER = "false"
DEFAULT_ROB_STORE_TO_LOAD_FORWARDING = "true"
DEFAULT_ROB_ADDRESS_DISAMBIGUATION = "true"
DEFAULT_ROB_ISSUE_CONTENTION = "true"
DEFAULT_ROB_MLP_HISTOGRAM = "false"
DEFAULT_ROB_ISSUE_MEMOPS_AT_ISSUE = "true"

# Alpha controls the area/power trade-off in the ASI formula
DEFAULT_ALPHA = 0.5

# Search space: for each parameter, the candidate values to try. Following
# the reference ASI_exploration project's sweep-JSON convention, the first
# value in each list is the parameter's current/baseline value; the search
# skips re-testing a parameter at that value.
PARAM_SPACE_FILE = Path(__file__).resolve().parent / "param_space.json"
PARAM_SPACE: dict[str, list[Any]] = json.loads(PARAM_SPACE_FILE.read_text())
DEFAULTS: dict[str, Any] = {param: values[0] for param, values in PARAM_SPACE.items()}

# Some PARAM_SPACE entries only make sense for a subset of branch_predictor_type
# values -- e.g. nn_learning_rate is read only by Sniper's "nn" predictor, so
# varying it while branch_predictor_type == "pentium_m" would produce a config
# that's functionally identical to one without that override, wasting an
# evaluation. BRANCH_PREDICTOR_PARAMS records each type's own knobs; the search
# strategies use it (via active_params()) to only vary/include params that are
# actually relevant to an entity's currently selected predictor type.
BRANCH_PREDICTOR_PARAMS: dict[str, tuple[str, ...]] = {
    "a53": ("branch_predictor_size", "num_history_registers"),
    "nn": ("nn_batch_length", "nn_learning_rate"),
    "pentium_m": (),
}
CONDITIONAL_PARAMS: set[str] = {p for params in BRANCH_PREDICTOR_PARAMS.values() for p in params}


def active_params(param_space: dict[str, list], bp_type: str) -> set[str]:
    """Keys of `param_space` that are meaningful for a given branch_predictor_type:
    every always-relevant parameter, plus that type's own predictor-specific
    knobs (pentium_m has none of its own -- it's fully determined once the
    type is chosen). Takes param_space explicitly (rather than closing over
    the module-level PARAM_SPACE) so callers that monkeypatch their own local
    PARAM_SPACE/DEFAULTS (e.g. tests/test.py) still get consistent results."""
    return {p for p in param_space if p not in CONDITIONAL_PARAMS} | set(BRANCH_PREDICTOR_PARAMS.get(bp_type, ()))