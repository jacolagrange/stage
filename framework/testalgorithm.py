import shutil
import subprocess
import sys
from pathlib import Path


framework_dir = Path(__file__).resolve().parent
output_dir = framework_dir / "sniper-output"
combined_power_file = framework_dir / "power.txt"


with combined_power_file.open("w", encoding="utf-8") as combined:
    for val in [1024, 512, 256]:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)

        subprocess.run(
            [
                sys.executable,
                str(framework_dir / "run.py"),
                "--outputdir",
                str(output_dir),
                "--l3-size",
                str(val),
                "--",
                "/bin/echo",
                "hello",
            ],
            check=True,
        )

        power_file = output_dir / "power.txt"
        if not power_file.exists():
            raise FileNotFoundError(f"missing power report: {power_file}")
        combined.write(power_file.read_text(encoding="utf-8"))