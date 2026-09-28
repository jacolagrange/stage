"""Terminal-output formatting for design points and Pareto fronts."""
from typing import Any

from .models import DesignPoint
from .config import DEFAULTS, DEFAULT_BRANCH_PREDICTOR_TYPE

_SHORT = {
    "l1i_size": "l1i", "l1d_size": "l1d", "l2_size": "l2", "l3_size": "l3",
    "l1i_assoc": "l1ia", "l1d_assoc": "l1da", "l2_assoc": "l2a", "l3_assoc": "l3a",
    "branch_predictor_type": "bpt", "branch_predictor_size": "bp",
    "num_history_registers": "nhist", "nn_batch_length": "nnbl", "nn_learning_rate": "nnlr",
    "rob_window_size": "robw", "rob_dispatch_width": "robd",
    "rob_commit_width": "robc",
    "rob_outstanding_loads": "ld_out", "rob_outstanding_stores": "st_out",
}


def fmt_params(params: dict[str, Any]) -> str:
    """Renders a point's (possibly sparse) params dict for terminal output.
    branch_predictor_type is always shown; every other key only when it
    deviates from its default."""
    if not params or all(params[p] == DEFAULTS[p] for p in params):
        return "baseline"
    bp_type = params.get("branch_predictor_type", DEFAULTS.get("branch_predictor_type", DEFAULT_BRANCH_PREDICTOR_TYPE))
    shown = {**params, "branch_predictor_type": bp_type}
    return " ".join(f"{_SHORT.get(k, k)}={v}" for k, v in sorted(shown.items()))


def sustainability_label(asi: float, speedup: float) -> str:
    tn = 1.0 / speedup if speedup > 0 else float("inf")
    upper = max(1.0, tn)
    lower = min(1.0, tn)
    if asi > upper:
        return "Strongly Sust."
    if asi < lower:
        return "Unsustainable"
    if abs(asi - 1.0) < 1e-9 and abs(speedup - 1.0) < 1e-9:
        return "Reference"
    if asi < 1.0:
        return "Weakly S-FW"
    return "Weakly S-FT"


def print_evaluated_point(params: dict[str, Any], point: DesignPoint, prefix: str = "") -> None:
    label = sustainability_label(point.asi, point.speedup)
    breakdown = ""
    if len(point.per_benchmark) > 1:
        breakdown = "  [" + ", ".join(
            f"{name}={data['speedup']:.2f}x" for name, data in point.per_benchmark.items()
        ) + "]"
    print(
        f"  {prefix}{fmt_params(params):<32}"
        f"  ASI={point.asi:7.4f}  S={point.speedup:6.4f}"
        f"  A={point.area:7.2f}  P={point.peak_power:6.2f}  [{label}]{breakdown}"
    )


def print_pareto_table(pareto_set: list[DesignPoint]) -> None:
    col = 34
    header = f"  {'Params':<{col}} {'ASI':>8} {'Speedup':>8} {'Area':>8} {'PeakPow':>8}  Region"
    sep = "  " + "─" * (len(header) - 2)
    print(header)
    print(sep)
    for p in sorted(pareto_set, key=lambda x: x.speedup, reverse=True):
        label = sustainability_label(p.asi, p.speedup)
        print(
            f"  {fmt_params(p.params):<{col}} {p.asi:8.4f} {p.speedup:8.4f}"
            f" {p.area:8.2f} {p.peak_power:8.2f}  {label}"
        )
