"""Generate a 2x2 trajectory comparison figure from saved trajectory JSONs."""

import json
import numpy as np
import matplotlib.pyplot as plt
import seaborn as sns

# --- Data sources ---
RUNS = [
    ("Baseline", "logs/baseline_trajectory.json", None),
    ("Seq / Iterative Feedback", "logs/batch_results/1772821557/trajectory_sequential_iterative_feedback.json", None),
    ("Seq / Staged Pipeline", "logs/batch_results/1772823230/trajectory_sequential_staged_pipeline.json", None),
    ("Orch / Iterative Feedback", "logs/batch_results/1772823744/trajectory_orchestrated_iterative_feedback.json", None),
    ("Orch / Staged Pipeline", "logs/batch_results/1772824392/trajectory_orchestrated_staged_pipeline.json", None),
    ("Orch / Graph Routed", "logs/batch_results/1772824881/trajectory_orchestrated_graph_routed.json", None),
    ("Net / Iterative Feedback", "logs/batch_results/1772830187/trajectory_networked_iterative_feedback.json", None),
    ("Net / Staged Pipeline", "logs/batch_results/1772833044/trajectory_networked_staged_pipeline.json", None),
    ("Net / Graph Routed", "logs/trajectory_networked_graph_routed.json", None),
]

# --- Style config ---
sns.set_theme(style="whitegrid", font_scale=0.95)
palette = sns.color_palette("husl", n_colors=9)

# Lines that overlay the baseline get dashes to stay visible
LINE_STYLES = {
    "Baseline":                   {"color": palette[0], "lw": 2.5, "ls": "-",  "zorder": 10},
    "Seq / Iterative Feedback":   {"color": palette[1], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Seq / Staged Pipeline":      {"color": palette[2], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Orch / Iterative Feedback":  {"color": palette[3], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Orch / Staged Pipeline":     {"color": palette[4], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Orch / Graph Routed":        {"color": palette[5], "lw": 1.8, "ls": "--", "zorder": 8},
    "Net / Iterative Feedback":   {"color": palette[6], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Net / Staged Pipeline":      {"color": palette[7], "lw": 1.4, "ls": "-",  "zorder": 5},
    "Net / Graph Routed":         {"color": palette[8], "lw": 1.8, "ls": ":",  "zorder": 8},
}

# --- Load data ---
trajectories = {}
fuel_values = {}
for label, path, _ in RUNS:
    with open(path) as f:
        d = json.load(f)
    traj = d["trajectory"]
    trajectories[label] = traj
    fuel_values[label] = d.get("results", {}).get("fuel_burned_kg")

# --- Build figure ---
fig, axes = plt.subplots(2, 2, figsize=(14, 9), sharex=True)
fig.suptitle(
    "Trajectory Comparison — 8 Agent Configurations vs Baseline",
    fontsize=14, fontweight="bold", y=0.97,
)

panels = [
    (axes[0, 0], "altitude_ft", "Altitude (ft)", None),
    (axes[0, 1], "mach",        "Mach Number",   None),
    (axes[1, 0], "mass_kg",     "Aircraft Mass (kg)", None),
    (axes[1, 1], "throttle",    "Throttle",      None),
]

for ax, var_key, ylabel, ylim in panels:
    for label, path, _ in RUNS:
        traj = trajectories[label]
        time_min = np.array(traj["time_s"]) / 60.0
        values = np.array(traj[var_key], dtype=float)
        style = LINE_STYLES[label]

        fuel = fuel_values.get(label)
        legend_label = label
        if fuel is not None and label != "Baseline":
            delta = (fuel - fuel_values["Baseline"]) / fuel_values["Baseline"] * 100
            sign = "+" if delta >= 0 else ""
            legend_label = f"{label} ({sign}{delta:.1f}%)"
        elif label == "Baseline":
            legend_label = f"Baseline ({fuel:.0f} kg)"

        ax.plot(
            time_min, values,
            color=style["color"],
            linewidth=style["lw"],
            linestyle=style["ls"],
            zorder=style["zorder"],
            label=legend_label,
        )

    ax.set_ylabel(ylabel, fontsize=11)
    if ylim:
        ax.set_ylim(ylim)
    ax.tick_params(labelsize=9)

# Shared x-axis label
for ax in axes[1]:
    ax.set_xlabel("Time (min)", fontsize=11)

# Single legend below all panels
handles, labels = axes[0, 0].get_legend_handles_labels()
fig.legend(
    handles, labels,
    loc="lower center",
    ncol=3,
    fontsize=8.5,
    frameon=True,
    fancybox=True,
    shadow=False,
    bbox_to_anchor=(0.5, -0.02),
)

fig.tight_layout(rect=[0, 0.08, 1, 0.95])

out_path = "figures/trajectory_comparison.png"
import os
os.makedirs("figures", exist_ok=True)
fig.savefig(out_path, dpi=600, bbox_inches="tight", facecolor="white")
print(f"Saved: {out_path}")
plt.close(fig)
