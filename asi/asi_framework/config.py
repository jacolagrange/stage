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
DEFAULT_BRANCH_PREDICTOR_TYPE = "pentium_m"
DEFAULT_BRANCH_MISPREDICT_PENALTY = 8
DEFAULT_BRANCH_PREDICTOR_SIZE = 1024

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

# Search space: for each parameter, the candidate values to try.
PARAM_SPACE: dict[str, list[Any]] = {
    "l1i_size":                 [16, 32, 64],
    "l1d_size":                 [16, 32, 64],
    "l2_size":                  [128, 256, 512],
    "l3_size":                  [1024, 2048, 4096, 8192],
    "l1i_assoc":                [4, 8],
    "l1d_assoc":                [4, 8],
    "l2_assoc":                 [4, 8],
    "l3_assoc":                 [8, 16],
    "branch_predictor_size":    [512, 1024, 2048],
    "rob_rs_entries":           [16, 36, 64, 96],
    #"rob_outstanding_loads":    [16, 32, 48, 64],
    #"rob_outstanding_stores":   [16, 32, 48, 64],

}

TEST_PARAM_SPACE = {
    "l1i_size":               [16, 32, 64],
    "l1d_size":               [16, 32, 64],
    "l2_size":                [128, 256, 512],
    "l3_size":                [1024, 2048, 4096, 8192],
    "l1i_assoc":              [4, 8],
    "l1d_assoc":              [4, 8],
    "l2_assoc":               [4, 8],
    "l3_assoc":               [8, 16],
    "branch_predictor_size":  [512, 1024, 2048],
    "rob_rs_entries":         [16, 36, 64, 96],
    "rob_outstanding_loads":  [16, 32, 48, 64],
    "rob_outstanding_stores": [16, 32, 48, 64],
}