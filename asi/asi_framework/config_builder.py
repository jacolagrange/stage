"""Translates a params dict into Sniper command-line override flags."""

from typing import List, Optional

from .config import (
    DEFAULT_BRANCH_PREDICTOR_TYPE, DEFAULT_NUM_HISTORY_REGISTERS, DEFAULT_CORE_TYPE,
    DEFAULT_BRANCH_PREDICTOR_SIZE, DEFAULT_NN_BATCH_LENGTH, DEFAULT_NN_LEARNING_RATE,
    DEFAULT_ROB_WINDOW_SIZE, DEFAULT_ROB_DISPATCH_WIDTH,
    DEFAULT_ROB_OUTSTANDING_LOADS, DEFAULT_ROB_OUTSTANDING_STORES, DEFAULT_ROB_COMMIT_WIDTH,
    DEFAULT_ROB_IN_ORDER, DEFAULT_ROB_STORE_TO_LOAD_FORWARDING, DEFAULT_ROB_ADDRESS_DISAMBIGUATION,
    DEFAULT_ROB_ISSUE_CONTENTION, DEFAULT_ROB_MLP_HISTOGRAM, DEFAULT_ROB_ISSUE_MEMOPS_AT_ISSUE,
)

SNIPER_KNOB_MAP = {
    "core_type": "perf_model/core/type",
    "frequency": "perf_model/core/frequency",
    "logical_cpus": "perf_model/core/logical_cpus",
    "l1i_size": "perf_model/l1_icache/cache_size",
    "l1i_assoc": "perf_model/l1_icache/associativity",
    "l1d_size": "perf_model/l1_dcache/cache_size",
    "l1d_assoc": "perf_model/l1_dcache/associativity",
    "l2_size": "perf_model/l2_cache/cache_size",
    "l2_assoc": "perf_model/l2_cache/associativity",
    "l3_size": "perf_model/l3_cache/cache_size",
    "l3_assoc": "perf_model/l3_cache/associativity",
    "branch_predictor_type": "perf_model/branch_predictor/type",
}

BRANCH_PREDICTOR_TYPE_KNOBS: dict[str, dict[str, tuple[str, object]]] = {
    "a53": {
        "branch_predictor_size": ("perf_model/branch_predictor/size", DEFAULT_BRANCH_PREDICTOR_SIZE),
        "num_history_registers": ("perf_model/branch_predictor/num_history_registers", DEFAULT_NUM_HISTORY_REGISTERS),
    },
    "nn": {
        "nn_batch_length": ("perf_model/branch_predictor/batch_length", DEFAULT_NN_BATCH_LENGTH),
        "nn_learning_rate": ("perf_model/branch_predictor/learning_rate", DEFAULT_NN_LEARNING_RATE),
    },
    "pentium_m": {},
}

SNIPER_ROB_KNOB_MAP = {
    "rob_window_size": "perf_model/core/interval_timer/window_size",
    "rob_dispatch_width": "perf_model/core/interval_timer/dispatch_width",
    "rob_rs_entries": "perf_model/core/rob_timer/rs_entries",
    "rob_outstanding_loads": "perf_model/core/rob_timer/outstanding_loads",
    "rob_outstanding_stores": "perf_model/core/rob_timer/outstanding_stores",
    "rob_commit_width": "perf_model/core/rob_timer/commit_width",
    "rob_in_order": "perf_model/core/rob_timer/in_order",
    "rob_store_to_load_forwarding": "perf_model/core/rob_timer/store_to_load_forwarding",
    "rob_address_disambiguation": "perf_model/core/rob_timer/address_disambiguation",
    "rob_issue_contention": "perf_model/core/rob_timer/issue_contention",
    "rob_mlp_histogram": "perf_model/core/rob_timer/mlp_histogram",
    "rob_issue_memops_at_issue": "perf_model/core/rob_timer/issue_memops_at_issue",
}

