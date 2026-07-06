"""
Config Builder Module for the Architectural Search Interface (ASI).
Translates design exploration parameter sets directly into Sniper 
command-line configuration override flags.
"""

from typing import List, Optional

from .config import (
    DEFAULT_BRANCH_PREDICTOR_TYPE, DEFAULT_NUM_HISTORY_REGISTERS, DEFAULT_CORE_TYPE,
    DEFAULT_BRANCH_PREDICTOR_SIZE, DEFAULT_NN_BATCH_LENGTH, DEFAULT_NN_LEARNING_RATE,
    DEFAULT_ROB_WINDOW_SIZE, DEFAULT_ROB_DISPATCH_WIDTH, DEFAULT_ROB_RS_ENTRIES,
    DEFAULT_ROB_OUTSTANDING_LOADS, DEFAULT_ROB_OUTSTANDING_STORES, DEFAULT_ROB_COMMIT_WIDTH,
    DEFAULT_ROB_IN_ORDER, DEFAULT_ROB_STORE_TO_LOAD_FORWARDING, DEFAULT_ROB_ADDRESS_DISAMBIGUATION,
    DEFAULT_ROB_ISSUE_CONTENTION, DEFAULT_ROB_MLP_HISTOGRAM, DEFAULT_ROB_ISSUE_MEMOPS_AT_ISSUE,
)

# Dict maps friendly design parameters used by the search algorithm
# to the exact section/key paths expected within Sniper's configuration parser hierarchy.
# branch_predictor_size/num_history_registers/nn_batch_length/nn_learning_rate
# are NOT here -- they're type-specific (only meaningful for one
# branch_predictor_type) and are handled by BRANCH_PREDICTOR_TYPE_KNOBS below,
# since (like the ROB knobs) Sniper reads them unconditionally once that type
# is selected and they can't just be skipped when unset.
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

# Per branch_predictor_type, the knobs Sniper's predictor constructor reads
# unconditionally (path, default-if-unset). pentium_m has none of its own --
# it's fully determined by its type. Mirrors config.py's BRANCH_PREDICTOR_PARAMS
# (which the search strategies use to decide which knobs to vary), but also
# carries the Sniper path and a required fallback default, since e.g. a53's
# num_history_registers/size must always be supplied once type=="a53" -- the
# ASI reference configs (e.g. nehalem.cfg) are written for pentium_m and don't
# define them.
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

# Sub-section mappings specifically dedicated to Out-of-Order Reorder Buffer (ROB) tuning.
# window_size/dispatch_width are the ROB's actual size and dispatch width, but
# live under perf_model/core/interval_timer (shared with the interval core
# model); the rest are perf_model/core/rob_timer-only.
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

# Sniper's RobTimer constructor reads every perf_model/core/rob_timer/* key
# (and RobPerformanceModel reads interval_timer/window_size+dispatch_width)
# unconditionally -- if any is missing from the merged config, Sniper aborts
# with a KeyNotFound error. The ASI reference configs (e.g. nehalem.cfg) are
# built around the interval core model and define no [rob_timer] section at
# all, so once core_type resolves to "rob" every one of these must be
# supplied; this is why (unlike the rest of SNIPER_KNOB_MAP) they can't just
# be skipped when the search doesn't specify a value -- they fall back to
# these defaults (matching config/rob.cfg) instead.
SNIPER_ROB_DEFAULTS = {
    "rob_window_size": DEFAULT_ROB_WINDOW_SIZE,
    "rob_dispatch_width": DEFAULT_ROB_DISPATCH_WIDTH,
    "rob_rs_entries": DEFAULT_ROB_RS_ENTRIES,
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
    cores: Optional[int] = None,
    core_model: Optional[str] = None,
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
    rob_rs_entries: Optional[int] = None,
    rob_outstanding_loads: Optional[int] = None,
    rob_outstanding_stores: Optional[int] = None,
    **kwargs,
) -> List[str]:
    """
    Transforms dictionary keyword configurations passed during the exploration 
    search loops into a flat list of terminal flags.
    Options that are omitted or passed as None are skipped entirely, allowing 
    the base 'reference_config' defaults to cleanly fall through.
    """
    # Create explicit local arguments dictionary mapping parameter names to values
    local_arguments = {
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
        "rob_rs_entries": rob_rs_entries,
        "rob_outstanding_loads": rob_outstanding_loads,
        "rob_outstanding_stores": rob_outstanding_stores,
    }
    # Include any extra catch-all items from kwargs
    local_arguments.update(kwargs)
    
    override_flags: List[str] = []

    # 1. Process Standard Core/Cache/Branch Predictor Knobs
    for algorithm_param, sniper_path in SNIPER_KNOB_MAP.items():
        value = local_arguments.get(algorithm_param)
        if value is not None:
            override_flags.extend(["-c", f"{sniper_path}={value}"])

    # 1b. Process the selected branch predictor type's own knobs. Like the ROB
    # block below, these can't be skipped when unset: Sniper reads them
    # unconditionally once that type is selected, and the ASI reference
    # configs (written for pentium_m) don't define a53's/nn's own keys.
    resolved_bp_type = branch_predictor_type or DEFAULT_BRANCH_PREDICTOR_TYPE
    for algorithm_param, (sniper_path, default) in BRANCH_PREDICTOR_TYPE_KNOBS.get(resolved_bp_type, {}).items():
        value = local_arguments.get(algorithm_param)
        if value is None:
            value = default
        override_flags.extend(["-c", f"{sniper_path}={value}"])

    # 2. Process Out-of-Order ROB Timing Parameters. These can't be skipped
    # when unset (unlike SNIPER_KNOB_MAP above): Sniper's RobTimer reads every
    # one of these keys unconditionally, and reference configs built around
    # the interval core model (e.g. nehalem.cfg) don't define a [rob_timer]
    # section at all -- so every key must be supplied, falling back to
    # SNIPER_ROB_DEFAULTS when the search didn't specify a value.
    resolved_type = core_type or ""
    if resolved_type.lower() == "rob" or core_type is None:
        for algorithm_param, sniper_path in SNIPER_ROB_KNOB_MAP.items():
            value = local_arguments.get(algorithm_param)
            if value is None:
                value = SNIPER_ROB_DEFAULTS[algorithm_param]
            # Convert boolean values to config strings if present
            if isinstance(value, bool):
                value = str(value).lower()
            override_flags.extend(["-c", f"{sniper_path}={value}"])

    return override_flags