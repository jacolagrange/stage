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

# Out-of-order / ROB timer knobs (only meaningful when core_type == "rob")
DEFAULT_ROB_RS_ENTRIES = 36
DEFAULT_ROB_OUTSTANDING_LOADS = 48
DEFAULT_ROB_OUTSTANDING_STORES = 32
DEFAULT_ROB_COMMIT_WIDTH = 128
DEFAULT_ROB_IN_ORDER = "false"
DEFAULT_ROB_STORE_TO_LOAD_FORWARDING = "true"
DEFAULT_ROB_ADDRESS_DISAMBIGUATION = "true"

# Alpha controls the area/power trade-off in the ASI formula
DEFAULT_ALPHA = 0.5

# Search space: for each parameter, the candidate values to try. Following
# the reference ASI_exploration project's sweep-JSON convention, the first
# value in each list is the parameter's current/baseline value; the search
# skips re-testing a parameter at that value.
PARAM_SPACE_FILE = Path(__file__).resolve().parent / "param_space.json"
PARAM_SPACE: dict[str, list[Any]] = json.loads(PARAM_SPACE_FILE.read_text())
DEFAULTS: dict[str, Any] = {param: values[0] for param, values in PARAM_SPACE.items()}