SNIPER_ROB_DEFAULTS = {
    "rob_window_size": DEFAULT_ROB_WINDOW_SIZE,
    "rob_dispatch_width": DEFAULT_ROB_DISPATCH_WIDTH,
    "rob_outstanding_loads": DEFAULT_ROB_OUTSTANDING_LOADS,
    "rob_outstanding_stores": DEFAULT_ROB_OUTSTANDING_STORES,
    "rob_commit_width": DEFAULT_ROB_COMMIT_WIDTH,
    "rob_in_order": DEFAULT_ROB_IN_ORDER,
    "rob_store_to_load_forwarding": DEFAULT_ROB_STORE_TO_LOAD_FORWARDING,
    "rob_address_disambiguation": DEFAULT_ROB_ADDRESS_DISAMBIGUATION,
    "rob_issue_contention": DEFAULT_ROB_ISSUE_CONTENTION,
    "rob_mlp_histogram": DEFAULT_ROB_MLP_HISTOGRAM,
    "rob_issue_memops_at_issue": DEFAULT_ROB_ISSUE_MEMOPS_AT_ISSUE,
}


def build_runtime_config(
    reference_config: str,
    *,
    core_type: Optional[str] = DEFAULT_CORE_TYPE,
    frequency: Optional[float] = None,
    logical_cpus: Optional[int] = None,
    l1i_size: Optional[int] = None,
    l1i_assoc: Optional[int] = None,
    l1d_size: Optional[int] = None,
    l1d_assoc: Optional[int] = None,
    l2_size: Optional[int] = None,
    l2_assoc: Optional[int] = None,
    l3_size: Optional[int] = None,
    l3_assoc: Optional[int] = None,
    branch_predictor_type: Optional[str] = DEFAULT_BRANCH_PREDICTOR_TYPE,
    branch_predictor_size: Optional[int] = None,
    num_history_registers: Optional[int] = None,
    rob_outstanding_loads: Optional[int] = None,
    rob_outstanding_stores: Optional[int] = None,
    **kwargs,
) -> List[str]:
    """Turns a params dict into Sniper -c override flags; None values fall
    through to reference_config's own value."""
    params = {
        "core_type": core_type,
        "frequency": frequency,
        "logical_cpus": logical_cpus,
        "l1i_size": l1i_size,
        "l1i_assoc": l1i_assoc,
        "l1d_size": l1d_size,
        "l1d_assoc": l1d_assoc,
        "l2_size": l2_size,
        "l2_assoc": l2_assoc,
        "l3_size": l3_size,
        "l3_assoc": l3_assoc,
        "branch_predictor_type": branch_predictor_type,
        "branch_predictor_size": branch_predictor_size,
        "num_history_registers": num_history_registers,
        "rob_outstanding_loads": rob_outstanding_loads,
        "rob_outstanding_stores": rob_outstanding_stores,
    }
    params.update(kwargs)

    override_flags: List[str] = []

    for param, sniper_path in SNIPER_KNOB_MAP.items():
        value = params.get(param)
        if value is not None:
            override_flags.extend(["-c", f"{sniper_path}={value}"])

    resolved_bp_type = branch_predictor_type or DEFAULT_BRANCH_PREDICTOR_TYPE
    for param, (sniper_path, default) in BRANCH_PREDICTOR_TYPE_KNOBS.get(resolved_bp_type, {}).items():
        value = params.get(param)
        if value is None:
            value = default
        override_flags.extend(["-c", f"{sniper_path}={value}"])

    resolved_type = core_type or ""
    if resolved_type.lower() == "rob" or core_type is None:
        resolved_window_size = params.get("rob_window_size")
        if resolved_window_size is None:
            resolved_window_size = SNIPER_ROB_DEFAULTS["rob_window_size"]
        params["rob_rs_entries"] = resolved_window_size // 2

        for param, sniper_path in SNIPER_ROB_KNOB_MAP.items():
            value = params.get(param)
            if value is None:
                value = SNIPER_ROB_DEFAULTS[param]
            if isinstance(value, bool):
                value = str(value).lower()
            override_flags.extend(["-c", f"{sniper_path}={value}"])

    return override_flags