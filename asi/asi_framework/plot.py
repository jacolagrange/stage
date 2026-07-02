from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

from .models import DesignPoint


def plot_pareto_front_on_asi(
    front: list[DesignPoint],
    title: str = "ASI Pareto Front",
    save_path: Path | None = None,
) -> None:
    """
    Plot ASI sustainability regions (Fig. 1 of the paper) with Pareto front overlaid.
    If save_path is given the figure is saved there before being shown.
    """
    speedups = [p.speedup for p in front]
    asi_values = [p.asi for p in front]

    x_min = max(0.1, min(speedups) * 0.85)
    x_max = max(speedups) * 1.15
    y_max = max(4.0, max(asi_values) * 1.15)

    S = np.linspace(x_min, x_max, 500)
    upper = np.maximum(1, 1 / S)
    lower = np.minimum(1, 1 / S)

    fig, ax = plt.subplots(figsize=(9, 7))
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0, y_max)

    ax.fill_between(S, upper, y_max, color="#d4edda", label="Strongly Sustainable")
    ax.fill_between(S, 0, lower,  color="#f8d7da", label="Unsustainable")
    ax.fill_between(S, lower, upper, color="#fff3cd", label="Weakly Sustainable")

    ax.plot(S, upper, color="blue",  linewidth=1.5, linestyle="--", alpha=0.7)
    ax.plot(S, lower, color="green", linewidth=1.5, linestyle="--", alpha=0.7)

    ax.scatter(1, 1, color="black", s=100, zorder=5)
    ax.text(1.02, 1.03, "Ref (1,1)", fontsize=9, fontweight="bold", zorder=5)

    ax.scatter(speedups, asi_values, color="purple", edgecolors="black", s=80, zorder=6, label="Pareto Front")

    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("Speedup (S = 1/Tₙ)", fontsize=12)
    ax.set_ylabel("ASI", fontsize=12)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.legend(loc="upper right", framealpha=0.95)

    plt.tight_layout()

    if save_path is not None:
        save_path = Path(save_path)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved → {save_path}")

    plt.show()
