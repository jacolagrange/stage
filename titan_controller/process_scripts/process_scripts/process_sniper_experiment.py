import pandas as pd
from typing import List, Dict, Tuple
from pathlib import Path

import process_scripts.utils as utils
from process_scripts.extractor import SimParamExtractor
from process_scripts.json_extractor import JsonSimParamExtractor
import process_scripts.compute as compute

def get(exp_path: Path) -> pd.DataFrame:
    sim_param = {}
    if exp_path.is_file() and exp_path.suffix == ".json":
        sim_param = JsonSimParamExtractor(exp_path)
    elif exp_path.is_dir():
        sim_param = SimParamExtractor(exp_path)
    else:
        print("Trying to parse unsupported file type?")

    nested_data = sim_param.get()
    df = utils.sim_data_to_df(nested_data)

    #Taking averages accross cores -> runs -> benhcmarks
    df_core_avg = compute.average_across_cores(df)
    df_run_avg = compute.average_on_runs(df_core_avg)
    df = compute.make_ipc(df_run_avg)
    df = compute.average_on_benchmarks(df)

    return df

def get_saved(exp_path: str, force_remake: bool = False) -> pd.DataFrame:
    '''
    Parameters
    ----------
    exp_path: Where are the experiments located in the sturcture: experiments_variation -> 0-15 -> actual experiments files
    metrics: Which metrics should be looked at, if empty get all metrics
    '''
    exp_path = Path(exp_path)
    dir_path = exp_path if exp_path.is_dir() else exp_path.parent
    df_save = dir_path / "df_backup_v2.pickle.xz"
    df = utils.retrieve_dataframe(df_save)
    if df.empty or force_remake:
        df = get(exp_path)
        df.to_pickle(df_save, compression = "infer")
   
    return df
