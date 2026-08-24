#!/bin/env python3

# To install the process_scripts you need to run `pip3 install ~/path/to/process_scripts/`
# and for this script `pip3 install scipy` too.

from process_scripts import process_data
from process_scripts.graph_parameters import *
import pandas
import numpy as np
import seaborn as sns
from scipy.stats.mstats import hmean
try:
    matplotlib.use('tkagg') #to allow X11 forwarding of matplotlib, when using SSH
except ImportError as e:
    print("tkagg is not working here")
pandas.set_option('display.max_columns', None)
pandas.set_option('display.max_rows', None)
pandas.set_option('future.no_silent_downcasting', True)

HOME = "/home/jaime/Documents/projects/iovr"      #Where are the project files, with raw (for the experiment files), processed to output the graphs to
SAVE_HOME = f"{HOME}/processed/paper/spec"                    #So inside the projects, where is the output folder for the graphs
metrics = [
    {
        "metric": ("performance_model", "elapsed_time"),
        "full_name": "performance_model-elapsed_time",
        "avg_cores": None, #all cores
        "y_label": "time (fs)",
        "where": "stats"
    },{
        "metric": ("performance_model", "instruction_count"),
        "full_name": "performance_model-instruction_count",
        "avg_cores": None,
        "y_label": "nr instructions",
        "where": "stats"
    },{
        "metric": "perf_model/core/frequency",
        "full_name": "perf_model-core-frequency",
        "y_label": "frequency",
        "where": "config"
    },{
        "metric": ("IPC", "normalized_IPC"), #TODO: in plot_data, need to check if it is a tuple, for metric data for example
        "full_name": "IPC",
        "y_label": "normalized IPC",
        "where": "only plot"
    },
    # {
    #     "metric": ("IPC", "IPC"), #TODO: in plot_data, need to check if it is a tuple, for metric data for example
    #     "full_name": "IPC",
    #     "y_label": "IPC",
    #     "where": "only plot"
    #     }
    {
        "metric": ("front_end_scanner", "num_matches"),
        "full_name": "address-match",
        "y_label": "address-match",
        "where": "stats"
    },
    {
        "metric": ("front_end_scanner", "num_rands"),
        "full_name": "address-rands",
        "y_label": "address-rands",
        "where": "stats"
    },
    {
        "metric": ("front_end_scanner", "num_rands_extra"),
        "full_name": "address-rands-extra",
        "y_label": "address-rands-extra",
        "where": "stats"
    },
    ]

# Fetch the data from the database, and save it in a pandas file
data_baseline: pandas.DataFrame = process_data.get_values_pandas(f"{HOME}/raw/baseline_spec", metrics, force_remake = False)
data_baseline = process_data.make_ipc(data_baseline)
data_baseline.loc[data_baseline.loc[:, "in_order"] == "true", 'cpu_type'] = "in order"
data_baseline.loc[data_baseline.loc[:, "in_order"] == "false", 'cpu_type'] = "out of order"

data_svr: pandas.DataFrame = process_data.get_values_pandas(f"{HOME}/raw/svr_spec", metrics, force_remake = False)
data_svr = process_data.make_ipc(data_svr)
data_svr.loc[:, 'cpu_type'] = 'SVR'
data_svr.loc[:, "cpu_type"] = data_svr.loc[:, "cpu_type"].str.cat(data_svr.loc[:, "max_prefetch_dist"].astype("string"), sep=' ')

data = pandas.concat([data_baseline, data_svr], ignore_index=True)

# Rename the data for better names
data = pandas.concat([data_baseline, data_svr])
better_names = {
        "in-order": "in order",
        "out-of-order": "out of order",
        "imp": "IMP"
        }
data.loc[:, "cpu_type"] = data.loc[:, "cpu_type"].replace(better_names)

# Get harmonic mean
data_processed = data[(data["objectname"] == "IPC") & (data["core_id"] == -1)]
data_processed = data_processed.fillna(0)
data_processed = data_processed.pivot_table(index=["benchmark", "cpu_type", "max_prefetch_dist"], columns=["metricname"], values="mean")
data_processed = data_processed.fillna(0)

h_mean = data_processed.groupby(["cpu_type", "max_prefetch_dist"])[["IPC"]].agg(hmean).reset_index()
h_mean.loc[:,"benchmark"] = "H-mean"
print(h_mean)

data_processed = pandas.concat([data_processed.reset_index(), h_mean])

# Normalize the data
def norm_group(group):
    group.loc[:, "norm IPC"] = group.loc[:, "IPC"] / group.loc[group.loc[:, "cpu_type"] == "in order", "IPC"].values[0]
    return group
data_processed = data_processed.groupby("benchmark").apply(norm_group, include_groups=False).reset_index()

benchmark_order = np.append(np.sort(np.unique(data_processed["benchmark"].to_numpy())), "H-mean")

# Plot the data
rank_order = ["in order", "SVR 16", "out of order"]
fig, sns_ax = plt.subplots(figsize=(7, 2.16))# page_figsize)

sns_ax = sns.barplot(
    ax=sns_ax,
    data = data_processed,
    x= "benchmark",
    y= "norm IPC",
    errorbar=None,
    hue = "cpu_type",
    hue_order = rank_order,
    order = benchmark_order,
    # palette = [pale_palette[idx], color_palette[idx]],
    edgecolor='#1a1a1a'
    )


handles, labels = sns_ax.get_legend_handles_labels()
sns_ax.legend(ncols=len(rank_order), loc='lower center', bbox_to_anchor=(0.5, 1.02))

sns_ax.set_xlabel("")
sns_ax.set_ylabel("normalized IPC")
 
sns_ax.yaxis.set_minor_locator(matplotlib.ticker.AutoMinorLocator(y_minor_ndivs))
sns_ax.set_xticks(sns_ax.get_xticks())
sns_ax.set_xticklabels(sns_ax.get_xticklabels(), rotation=45, ha='right', rotation_mode='anchor') #to rotate labels
sns_ax.grid(which="minor", axis="y", linestyle='--')
plt.savefig(f"{SAVE_HOME}/spec_norm_SVR.pdf", bbox_inches='tight')
