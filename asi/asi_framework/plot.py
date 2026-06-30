import numpy as np
import matplotlib.pyplot as plt

def plot_pareto_front_on_asi(front: list, title: str = "Pareto Front on ASI Regions"):
    """
    Plots the ASI sustainability regions from Figure 1 / Table 1
    and overlays the Pareto front design points on top.
    """
    # 1. Generate the sustainability region boundaries
    # We create a dense range of speedups to plot smooth boundary curves
    S_range = np.linspace(0.2, 2.5, 500)
    y_max_cond = np.maximum(1, 1 / S_range)  # Upper boundary: max(1, 1/S)
    y_min_cond = np.minimum(1, 1 / S_range)  # Lower boundary: min(1, 1/S)

    fig, ax = plt.subplots(figsize=(9, 7))
    
    # Set plot boundaries (adjust y_upper_limit if your points have higher ASI values)
    y_upper_limit = 4.0
    ax.set_xlim(0.2, 2.5)
    ax.set_ylim(0, y_upper_limit)

    # 2. Fill the regions (Table 1 logic)
    # Region I: Strongly Sustainable (Green) -> Above max(1, 1/S)
    ax.fill_between(S_range, y_max_cond, y_upper_limit, color='#d4edda', label='Region I: Strongly Sustainable')
    # Region II: Unsustainable (Red) -> Below min(1, 1/S)
    ax.fill_between(S_range, 0, y_min_cond, color='#f8d7da', label='Region II: Unsustainable')
    # Region IV: Sustainable under FT (Orange) -> Between boundaries where S < 1
    ax.fill_between(S_range, y_min_cond, y_max_cond, where=(S_range < 1), color='#ffeeba', label='Region IV: Weakly Sustainable (FT)')
    # Region III: Sustainable under FW (Yellow) -> Between boundaries where S >= 1
    ax.fill_between(S_range, y_min_cond, y_max_cond, where=(S_range >= 1), color='#fff3cd', label='Region III: Weakly Sustainable (FW)')

    # Plot the boundary line tracks
    ax.plot(S_range, y_max_cond, color='blue', linewidth=1.5, linestyle='--', alpha=0.7)
    ax.plot(S_range, y_min_cond, color='green', linewidth=1.5, linestyle='--', alpha=0.7)

    # Mark the baseline Reference configuration at (1,1)
    ax.scatter(1, 1, color='black', s=100, zorder=5)
    ax.text(1.05, 1.05, 'Ref (1,1)', fontsize=10, fontweight='bold', zorder=5)

    # 3. Extract and plot your Pareto front data
    # NOTE: Assumes your points 'p' have a 'p.asi' attribute calculated alongside 'p.speedup'
    speedups = [p.speedup for p in front]
    asi_values = [p.asi for p in front]

    # zorder=6 ensures your custom points sit completely on top of the background colors
    ax.scatter(speedups, asi_values, color='purple', edgecolors='black', s=80, zorder=6, label='Pareto Front Designs')
    
    # (Optional) Draw a line connecting the Pareto front points if they are ordered
    ax.plot(speedups, asi_values, color='purple', linestyle='-', linewidth=2, alpha=0.8, zorder=6)

    # Labeling and adjustments
    ax.set_title(title, fontsize=14, pad=15)
    ax.set_xlabel("Speedup (S)", fontsize=12)
    ax.set_ylabel("ASI", fontsize=12)
    ax.grid(True, linestyle=':', alpha=0.6)

    # Clean up duplicate handles in the legend caused by the fill_between segments
    handles, labels = ax.get_legend_handles_labels()
    by_label = dict(zip(labels, handles))
    ax.legend(by_label.values(), by_label.keys(), loc='upper right', fontsize=9)

    plt.tight_layout()
    plt.show()