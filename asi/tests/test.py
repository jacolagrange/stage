#!/usr/bin/env python3
"""
Test harness for asi_framework's search/sensitivity/probation logic, without
running real Sniper simulations.

We monkeypatch asi_framework.greedy.run() with a synthetic function that:
  - parses the generated .cfg file to recover the parameter values
  - computes a fake area/power/time from a known formula
  - injects small random noise to mimic simulation measurement noise

This lets us pick which parameters "matter" and which don't, then check
that the freezing/probation logic correctly identifies them.

NOTE: we patch asi_framework.greedy.run (the name bound inside greedy.py via
`from .runner import run`), not asi_framework.runner.run, because greedy.py
calls the local name `run` directly.
"""

from __future__ import annotations

import math
import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asi_framework import greedy
from asi_framework import config as cfg
from asi_framework import plot as plt
from asi_framework.greedy import print_pareto_table

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

# As in asi_framework/param_space.json, the first value in each list is the
# parameter's baseline/default value; DEFAULTS below is derived from it
# rather than hand-duplicated, so the two can't drift out of sync.
TEST_PARAM_SPACE = {
    "l1i_size":               [cfg.DEFAULT_L1I_SIZE, 16, 32, 64],
    "l1d_size":               [cfg.DEFAULT_L1D_SIZE, 16, 32, 64],
    "l2_size":                [cfg.DEFAULT_L2_SIZE, 128, 256, 512],
    "l3_size":                [cfg.DEFAULT_L3_SIZE, 1024, 2048, 4096, 8192],
    "l1i_assoc":              [cfg.DEFAULT_L1I_ASSOC, 4, 8],
    "l1d_assoc":              [cfg.DEFAULT_L1D_ASSOC, 4, 8],
    "l2_assoc":               [cfg.DEFAULT_L2_ASSOC, 4, 8],
    "l3_assoc":               [cfg.DEFAULT_L3_ASSOC, 8, 16],
    "branch_predictor_size":  [cfg.DEFAULT_BRANCH_PREDICTOR_SIZE, 512, 1024, 2048],
    "rob_rs_entries":         [cfg.DEFAULT_ROB_RS_ENTRIES, 16, 36, 64, 96],
    "rob_outstanding_loads":  [cfg.DEFAULT_ROB_OUTSTANDING_LOADS, 16, 32, 48, 64],
    "rob_outstanding_stores": [cfg.DEFAULT_ROB_OUTSTANDING_STORES, 16, 32, 48, 64],
}
# Each list above may repeat its own default (e.g. rob_rs_entries already
# includes 36); de-dup while preserving order so it still matches PARAM_SPACE's
# "default first, then alternates" convention.
TEST_PARAM_SPACE = {
    param: list(dict.fromkeys(values)) for param, values in TEST_PARAM_SPACE.items()
}

TRUE_EFFECTS = generate_true_effects(TEST_PARAM_SPACE, seed=TRUE_EFFECTS_SEED)

NOISE_STD = 0.05  # simulation noise added to area/power, roughly matching threshold scale

DEFAULTS = {param: values[0] for param, values in TEST_PARAM_SPACE.items()}

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
    # on the greedy module specifically, since that's where `run(...)` is
    # called from (greedy.py imports it via `from .runner import run`).
    greedy.run = fake_run

    # Also override PARAM_SPACE (and matching DEFAULTS) so the test exercises
    # every parameter, not just whichever ones happen to be active in
    # asi_framework/config.py's param_space.json.
    greedy.PARAM_SPACE = TEST_PARAM_SPACE
    greedy.DEFAULTS = DEFAULTS

    outputdir = Path("/tmp/asi_test_output")
    outputdir.mkdir(parents=True, exist_ok=True)

    front = greedy.explore_pareto_front_with_sensitivity(
        reference_config="dummy_reference.cfg",
        sniper=Path("/fake/run-sniper"),  # never actually used by fake_run
        outputdir=outputdir,
        benchmarks={"echo": ["--", "/bin/echo", "hi"]},
        alpha=cfg.DEFAULT_ALPHA,
        max_iterations=8,
    )

    print("\n=== Final Pareto Front (synthetic) ===")
    print_pareto_table(front)

    print("\nExpected: parameters with real effects stay active;")
    print("insensitive parameters should freeze within the first couple of iterations.")
    plt.plot_pareto_front_on_asi(front, title="Synthetic Test Pareto Front")


if __name__ == "__main__":
    main()