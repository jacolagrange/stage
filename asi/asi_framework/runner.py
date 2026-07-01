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
from .config_builder import build_runtime_config

TEST_BASE_AREA = 100.0
TEST_BASE_POWER = 50.0

# --- run_test: synthetic stand-in for run() -------------------------------
def run_test(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    design_knobs: dict = None
) -> tuple[float, float, float]:
    """
    Synthetic cost surface with stochastic noise and parameter weighting.
    Parameters with low weights will appear 'irrelevant' to the sensitivity logic.
    """
    knobs = design_knobs or {}

    # Define 'Sensitivity Weights': how much each param affects the outcome
    # l1i_assoc and l3_assoc are intentionally set to near-zero impact
    weights = {
        "l1i_size": 0.05, "l1d_size": 0.05,
        "l2_size": 0.02,  "l3_size": 0.01,
        "l1i_assoc": 0.0001, "l1d_assoc": 0.0002, 
        "l2_assoc": 0.0002,  "l3_assoc": 0.0001,
        "branch_predictor_size": 0.01,
        "rob_rs_entries": 0.15,
    }

    # Base values
    area = TEST_BASE_AREA
    peak_power = TEST_BASE_POWER
    time_val = 100.0

    # Apply weighted impacts
    for param, val in knobs.items():
        w = weights.get(param, 0.01)
        area += w * val
        peak_power += (w * 0.4) * val
        time_val -= (w * 5.0) * val

    # Add stochastic noise (Simulating system variance)
    # The sensitivity logic in search.py will see this variance as the "signal"
    # for parameters with high weights, and as "insignificant jitter" for low weights.
    area += random.gauss(0, 0.05)
    peak_power += random.gauss(0, 0.02)
    time_val += random.gauss(0, 0.5)

    return max(1.0, area), max(0.1, peak_power), max(10.0, time_val)


# --- Real physical Sniper Simulation Runner ---------------------------------
def run(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    design_knobs: dict = None
) -> tuple[float, float, float]:
    """
    Runs the actual physical Sniper simulator using command-line config overrides.
    """
    knobs = design_knobs or {}
    outputdir = Path(outputdir)
    outputdir.mkdir(parents=True, exist_ok=True)

    # Extract or default the total number of cores
    total_cores = knobs.get("cores", 1)

    # 1. Generate CLI flags from the knobs dictionary
    override_flags = build_runtime_config(reference_config, **knobs)

    # 2. Build execution arguments list, adding the explicit '-n' flag for core sizing
    run_args = [
        str(sniper), 
        "-n", str(total_cores), 
        "-c", str(reference_config), 
        "-d", str(outputdir)
    ] + override_flags + ["--"] + cmd

    # 3. Execute process and capture output logs
    try:
        result = subprocess.run(
            run_args, 
            check=True, 
            stdout=subprocess.PIPE, 
            stderr=subprocess.PIPE, 
            text=True
        )
        stdout_log = result.stdout
        stderr_log = result.stderr
    except subprocess.CalledProcessError as err:
        print("=== Sniper Execution STDOUT ===")
        print(err.stdout)
        print("=== Sniper Execution STDERR ===")
        print(err.stderr)
        raise RuntimeError(f"Sniper simulator process exited with error status: {err.returncode}")

    # Helper function to print logs if parsing fails
    def raise_with_logs(exception_class, message):
        print("\n=== SNIPER RUN LOGS (STDOUT) ===")
        print(stdout_log)
        print("=== SNIPER RUN LOGS (STDERR) ===")
        print(stderr_log)
        raise exception_class(message)

    # 4. Parse power metrics (McPAT output)
    power_file = outputdir / "power.txt"
    if not power_file.exists():
        power_file = outputdir / "power" / "power.txt"
        
    if not power_file.exists():
        raise_with_logs(
            FileNotFoundError, 
            f"McPAT power file summary (power.txt) not found in {outputdir} or its power/ subdirectory."
        )

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
        raise_with_logs(ValueError, f"Could not extract Area or Peak Power metrics inside: {power_file}")

    # 5. Parse timing metrics (sim.out processing)
    simout_file = outputdir / "sim.out"
    if not simout_file.exists():
        raise_with_logs(FileNotFoundError, f"missing sniper output trace: {simout_file}")

    time_val = None
    for raw_line in simout_file.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if line.startswith("Time (ns)"):
            time_str = line.split("|", 1)[1].strip()
            try:
                time_val = float(time_str)
            except ValueError:
                raise_with_logs(ValueError, f"Could not cleanly parse Time string numeric formatting: {time_str}")
            break

    if time_val is None:
        raise_with_logs(ValueError, f"Could not locate 'Time (ns)' signature string sequence inside {simout_file}")

    return area, peak_power, time_val