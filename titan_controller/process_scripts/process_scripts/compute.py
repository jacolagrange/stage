import pandas as pd
from scipy.stats import hmean

#TODO this average might not be correct for all metrics? (e.g. Instructions, could want both maximum and minimum?)
def average_across_cores(df: pd.DataFrame) -> pd.DataFrame:
    # Split columns based on whether they have a valid core value
    with_core = [col for col in df.columns if col[3] not in (None, float('nan')) and not pd.isna(col[3])]
    without_core = [col for col in df.columns if col[3] in (None, float('nan')) or pd.isna(col[3])]

    df_with_core = df[with_core]
    df_without_core = df[without_core]

    # 1. Split numeric and non-numeric for with_core
    numeric_core_cols = df_with_core.select_dtypes(include='number').columns
    non_numeric_core_cols = df_with_core.select_dtypes(exclude='number').columns

    # 2. Average numeric across cores
    df_numeric = df[numeric_core_cols].T.groupby(level=[0, 1, 2]).mean().T

    # 3. Take first for non-numeric across cores
    df_strings = df[non_numeric_core_cols].T.groupby(level=[0, 1, 2]).first().T

    # 4. Combine back with non-core data
    df_core_avg = pd.concat([df_numeric, df_strings, df_without_core], axis=1)

    return df_core_avg

def average_on_runs(df: pd.DataFrame) -> pd.DataFrame:
    # Step 1: Figure out which columns are numeric
    numeric_cols = df.select_dtypes(include='number').columns
    non_numeric_cols = df.select_dtypes(exclude='number').columns
    
    # Step 2: Build the aggregation dictionary
    agg_funcs = {col: 'mean' for col in numeric_cols}
    agg_funcs.update({col: 'first' for col in non_numeric_cols})
    
    # Step 3: Group and aggregate in one go
    df_grouped = df.groupby(level=["param_id", "benchmark"]).agg(agg_funcs)
    return df_grouped

def average_on_benchmarks(df: pd.DataFrame) -> pd.DataFrame:

    # Get column list
    columns = df.columns
    agg_functions = {}

    for col in columns:
        source = col[0]
        if col == ("computed", "IPC", "IPC"):
            agg_functions[col] = hmean
        elif source in ["mcpat", "stats", "CPI"]:
            agg_functions[col] = "mean"
        #elif source in ["config", "args"]:
        else:
            agg_functions[col] = "first"

    # Drop benchmark level from index to group over remaining (e.g., param_id)
    group_levels = [i for i in df.index.names if i != "benchmark"]
    df_avg = df.groupby(level=group_levels).agg(agg_functions)

    # Add back "benchmark" level with label "H-mean"
    # df_avg = df_avg.reset_index()
    # df_avg["benchmark"] = "H-mean"
    # df_avg = df_avg.set_index(["param_id", "benchmark"])  # or whatever index you had

    return df_avg

def make_ipc(df: pd.DataFrame) -> pd.DataFrame:
    # Ensure frequency is numeric
    freq_col = ("config", "perf_model/core", "frequency")
    df.loc[:, freq_col] = pd.to_numeric(df.loc[:, freq_col], errors="coerce")

    # Compute IPC = instr / (elapsed_time × frequency in MHz)
    ipc = (
        df.loc[:, ("stats", "performance_model", "instruction_count")]
        / (
            df.loc[:, ("stats", "performance_model", "elapsed_time")]
            * df.loc[:, freq_col]
        )
        * 1_000_000  # fs to s
    )

    df.loc[:, ("computed", "IPC", "IPC")] = ipc
    return df
