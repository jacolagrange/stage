import configparser
import hashlib
import math
import os
import random
import sys
from pathlib import Path
import subprocess

from .config import (
    ROOT,
    PARAM_SPACE,
    DEFAULT_L1I_SIZE,
    DEFAULT_L1I_ASSOC,
    DEFAULT_L1D_SIZE,
    DEFAULT_L1D_ASSOC,
    DEFAULT_L2_SIZE,
    DEFAULT_L2_ASSOC,
    DEFAULT_L3_SIZE,
    DEFAULT_L3_ASSOC,
    DEFAULT_BRANCH_PREDICTOR_SIZE,
    DEFAULT_ROB_RS_ENTRIES,
    DEFAULT_ROB_OUTSTANDING_LOADS,
    DEFAULT_ROB_OUTSTANDING_STORES,
)

# --- run_test: synthetic stand-in for run() -------------------------------
#
# run_test() never touches Sniper. Instead it reads back the knobs that
# config_builder.build_runtime_config() wrote into the cfg file and maps
# them onto a synthetic (area, peak_power, time) surface. The mapping is
# built so that "bigger" configs (larger caches/associativity/ROB) trend
# toward more area and power and less time, but with an added sinusoidal
# term so the resulting ASI-vs-speedup surface has local bumps ("loss"
# landscape) instead of being perfectly monotonic — closer to what a real
# search over microarchitecture knobs would see, and useful for exercising
# explore_pareto_front*() without paying for real Sniper runs.

TEST_BASE_AREA = 31.6          # mm^2, roughly the baseline core area
TEST_BASE_POWER = 17.5         # W, roughly the baseline peak power
TEST_BASE_TIME = 664734.0      # ns, roughly the baseline runtime
TEST_AREA_GAIN = 1.4           # how much area grows across the full param range
TEST_POWER_GAIN = 1.1          # how much power grows across the full param range
TEST_SPEEDUP_GAIN = 0.55       # how much time shrinks across the full param range
TEST_LANDSCAPE_AMPLITUDE = 0.12  # size of the non-monotonic "bumps" in time
TEST_LANDSCAPE_FREQ = 2.5       # spatial frequency of the bumps
TEST_NOISE_STD = 0.01           # relative measurement-noise jitter
TEST_MIN_TIME_FRACTION = 0.05   # floor on time, as a fraction of TEST_BASE_TIME

# Knobs read back out of the cfg file, and where to find each one.
_TEST_CFG_FIELDS: dict[str, tuple[str, str, float]] = {
    "l1i_size": ("perf_model/l1_icache", "cache_size", DEFAULT_L1I_SIZE),
    "l1i_assoc": ("perf_model/l1_icache", "associativity", DEFAULT_L1I_ASSOC),
    "l1d_size": ("perf_model/l1_dcache", "cache_size", DEFAULT_L1D_SIZE),
    "l1d_assoc": ("perf_model/l1_dcache", "associativity", DEFAULT_L1D_ASSOC),
    "l2_size": ("perf_model/l2_cache", "cache_size", DEFAULT_L2_SIZE),
    "l2_assoc": ("perf_model/l2_cache", "associativity", DEFAULT_L2_ASSOC),
    "l3_size": ("perf_model/l3_cache", "cache_size", DEFAULT_L3_SIZE),
    "l3_assoc": ("perf_model/l3_cache", "associativity", DEFAULT_L3_ASSOC),
    "branch_predictor_size": ("perf_model/branch_predictor", "size", DEFAULT_BRANCH_PREDICTOR_SIZE),
    "rob_rs_entries": ("perf_model/core/rob_timer", "rs_entries", DEFAULT_ROB_RS_ENTRIES),
    "rob_outstanding_loads": ("perf_model/core/rob_timer", "outstanding_loads", DEFAULT_ROB_OUTSTANDING_LOADS),
    "rob_outstanding_stores": ("perf_model/core/rob_timer", "outstanding_stores", DEFAULT_ROB_OUTSTANDING_STORES),
}


def _read_cfg_knobs(config: str) -> dict[str, float]:
    """Parse the microarchitectural knobs out of a cfg file written by
    build_runtime_config(), falling back to the config.py defaults for
    anything missing (e.g. the rob_timer section on non-rob cores)."""
    parser = configparser.ConfigParser()
    parser.read(config, encoding="utf-8")

    knobs: dict[str, float] = {}
    for name, (section, key, default) in _TEST_CFG_FIELDS.items():
        try:
            knobs[name] = float(parser.get(section, key))
        except (configparser.NoSectionError, configparser.NoOptionError, ValueError):
            knobs[name] = float(default)
    return knobs


