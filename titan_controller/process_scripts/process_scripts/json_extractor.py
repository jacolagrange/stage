from typing import List, Optional
from pathlib import Path
import pandas as pd
import json

from process_scripts.sim_information import SimulatorInformation, SimData

from process_scripts.arguments import SimArgs
from process_scripts.simstdout import SimStdout
from process_scripts.simcpi import SimCpi
from process_scripts.simmcpat import SimMcPat
from process_scripts.simcfg import SimCfg
from process_scripts.simstat import SimStat

from process_scripts.extractor import RunExtractor

import process_scripts.utils as utils

class JsonBenchExtractor:
    def __init__(self, dir_path: Path, benchmarks_json: list):
        self.dir_path = dir_path
        self.benchmarks_json = benchmarks_json

    def get(self, sim_args: SimData):
        data = {}
        for bench_json_data in self.benchmarks_json:
            bench_name = bench_json_data["benchmark_name"]
            bench_path = self.dir_path / bench_name
            bench_data = {}

            for bench_run_data in bench_json_data["benchmark_runs"]:
                exp_nr = bench_run_data["run_idx"]
                exp_path = bench_path / str(exp_nr)
                print(f"Processing {exp_path}")

                run_info = RunExtractor(exp_path)
                run_data = run_info.get()
                run_data += sim_args
                bench_data[exp_nr] = run_data

            data[bench_name] = bench_data

        return data

class JsonSimParamExtractor:
    def __init__(self, json_file: Path):
        self.json_file = json_file

    def get(self):
        all_exp = {}
        with open(self.json_file) as jf:
            json_data = json.load(jf)
            
            for suite in json_data["benchmark_suites"]:
                suite_path = Path(suite["host_dst_path"])
                for sim_params in suite["simulator_parameters"]:
                    sim_dir_name = sim_params["simulator_dir_name"]
                    sim_path = suite_path / sim_dir_name
                    sim_args_data = SimArgs.from_dict(sim_params["variable_sniper_parameters"])

                    benches = JsonBenchExtractor(sim_path, sim_params["benchmark_parameters"])
                    bench_data = benches.get(sim_args_data)
                    
                    all_exp[sim_dir_name] = bench_data

        return all_exp
