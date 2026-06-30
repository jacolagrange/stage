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

import random
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from asi_framework import search as search_module
from asi_framework import config as cfg


# ---------------------------------------------------------------------------
# Ground truth: which parameters we pretend actually affect area/power, and
# by how much. Anything not listed here has ~no effect (just noise).
# ---------------------------------------------------------------------------
TRUE_EFFECTS = {
    "l1i_size":               (0.0, 0.0),     # no real effect -> should freeze
    "l1d_size":               (0.0, 0.0),     # no real effect -> should freeze
    "l2_size":                (0.0, 0.0),     # no real effect -> should freeze
    "l3_size":                (0.0008, 0.0006),  # genuinely matters
    "l1i_assoc":              (0.0, 0.0),
    "l1d_assoc":              (0.0, 0.0),
    "l2_assoc":               (0.0, 0.0),
    "l3_assoc":               (0.003, 0.002), # genuinely matters
    "branch_predictor_size":  (0.0, 0.0),
    "rob_rs_entries":         (0.01, 0.015),  # genuinely matters
    "rob_outstanding_loads":  (0.0, 0.0),
    "rob_outstanding_stores": (0.0, 0.0),
}

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


def fake_run(config: str, sniper: Path, outputdir: Path, cmd: list[str]) -> tuple[float, float, float]:
    """Drop-in replacement for asi_framework.runner.run() that doesn't touch Sniper at all."""
    cfg_values = parse_cfg_values(config)

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

    # Monkeypatch: replace the real run() with our synthetic one. We patch it
    # on the search module specifically, since that's where `run(...)` is
    # called from (search.py imports it via `from .runner import run`).
    search_module.run = fake_run
    search_module.PARAM_SPACE = cfg.TEST_PARAM_SPACE

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


if __name__ == "__main__":
    main()