from pathlib import Path
import subprocess
from typing import List, Dict

import process_scripts.const as const
from process_scripts.sim_information import SimulatorInformation, SimData

class SimCpi(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.dir_path = dir_path

    def get(self) -> List[SimData]:
        cmd = [const.CPI_SCRIPT_PATH, "-d", str(self.dir_path), "|", "tr", "-s", "\" \""]
        cpi_output_file = self.dir_path / "cpi-stack.txt"
        if(not cpi_output_file.exists()):
            f = open(cpi_output_file, "w")
            subprocess.run(cmd, cwd=self.dir_path, shell=True, stdout=f)
            f.close()
    
        values = []
        found_first_line = False
        with cpi_output_file.open() as f:
            for line in f.readlines():
                if "CPI" in line:
                    found_first_line = True
                    continue
                if not found_first_line:
                    continue
    
                tmp = line.split()
                if(len(tmp) == 0):
                    continue
    
                element, value, time = tmp
                values.append(SimData("CPI", element, None, None, float(value)))
    
        return values
