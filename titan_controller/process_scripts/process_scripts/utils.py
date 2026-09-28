import json
from pathlib import Path
import re
import pandas as pd
from typing import List

def get_experiment_baseline(json_path: str) -> dict:
    experiment_path = Path(json_path)
    baseline = {}
    if not experiment_path.exists():
        return baseline
    with open(experiment_path, "r") as json_file:
        data = json.load(json_file)
        for param_name, param_values in data["sniper_parameters"]["parameters"][0]["values"].items():
            baseline[param_name] = param_values[0]
    return baseline

def find_value_in_file(file_path: Path, regex_string: str) -> List[str]:
    reg_pattern = re.compile(regex_string)
    fd = open(file_path)
    all_values = []
    for line in fd:
        for res in reg_pattern.finditer(line):
            all_values.append(res.group())
    return all_values

def get_all_subdirs(exp_dir_name : str) -> List[Path]:
    raw_data_path = Path(exp_dir_name)
    for sub_dir in raw_data_path.glob("*"):
        if (sub_dir.is_dir()):
            yield sub_dir

def retrieve_dataframe(df_save: Path) -> pd.DataFrame:
    df = pd.DataFrame()
    if df_save.exists():
        df = pd.read_pickle(df_save, compression="infer")
    return df

def sim_data_to_df(nested: dict) -> pd.DataFrame:
    rows = []
    row_index = []
    col_keys = set()

    for param_id, benchmarks in nested.items():
        for benchmark, runs in benchmarks.items():
            for run_idx, simdata_list in runs.items():
                row_data = {}
                row_index.append((param_id, benchmark, run_idx))

                for sim in simdata_list:
                    col = (sim.source, sim.section, sim.key, sim.core)
                    row_data[col] = sim.value
                    col_keys.add(col)

                rows.append(row_data)

    # Build DataFrame
    all_cols = sorted(col_keys)
    df = pd.DataFrame(rows, index=pd.MultiIndex.from_tuples(row_index, names=["param_id", "benchmark", "run_idx"]))
    df = df.reindex(columns=all_cols)  # fill missing columns with NaN
    df.columns = pd.MultiIndex.from_tuples(df.columns, names=["source", "section", "key", "core"])

    return df
