import re
from pathlib import Path
from typing import List

from process_scripts.sim_information import SimulatorInformation, SimData
# ---------------------------------------------------- All read simulated time (txt) realted methods -------------------------------

reg_sim_time = re.compile(r"\[SNIPER\] Elapsed time: [0-9]*\.[0-9]* seconds")
class SimStdout(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.file_path = dir_path / "stdout_vm.txt"

    def get(self) -> List[SimData]:
        global reg_sim_time
        elapsed_time = 0
        values = []
        with open(self.file_path, "r") as file_descriptor:
            lines = str(file_descriptor.readlines())
            file_descriptor.close()
            str_elapsed_time = str(reg_sim_time.search(lines).group(0)).split()[3]
            elapsed_time = float(str_elapsed_time)
            values = [SimData("stdout", "Wall Time", None, None, elapsed_time)]
        return values
