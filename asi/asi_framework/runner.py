import subprocess
from pathlib import Path

from .config_builder import build_runtime_config


def run(
    reference_config: str,
    sniper: Path,
    outputdir: Path,
    cmd: list[str],
    design_knobs: dict = None,
) -> tuple[float, float, float]:
    """
    Run the Sniper simulator and return (area_mm2, peak_power_W, time_ns).
    """
    knobs = design_knobs or {}
    outputdir = Path(outputdir)
    outputdir.mkdir(parents=True, exist_ok=True)

    total_cores = knobs.get("cores", 1)
    override_flags = build_runtime_config(reference_config, **knobs)

    run_args = [
        str(sniper),
        "-n", str(total_cores),
        "-c", str(reference_config),
        "-d", str(outputdir),
        "--roi",
        "--power",
    ] + override_flags + ["--"] + cmd

    try:
        result = subprocess.run(
            run_args,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        stdout_log = result.stdout
        stderr_log = result.stderr
    except subprocess.CalledProcessError as err:
        print("=== Sniper STDOUT ===")
        print(err.stdout)
        print("=== Sniper STDERR ===")
        print(err.stderr)
        raise RuntimeError(f"Sniper exited with status {err.returncode}")

    def fail(exc_cls, msg):
        print("\n=== SNIPER STDOUT ===")
        print(stdout_log)
        print("=== SNIPER STDERR ===")
        print(stderr_log)
        raise exc_cls(msg)

    # --- Parse power.txt (McPAT output) ---
    power_file = outputdir / "power.txt"
    if not power_file.exists():
        power_file = outputdir / "power" / "power.txt"
    if not power_file.exists():
        fail(FileNotFoundError, f"power.txt not found under {outputdir}")

    area = peak_power = None
    in_processor = False
    for raw in power_file.read_text(encoding="utf-8").splitlines():
        if raw.strip() == "Processor:":
            in_processor = True
            continue
        if in_processor and raw.startswith("  ") and not raw.startswith("   "):
            line = raw.strip()
            if area is None and line.startswith("Area = ") and line.endswith("mm^2"):
                area = float(line.split("=", 1)[1].split("mm^2")[0].strip())
            elif peak_power is None and line.startswith("Peak Power = ") and line.endswith("W"):
                peak_power = float(line.split("=", 1)[1].split("W")[0].strip())
            if area is not None and peak_power is not None:
                break

    if area is None or peak_power is None:
        fail(ValueError, f"Could not parse Area/Peak Power from {power_file}")

    # --- Parse sim.out (execution time) ---
    simout = outputdir / "sim.out"
    if not simout.exists():
        fail(FileNotFoundError, f"sim.out not found in {outputdir}")

    time_ns = None
    for raw in simout.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("Time (ns)"):
            try:
                time_ns = float(line.split("|", 1)[1].strip())
            except (IndexError, ValueError):
                fail(ValueError, f"Could not parse Time (ns) line: {line!r}")
            break

    if time_ns is None:
        fail(ValueError, f"'Time (ns)' not found in {simout}")

    return area, peak_power, time_ns
