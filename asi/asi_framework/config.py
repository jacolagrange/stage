import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent.parent
RUN_SNIPER = ROOT / "snipersim" / "run-sniper"
DEFAULT_OUTPUT_DIR = ROOT / "asi" / "asi-output"
DEFAULT_CORE_TYPE = "rob"
DEFAULT_L1I_SIZE = 32
DEFAULT_L1D_SIZE = 32
DEFAULT_L2_SIZE = 256
DEFAULT_L3_SIZE = 8192
DEFAULT_L1I_ASSOC = 4
DEFAULT_L1D_ASSOC = 8
DEFAULT_L2_ASSOC = 8
DEFAULT_L3_ASSOC = 16
DEFAULT_BRANCH_PREDICTOR_TYPE = "a53"
DEFAULT_BRANCH_PREDICTOR_SIZE = 1024
DEFAULT_NUM_HISTORY_REGISTERS = 3
DEFAULT_NN_BATCH_LENGTH = 32
DEFAULT_NN_LEARNING_RATE = 0.001

DEFAULT_ROB_WINDOW_SIZE = 128
DEFAULT_ROB_DISPATCH_WIDTH = 4
DEFAULT_ROB_RS_ENTRIES = DEFAULT_ROB_WINDOW_SIZE // 2
DEFAULT_ROB_OUTSTANDING_LOADS = 48
DEFAULT_ROB_OUTSTANDING_STORES = 32
DEFAULT_ROB_COMMIT_WIDTH = 4
DEFAULT_ROB_IN_ORDER = "false"
DEFAULT_ROB_STORE_TO_LOAD_FORWARDING = "true"
DEFAULT_ROB_ADDRESS_DISAMBIGUATION = "true"
DEFAULT_ROB_ISSUE_CONTENTION = "true"
DEFAULT_ROB_MLP_HISTOGRAM = "false"
DEFAULT_ROB_ISSUE_MEMOPS_AT_ISSUE = "true"

DEFAULT_ALPHA = 0.5

PARAM_SPACE_FILE = Path(__file__).resolve().parent / "param_space.json"
PARAM_SPACE: dict[str, list[Any]] = json.loads(PARAM_SPACE_FILE.read_text())
DEFAULTS: dict[str, Any] = {param: values[0] for param, values in PARAM_SPACE.items()}

BRANCH_PREDICTOR_PARAMS: dict[str, tuple[str, ...]] = {
    "a53": ("branch_predictor_size", "num_history_registers"),
    "nn": ("nn_batch_length", "nn_learning_rate"),
    "pentium_m": (),
    "tage": (),
}
CONDITIONAL_PARAMS: set[str] = {p for params in BRANCH_PREDICTOR_PARAMS.values() for p in params}


def active_params(param_space: dict[str, list], bp_type: str) -> set[str]:
    """Keys of param_space meaningful for the given branch_predictor_type."""
    return {p for p in param_space if p not in CONDITIONAL_PARAMS} | set(BRANCH_PREDICTOR_PARAMS.get(bp_type, ()))