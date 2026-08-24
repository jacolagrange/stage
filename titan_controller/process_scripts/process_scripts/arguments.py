import json
from pathlib import Path
from typing import List

from process_scripts.sim_information import SimulatorInformation, SimData

class SimArgs(SimulatorInformation):
    def __init__(self, dir_path: Path):
        self.file_path = dir_path / "args.json"

    def get(self) -> List[SimData]:
        read_p = self.file_path
        dict_args = {}
        if not read_p.exists():
            print(f"WARNING args.json at path {read_p} does not exist, proceeding without")
            return None
        with open(read_p, "r") as json_file:
            try:
                dict_args = json.load(json_file)
            except Exception as e:
                import traceback
                print(f"Something went wrong reading json_file {read_p}")
                print(e)
                traceback.print_exc()
                exit(1)
            json_file.close()
    
        return SimArgs.from_dict(dict_args)
    
    def from_dict(dict_args: dict) -> List[SimData]:
        return [SimData("args", param, None, None, val) for param, val in dict_args.items()]

