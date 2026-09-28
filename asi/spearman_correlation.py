"""Spearman rank correlation between swept microarchitectural parameters and
(speedup, ASI) across the true Pareto front (final_full_factorial, 209 configs)."""
import json
from pathlib import Path

import pandas as pd

FRONT_PATH = Path(__file__).parent / "asi-output" / "final_full_factorial" / "search_state.json"
CSV_PATH = Path(__file__).parent / "configs.csv"

pareto_set = json.loads(FRONT_PATH.read_text())["pareto_set"]

df = pd.DataFrame([
    {
        "l1i_size": p["params"]["l1i_size"],
        "l1d_size": p["params"]["l1d_size"],
        "l2_size": p["params"]["l2_size"],
        "l3_size": p["params"]["l3_size"],
        "branch_predictor_type": int(p["params"]["branch_predictor_type"] == "tage"),
        "rob_window_size": p["params"]["rob_window_size"],
        "rob_dispatch_width": p["params"]["rob_dispatch_width"],
        "rob_commit_width": p["params"]["rob_commit_width"],
        "speedup": p["speedup"],
        "asi": p["asi"],
    }
    for p in pareto_set
])
df.to_csv(CSV_PATH, index=False)
print(f"{len(df)} configs written to {CSV_PATH}\n")

params = df.columns.drop(["speedup", "asi"])
summary = pd.DataFrame({
    "rho_speedup": df[params].corrwith(df["speedup"], method="spearman"),
    "rho_asi": df[params].corrwith(df["asi"], method="spearman"),
}).sort_values("rho_speedup", key=abs, ascending=False)
print(summary.round(4))

print("\nFull Spearman correlation matrix:")
print(df.corr(method="spearman").round(3))
