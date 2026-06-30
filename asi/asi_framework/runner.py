import os
import sys
from pathlib import Path
import subprocess
 
from .config import ROOT
 
 
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
 
