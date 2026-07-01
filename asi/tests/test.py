#!/usr/bin/env python3
"""
Test harness for asi_framework's search/sensitivity/probation logic, without
running real Sniper simulations.

We monkeypatch asi_framework.search.run() with a synthetic function that:
  - parses the generated .cfg file to recover the parameter values
  - computes a fake area/power/time from a known formula
  - injects small random noise to mimic simulation measurement noise

This lets us pick which parameters "matter" and which don't, then check
that the freezing/probation logic correctly identifies them.

NOTE: we patch asi_framework.search.run (the name bound inside search.py via
`from .runner import run`), not asi_framework.runner.run, because search.py
calls the local name `run` directly.
"""

from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asi_framework import search as search_module
from asi_framework import config as cfg

from asi_framework import plot as plt

# ---------------------------------------------------------------------------
# Ground truth landscape generator: instead of hand-picking which parameters
# "matter", randomly decide it. This lets you stress-test the freeze/probation
# logic against different landscapes just by changing the seed or knobs below.
# ---------------------------------------------------------------------------
def generate_true_effects(
    param_space: dict[str, list],
    seed: int,
    fraction_significant: float = 0.5,
    min_coeff: float = 0.005,
    max_coeff: float = 0.05,
) -> dict[str, tuple[float, float]]:
    """
    For each parameter, randomly decide whether it has a real effect on
    area/power (probability = fraction_significant). If it does, draw its
    area/power coefficients log-uniformly between min_coeff and max_coeff
    (so effect sizes span orders of magnitude, like a real design space
    would). Everything else gets (0.0, 0.0) -- a true null.
    """
    rng = random.Random(seed)
    effects: dict[str, tuple[float, float]] = {}
    for param in param_space:
        if rng.random() < fraction_significant:
            # log-uniform draw so we get a mix of strong and weak real effects
            log_min, log_max = math.log(min_coeff), math.log(max_coeff)
            area_coeff = math.exp(rng.uniform(log_min, log_max))
            power_coeff = math.exp(rng.uniform(log_min, log_max))
            effects[param] = (area_coeff, power_coeff)
        else:
            effects[param] = (0.0, 0.0)
    return effects


TRUE_EFFECTS_SEED = 42  # change this to sample a different landscape

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

TRUE_EFFECTS = generate_true_effects(TEST_PARAM_SPACE, seed=TRUE_EFFECTS_SEED)

NOISE_STD = 0.05  # simulation noise added to area/power, roughly matching threshold scale

DEFAULTS = {
    "l1i_size": cfg.DEFAULT_L1I_SIZE,
    "l1d_size": cfg.DEFAULT_L1D_SIZE,
    "l2_size": cfg.DEFAULT_L2_SIZE,
    "l3_size": cfg.DEFAULT_L3_SIZE,
    "l1i_assoc": cfg.DEFAULT_L1I_ASSOC,
    "l1d_assoc": cfg.DEFAULT_L1D_ASSOC,
    "l2_assoc": cfg.DEFAULT_L2_ASSOC,
    "l3_assoc": cfg.DEFAULT_L3_ASSOC,
    "branch_predictor_size": cfg.DEFAULT_BRANCH_PREDICTOR_SIZE,
    "rob_rs_entries": cfg.DEFAULT_ROB_RS_ENTRIES,
    "rob_outstanding_loads": cfg.DEFAULT_ROB_OUTSTANDING_LOADS,
    "rob_outstanding_stores": cfg.DEFAULT_ROB_OUTSTANDING_STORES,
}

BASE_AREA = 31.6
BASE_POWER = 17.5
BASE_TIME = 1000.0

_CFG_KV_RE = re.compile(r"^(\w[\w/]*)\s*=\s*(\S+)\s*$")