def _normalize_knobs(knobs: dict[str, float]) -> list[float]:
    """Map each knob onto [0, 1] using its PARAM_SPACE range, so every
    dimension contributes on a comparable scale regardless of its units."""
    coords = []
    for name, value in knobs.items():
        values = PARAM_SPACE.get(name)
        if not values:
            coords.append(0.5)
            continue
        lo, hi = min(values), max(values)
        coords.append(0.0 if hi == lo else (value - lo) / (hi - lo))
    return coords


def _deterministic_seed(knobs: dict[str, float]) -> int:
    """A seed derived only from the knob values, so re-evaluating the same
    design point reproduces the same synthetic measurement."""
    key = ",".join(f"{name}={knobs[name]}" for name in sorted(knobs))
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:16], 16)


def run_test(config: str, sniper: Path, outputdir: Path, cmd: list[str]) -> tuple[float, float, float]:
    """Drop-in replacement for run() that skips Sniper entirely.

    Same signature and return value as run() — (area, peak_power, time) —
    but the numbers come from a synthetic landscape derived from the knobs
    in `config` instead of an actual simulation. `sniper`, `outputdir`, and
    `cmd` are accepted for interface compatibility but are not used.
    """
    knobs = _read_cfg_knobs(config)
    coords = _normalize_knobs(knobs)
    growth = sum(coords) / len(coords)

    rng = random.Random(_deterministic_seed(knobs))
    landscape = sum(
        math.sin(TEST_LANDSCAPE_FREQ * math.pi * c + i)
        for i, c in enumerate(coords)
    ) / len(coords)

    area = TEST_BASE_AREA * (1 + TEST_AREA_GAIN * growth) * (1 + rng.gauss(0, TEST_NOISE_STD))
    peak_power = TEST_BASE_POWER * (1 + TEST_POWER_GAIN * growth ** 1.15) * (1 + rng.gauss(0, TEST_NOISE_STD))
    time = TEST_BASE_TIME * (1 - TEST_SPEEDUP_GAIN * growth + TEST_LANDSCAPE_AMPLITUDE * landscape)
    time *= (1 + rng.gauss(0, TEST_NOISE_STD))
    time = max(time, TEST_BASE_TIME * TEST_MIN_TIME_FRACTION)

    return area, peak_power, time


def run(config: str, sniper: Path, outputdir: Path, cmd: list[str]) -> tuple[float, float, float]:
    forwarded: list[str] = [str(sniper), "-d", str(outputdir), "--power", "-c", config]

    if cmd[:1] == ["--"]:
        forwarded.extend(cmd[1:])
    else:
        forwarded.extend(cmd)

    env = os.environ.copy()
    env.setdefault("SNIPER_ROOT", str(ROOT / "snipersim"))

    result = subprocess.run([sys.executable, *forwarded], env=env, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"Sniper failed with code {result.returncode}: {result.stderr}")

    power_file = Path(outputdir) / "power.txt"
    if not power_file.exists():
        raise FileNotFoundError(f"missing power report: {power_file}")

    area = None
    peak_power = None
    in_processor_section = False

    for raw_line in power_file.read_text(encoding="utf-8").splitlines():
        if raw_line.startswith("  ") and not raw_line.startswith("   "):
            line = raw_line.strip()
            if in_processor_section:
                if area is None and line.startswith("Area = ") and line.endswith("mm^2"):
                    area = float(line.split("=", 1)[1].split("mm^2")[0].strip())
                elif peak_power is None and line.startswith("Peak Power = ") and line.endswith("W"):
                    peak_power = float(line.split("=", 1)[1].split("W")[0].strip())
                if area is not None and peak_power is not None:
                    break
        if raw_line.strip() == "Processor:":
            in_processor_section = True

    if area is None or peak_power is None:
        raise ValueError(f"Could not extract Area or Peak Power from {power_file}")

    sniper_out = Path(outputdir) / "sim.out"
    if not sniper_out.exists():
        raise FileNotFoundError(f"missing sniper output: {sniper_out}")

    time = None
    for raw_line in sniper_out.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("Time (ns)"):
            time_str = line.split("|", 1)[1].strip()
            try:
                time = float(time_str)
            except ValueError:
                raise ValueError(f"Could not parse Time value: {time_str}")
            break

    if time is None:
        raise ValueError(f"Could not extract Time from {sniper_out}")

    return area, peak_power, time