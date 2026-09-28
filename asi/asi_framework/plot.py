from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.colors import Normalize

from .models import DesignPoint


def _asi_region_bounds(speedups: list[float], asi_values: list[float]) -> tuple[float, float, float, float]:
    x_min = max(0.1, min(speedups) * 0.85)
    x_max = max(speedups) * 1.15
    y_min = min(asi_values) * 1.15 if min(asi_values) < 0 else 0.0
    y_max = max(4.0, max(asi_values) * 1.15)
    return x_min, x_max, y_min, y_max


def _draw_asi_regions(ax, x_min: float, x_max: float, y_min: float, y_max: float) -> None:
    """Shared background for both plot functions: the ASI sustainability
    regions (Fig. 1 of the paper) and the (1,1) reference point."""
    S = np.linspace(x_min, x_max, 500)
    upper = np.maximum(1, 1 / S)
    lower = np.minimum(1, 1 / S)

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)

    ax.fill_between(S, upper, y_max, color="#d4edda", label="Strongly Sustainable")
    ax.fill_between(S, y_min, lower, color="#f8d7da", label="Unsustainable")
    ax.fill_between(S, lower, upper, color="#fff3cd", label="Weakly Sustainable")

    ax.plot(S, upper, color="blue",  linewidth=1.5, linestyle="--", alpha=0.7)
    ax.plot(S, lower, color="green", linewidth=1.5, linestyle="--", alpha=0.7)

    ax.scatter(1, 1, color="black", s=100, zorder=5)
    ax.text(1.02, 1.03, "Ref (1,1)", fontsize=9, fontweight="bold", zorder=5)


def _finish(fig, ax, title: str, save_path: Path | None, show: bool) -> None:
    ax.set_title(title, fontsize=14, pad=12)
    ax.set_xlabel("Speedup (S = 1/Tₙ)", fontsize=12)
    ax.set_ylabel("ASI", fontsize=12)
    ax.grid(True, linestyle=":", alpha=0.5)

    plt.tight_layout()

    if save_path is not None:
        save_path = Path(save_path)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)


def plot_pareto_front_on_asi(
    front: list[DesignPoint],
    title: str = "ASI Pareto Front",
    save_path: Path | None = None,
    show: bool = True,
) -> None:
    """Plot ASI sustainability regions with a single Pareto front overlaid."""
    speedups = [p.speedup for p in front]
    asi_values = [p.asi for p in front]
    x_min, x_max, y_min, y_max = _asi_region_bounds(speedups, asi_values)

    fig, ax = plt.subplots(figsize=(9, 7))
    _draw_asi_regions(ax, x_min, x_max, y_min, y_max)

    ax.scatter(speedups, asi_values, color="purple", edgecolors="black", s=80, zorder=6, label="Pareto Front")

    ax.legend(loc="upper right", framealpha=0.95)
    _finish(fig, ax, title, save_path, show)


def plot_pareto_fronts_on_asi(
    fronts: list[list[DesignPoint]],
    title: str = "ASI Pareto Fronts",
    sequence_label: str = "Generation",
    save_path: Path | None = None,
    show: bool = True,
) -> None:
    """Plot a sequence of Pareto fronts (e.g. one per generation), color-coded
    by position; the last front is drawn on top as large, solid points."""
    non_empty = [f for f in fronts if f]
    if not non_empty:
        raise ValueError("plot_pareto_fronts_on_asi requires at least one non-empty front")

    all_speedups = [p.speedup for f in non_empty for p in f]
    all_asi = [p.asi for f in non_empty for p in f]
    x_min, x_max, y_min, y_max = _asi_region_bounds(all_speedups, all_asi)

    fig, ax = plt.subplots(figsize=(9, 7))
    _draw_asi_regions(ax, x_min, x_max, y_min, y_max)

    n = len(fronts)
    cmap = plt.get_cmap("viridis")
    norm = Normalize(vmin=0, vmax=max(1, n - 1))
    for i, front in enumerate(fronts):
        if not front:
            continue
        speedups = [p.speedup for p in front]
        asi_values = [p.asi for p in front]
        is_last = i == n - 1
        ax.scatter(
            speedups, asi_values,
            color="purple" if is_last else cmap(norm(i)),
            edgecolors="black", linewidths=0.8 if is_last else 0.3,
            s=110 if is_last else 35,
            alpha=1.0 if is_last else 0.6,
            zorder=7 if is_last else 6,
            label="Final Pareto Front" if is_last else None,
        )

    sm = cm.ScalarMappable(cmap=cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(sm, ax=ax, pad=0.02)
    cbar.set_label(sequence_label)

    ax.legend(loc="upper right", framealpha=0.95)
    _finish(fig, ax, title, save_path, show)


def plot_hv_vs_simulations(
    sim_history: list[int],
    hv_history: list[float],
    size_history: list[int],
    title: str = "Hypervolume & Pareto Front Size vs. Simulations",
    save_path: Path | None = None,
    show: bool = True,
    switch_sims: list[int] | None = None,
    switch_label: str = "Strategy switch",
) -> None:
    """Two stacked step-plots vs. cumulative configurations evaluated:
    hypervolume on top, Pareto front size on the bottom. switch_sims (used by
    hybrid) draws a vertical line + annotation at each strategy handoff."""
    fig, (ax_hv, ax_size) = plt.subplots(2, 1, figsize=(8, 8), sharex=True)

    ax_hv.step(sim_history, hv_history, where="post", color="#6f42c1", linewidth=1.75, zorder=3)
    ax_hv.scatter(sim_history, hv_history, color="#6f42c1", edgecolors="black", s=28, zorder=4)
    ax_hv.set_ylabel("Hypervolume", fontsize=12)
    ax_hv.set_title(title, fontsize=14, pad=12)
    ax_hv.grid(True, linestyle=":", alpha=0.5)

    ax_size.step(sim_history, size_history, where="post", color="#fd7e14", linewidth=1.75, zorder=3)
    ax_size.scatter(sim_history, size_history, color="#fd7e14", edgecolors="black", s=28, zorder=4)
    ax_size.set_xlabel("Cumulative configurations evaluated", fontsize=12)
    ax_size.set_ylabel("Pareto front size (points)", fontsize=12)
    ax_size.grid(True, linestyle=":", alpha=0.5)
    ax_size.set_xlim(left=0)

    for i, x in enumerate(switch_sims or []):
        ax_hv.axvline(x, color="#495057", linewidth=1.25, linestyle="--", zorder=2,
                       label=switch_label if i == 0 else None)
        ax_size.axvline(x, color="#495057", linewidth=1.25, linestyle="--", zorder=2)
        ax_hv.annotate(
            f"{x} configs", xy=(x, 1), xycoords=("data", "axes fraction"),
            xytext=(4, -4), textcoords="offset points",
            fontsize=9, color="#495057", ha="left", va="top", rotation=90, zorder=5,
        )
    if switch_sims:
        ax_hv.legend(loc="lower right", framealpha=0.95)

    if sim_history:
        ax_hv.annotate(
            f"{sim_history[-1]} configs total", xy=(sim_history[-1], hv_history[-1]),
            xytext=(-6, 10), textcoords="offset points", clip_on=False,
            fontsize=9, color="#6f42c1", ha="right", va="bottom", fontweight="bold", zorder=5,
        )
        ax_hv.margins(y=0.12)

    plt.tight_layout()

    if save_path is not None:
        save_path = Path(save_path)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)
