from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt
import matplotlib.cm as cm
from matplotlib.colors import Normalize

from .models import DesignPoint


def _asi_region_bounds(speedups: list[float], asi_values: list[float]) -> tuple[float, float, float]:
    x_min = max(0.1, min(speedups) * 0.85)
    x_max = max(speedups) * 1.15
    y_max = max(4.0, max(asi_values) * 1.15)
    return x_min, x_max, y_max


def _draw_asi_regions(ax, x_min: float, x_max: float, y_max: float) -> None:
    """Shared background for both plot functions: the ASI sustainability
    regions (Fig. 1 of the paper) and the (1,1) reference point."""
    S = np.linspace(x_min, x_max, 500)
    upper = np.maximum(1, 1 / S)
    lower = np.minimum(1, 1 / S)

    ax.set_xlim(x_min, x_max)
    ax.set_ylim(0, y_max)

    ax.fill_between(S, upper, y_max, color="#d4edda", label="Strongly Sustainable")
    ax.fill_between(S, 0, lower,  color="#f8d7da", label="Unsustainable")
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
    """
    Plot ASI sustainability regions (Fig. 1 of the paper) with a single
    Pareto front overlaid. If save_path is given the figure is saved there
    before being shown.
    """
    speedups = [p.speedup for p in front]
    asi_values = [p.asi for p in front]
    x_min, x_max, y_max = _asi_region_bounds(speedups, asi_values)

    fig, ax = plt.subplots(figsize=(9, 7))
    _draw_asi_regions(ax, x_min, x_max, y_max)

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
    """
    Plot a sequence of Pareto fronts (e.g. one per SPEA2 generation) overlaid
    on the same ASI sustainability-region background. Earlier fronts are
    shown as small, faint points color-coded by their position in the
    sequence (via a colorbar rather than a per-front legend entry, since
    there can be dozens of generations); the last front is drawn on top as
    large, solid points so the final result stands out.
    """
    non_empty = [f for f in fronts if f]
    if not non_empty:
        raise ValueError("plot_pareto_fronts_on_asi requires at least one non-empty front")

    all_speedups = [p.speedup for f in non_empty for p in f]
    all_asi = [p.asi for f in non_empty for p in f]
    x_min, x_max, y_max = _asi_region_bounds(all_speedups, all_asi)

    fig, ax = plt.subplots(figsize=(9, 7))
    _draw_asi_regions(ax, x_min, x_max, y_max)

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
    title: str = "Hypervolume vs. Simulations",
    save_path: Path | None = None,
    show: bool = True,
) -> None:
    """
    Pareto-front hypervolume (see greedy.hypervolume) against the cumulative
    number of *real* Sniper simulations spent to reach it -- the number every
    strategy's search actually costs, as opposed to iteration/generation
    count, which means something different for each strategy (mesmo's
    default batch_size=1 spends exactly one simulation per iteration, so its
    curve is a near-continuous per-simulation trace; greedy's iterations and
    spea2's generations each evaluate a whole batch of configurations before
    the front -- and hence hv_history -- updates again, so both show up here
    as flat plateaus followed by a jump). Drawn as a step function
    (`where="post"`) since hv_history only actually changes at those jump
    points -- interpolating between them would imply progress that didn't
    happen. `sim_history`/`hv_history` are the parallel lists each search
    strategy checkpoints in its own resumable state (GreedySearchState.
    sim_history/hv_history, etc.), one pair of entries per iteration/
    generation, both starting at the pre-search state (0 simulations, the
    baseline-only front).
    """
    fig, ax = plt.subplots(figsize=(8, 5))
    ax.step(sim_history, hv_history, where="post", color="#6f42c1", linewidth=1.75, zorder=3)
    ax.scatter(sim_history, hv_history, color="#6f42c1", edgecolors="black", s=28, zorder=4)

    ax.set_xlabel("Cumulative real Sniper simulations", fontsize=12)
    ax.set_ylabel("Hypervolume", fontsize=12)
    ax.set_title(title, fontsize=14, pad=12)
    ax.grid(True, linestyle=":", alpha=0.5)
    ax.set_xlim(left=0)

    plt.tight_layout()

    if save_path is not None:
        save_path = Path(save_path)
        plt.savefig(save_path, dpi=150, bbox_inches="tight")
        print(f"Plot saved → {save_path}")

    if show:
        plt.show()
    else:
        plt.close(fig)
