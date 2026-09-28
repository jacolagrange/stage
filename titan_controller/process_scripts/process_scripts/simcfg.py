import configparser
from pathlib import Path
from typing import List, Dict, Tuple

from process_scripts.sim_information import SimulatorInformation, SimData

class SimCfg(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.file_path = dir_path / "sim.cfg"

    def _clean_value(self, value: str) -> str:
        """Remove surrounding quotes and handle escaped quotes"""
        value = value.strip()
        
        # Handle quoted strings
        if (value.startswith('"') and value.endswith('"')) or \
           (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        
        return value

    def get(self) -> List[SimData]:
        config = configparser.ConfigParser()
        config.read(self.file_path)
    
        values = []
        for section in config.sections():
            for key, value in config.items(section):
                cleaned_value = self._clean_value(value)
                values.append(SimData("config", section, key, None, cleaned_value))
    
        return values
