"""
Config Builder Module for the Architectural Search Interface (ASI).
Translates design exploration parameter sets directly into Sniper 
command-line configuration override flags.
"""

from typing import List, Optional

# Dict maps friendly design parameters used by the search algorithm
# to the exact section/key paths expected within Sniper's configuration parser hierarchy.
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
    "branch_predictor_size": "perf_model/branch_predictor/size",
}

# Sub-section mappings specifically dedicated to Out-of-Order Reorder Buffer (ROB) tuning
SNIPER_ROB_KNOB_MAP = {
    "rob_rs_entries": "perf_model/core/rob_timer/rs_entries",
    "rob_outstanding_loads": "perf_model/core/rob_timer/outstanding_loads",
    "rob_outstanding_stores": "perf_model/core/rob_timer/outstanding_stores",
    "rob_commit_width": "perf_model/core/rob_timer/commit_width",
    "rob_in_order": "perf_model/core/rob_timer/in_order",
    "rob_store_to_load_forwarding": "perf_model/core/rob_timer/store_to_load_forwarding",
    "rob_address_disambiguation": "perf_model/core/rob_timer/address_disambiguation",
}


def build_runtime_config(
    reference_config: str,
    *,
    cores: Optional[int] = None,
    core_model: Optional[str] = None,
    core_type: Optional[str] = None,
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
    branch_predictor_size: Optional[int] = None,
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
        "branch_predictor_size": branch_predictor_size,
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

    # 2. Process Out-of-Order ROB Timing Parameters
    resolved_type = core_type or ""
    if resolved_type.lower() == "rob" or core_type is None:
        for algorithm_param, sniper_path in SNIPER_ROB_KNOB_MAP.items():
            value = local_arguments.get(algorithm_param)
            if value is not None:
                # Convert boolean values to config strings if present
                if isinstance(value, bool):
                    value = str(value).lower()
                override_flags.extend(["-c", f"{sniper_path}={value}"])

    return override_flags