def parse_cfg_values(cfg_path: str) -> dict[str, float]:
    """Pull out the numeric values we care about from a generated .cfg file."""
    values: dict[str, float] = {}
    section = None
    for raw_line in Path(cfg_path).read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            continue
        m = _CFG_KV_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2)
        try:
            num = float(val)
        except ValueError:
            continue
        # Map (section, key) back onto our flat param names
        if section == "perf_model/l1_icache" and key == "cache_size":
            values["l1i_size"] = num
        elif section == "perf_model/l1_dcache" and key == "cache_size":
            values["l1d_size"] = num
        elif section == "perf_model/l2_cache" and key == "cache_size":
            values["l2_size"] = num
        elif section == "perf_model/l3_cache" and key == "cache_size":
            values["l3_size"] = num
        elif section == "perf_model/l1_icache" and key == "associativity":
            values["l1i_assoc"] = num
        elif section == "perf_model/l1_dcache" and key == "associativity":
            values["l1d_assoc"] = num
        elif section == "perf_model/l2_cache" and key == "associativity":
            values["l2_assoc"] = num
        elif section == "perf_model/l3_cache" and key == "associativity":
            values["l3_assoc"] = num
        elif section == "perf_model/branch_predictor" and key == "size":
            values["branch_predictor_size"] = num
        elif section == "perf_model/core/rob_timer" and key == "rs_entries":
            values["rob_rs_entries"] = num
        elif section == "perf_model/core/rob_timer" and key == "outstanding_loads":
            values["rob_outstanding_loads"] = num
        elif section == "perf_model/core/rob_timer" and key == "outstanding_stores":
            values["rob_outstanding_stores"] = num
    return values


def fake_run(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    design_knobs: dict | None = None,
) -> tuple[float, float, float]:
    """Drop-in replacement for asi_framework.runner.run() that doesn't touch Sniper at all."""
    if design_knobs is not None:
        cfg_values = dict(design_knobs)
    else:
        cfg_values = parse_cfg_values(reference_config)

    area = BASE_AREA
    power = BASE_POWER

    for param, (area_coeff, power_coeff) in TRUE_EFFECTS.items():
        if param in cfg_values:
            delta = cfg_values[param] - DEFAULTS[param]
            area += area_coeff * delta
            power += power_coeff * delta

    # noise to mimic simulation measurement variance
    area += random.gauss(0, NOISE_STD)
    power += random.gauss(0, NOISE_STD)
    time = BASE_TIME + random.gauss(0, NOISE_STD * 10)

    return max(area, 0.1), max(power, 0.1), max(time, 1.0)


def main() -> None:
    random.seed(0)  # reproducible test run

    print(f"=== Sampled ground-truth landscape (seed={TRUE_EFFECTS_SEED}) ===")
    for param, (area_c, power_c) in TRUE_EFFECTS.items():
        tag = "REAL EFFECT" if (area_c, power_c) != (0.0, 0.0) else "null"
        print(f"  {param:<25} area_coeff={area_c:.5f}  power_coeff={power_c:.5f}  [{tag}]")
    print()

    # Monkeypatch: replace the real run() with our synthetic one. We patch it
    # on the search module specifically, since that's where `run(...)` is
    # called from (search.py imports it via `from .runner import run`).
    search_module.run = fake_run

    # Also override PARAM_SPACE so the test exercises every parameter, not
    # just whichever ones happen to be active in asi_framework/config.py.
    search_module.PARAM_SPACE = TEST_PARAM_SPACE

    outputdir = Path("/tmp/asi_test_output")
    outputdir.mkdir(parents=True, exist_ok=True)

    front = search_module.explore_pareto_front_with_sensitivity(
        reference_config="dummy_reference.cfg",
        sniper=Path("/fake/run-sniper"),  # never actually used by fake_run
        outputdir=outputdir,
        cmd=["--", "/bin/echo", "hi"],
        alpha=cfg.DEFAULT_ALPHA,
        max_iterations=8,
    )

    print("\n=== Final Pareto Front (synthetic) ===")
    print(f"{'Params':<55} {'ASI':>8} {'Speedup':>10} {'Area':>10} {'PeakPow':>10}")
    print("-" * 100)
    for p in sorted(front, key=lambda x: x.speedup):
        print(f"{str(p.params):<55} {p.asi:>8.4f} {p.speedup:>10.4f} "
              f"{p.area:>10.4f} {p.peak_power:>10.4f}")

    print("\nExpected: l3_size and rob_rs_entries stay active (real effect, if present in PARAM_SPACE);")
    print("everything else should get frozen within the first couple iterations.")
    plt.plot_pareto_front_on_asi(front, title="Synthetic Test Pareto Front")


if __name__ == "__main__":
    main()