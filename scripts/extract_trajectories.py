"""Extract trajectory timeseries from live Aviary MCP sessions and generate analysis figures.

Connects to the running MCP server via FastMCP Client to check session liveness
and extract timeseries data. Falls back to hardcoded results for figure generation.
"""

import asyncio
import json
import os
import sys
import math
import numpy as np

# ---------------------------------------------------------------------------
# Session IDs from the Linux batch runs
# ---------------------------------------------------------------------------
SESSION_IDS = {
    "aviary_sequential_iterative_feedback": "b4b0eb4b-c4ee-41e2-92bf-ad68a4dc90a1",
    "aviary_sequential_staged_pipeline": "cd850185-c6d4-44f7-a904-708649b6405e",
    "aviary_orchestrated_iterative_feedback": "3be8198b-4547-42c0-8939-34c932f930bf",
    "aviary_orchestrated_staged_pipeline": "53b090c8-df17-4cf6-8c4c-e4a4a70f856b",
    "aviary_orchestrated_graph_routed": "ec8fb917-c4fc-4c27-9cb5-7301aee37e1a",
    "aviary_networked_iterative_feedback": "90f68207-a741-4534-9f67-9e786f164b22",
    "aviary_networked_staged_pipeline": "65e137ab-0963-4e5f-b6e3-7f5bb6ca0bd8",
    "aviary_networked_graph_routed": "8d44d0d4-4171-416d-93a9-67a7079b52a6",
}

REFERENCE = {
    "fuel_burned_kg": 7000.65,
    "params": {"AREA": 160.0, "ASPECT_RATIO": 11.22, "SCALE_FACTOR": 0.8},
}

# Hardcoded fuel values from Linux batch results (fallback)
FUEL_HARDCODED = {
    "sequential_iterative_feedback": 6631.7,
    "sequential_staged_pipeline": 7000.6,
    "orchestrated_iterative_feedback": None,  # eval_skipped
    "orchestrated_staged_pipeline": 6849.6,
    "orchestrated_graph_routed": 7000.65,
    "networked_iterative_feedback": 6517.2,
    "networked_staged_pipeline": 6849.58,
    "networked_graph_routed": 6863.94,
}

# Hardcoded final parameter values from Linux traces
# Rows: combination name -> {AREA, ASPECT_RATIO, SPAN, SCALE_FACTOR}
PARAMS_HARDCODED = {
    "sequential_iterative_feedback": {"AREA": 140.0, "ASPECT_RATIO": 12.5, "SPAN": 41.8, "SCALE_FACTOR": 0.85},
    "sequential_staged_pipeline": {"AREA": 160.0, "ASPECT_RATIO": 11.2, "SPAN": 37.4, "SCALE_FACTOR": 0.8},
    "orchestrated_iterative_feedback": {"AREA": 0.0, "ASPECT_RATIO": 0.0, "SPAN": 0.0, "SCALE_FACTOR": 0.0},  # TODO: no data (eval_skipped)
    "orchestrated_staged_pipeline": {"AREA": 155.0, "ASPECT_RATIO": 11.5, "SPAN": 42.2, "SCALE_FACTOR": 0.82},
    "orchestrated_graph_routed": {"AREA": 160.0, "ASPECT_RATIO": 11.2, "SPAN": 37.4, "SCALE_FACTOR": 0.8},
    "networked_iterative_feedback": {"AREA": 135.0, "ASPECT_RATIO": 13.0, "SPAN": 41.9, "SCALE_FACTOR": 0.88},
    "networked_staged_pipeline": {"AREA": 155.0, "ASPECT_RATIO": 11.5, "SPAN": 42.2, "SCALE_FACTOR": 0.82},
    "networked_graph_routed": {"AREA": 152.0, "ASPECT_RATIO": 11.8, "SPAN": 42.3, "SCALE_FACTOR": 0.83},
}

# ---------------------------------------------------------------------------
# Color and style maps
# ---------------------------------------------------------------------------
COLOR_MAP = {
    "sequential_iterative_feedback": "#2166ac",
    "sequential_staged_pipeline": "#4393c3",
    "sequential_graph_routed": "#92c5de",
    "orchestrated_iterative_feedback": "#d6604d",
    "orchestrated_staged_pipeline": "#f4a582",
    "orchestrated_graph_routed": "#ca0020",
    "networked_iterative_feedback": "#1a9850",
    "networked_staged_pipeline": "#66bd63",
    "networked_graph_routed": "#a6d96a",
}

OS_COLORS = {
    "sequential": "#2166ac",
    "orchestrated": "#d6604d",
    "networked": "#1a9850",
}

LINESTYLE_MAP = {
    "iterative_feedback": "solid",
    "staged_pipeline": "dashed",
    "graph_routed": "dashdot",
}

PHASES = ["climb", "cruise", "descent"]
TIMESERIES_VARS = [
    ("time", "s"),
    ("altitude", "ft"),
    ("mach", None),
    ("mass", "kg"),
    ("throttle", None),
    ("drag", "N"),
]

SERVER_URL = "http://localhost:8600/mcp"


def parse_os_handler(name):
    """Parse OS type and handler from combination name like 'aviary_sequential_iterative_feedback'."""
    # Strip 'aviary_' prefix if present
    clean = name.replace("aviary_", "")
    parts = clean.split("_")
    os_type = parts[0]
    handler = "_".join(parts[1:])
    return os_type, handler


def get_color(name):
    clean = name.replace("aviary_", "")
    return COLOR_MAP.get(clean, "#333333")


def get_linestyle(name):
    _, handler = parse_os_handler(name)
    return LINESTYLE_MAP.get(handler, "solid")


# ---------------------------------------------------------------------------
# Step 1: Check sessions via MCP
# ---------------------------------------------------------------------------
async def check_sessions():
    """Check which sessions are still live on the MCP server."""
    from fastmcp import Client

    found = {}
    expired = []

    async with Client(SERVER_URL) as client:
        for combo_name, sid in SESSION_IDS.items():
            try:
                resp = await client.call_tool("get_results", {"session_id": sid})
                data = json.loads(resp.content[0].text) if hasattr(resp, "content") else json.loads(resp[0].text)
                if data.get("success"):
                    print(f"FOUND: {combo_name}")
                    found[combo_name] = sid
                else:
                    print(f"EXPIRED: {combo_name}")
                    expired.append(combo_name)
            except Exception as e:
                print(f"EXPIRED: {combo_name} ({e})")
                expired.append(combo_name)

    return found, expired


# ---------------------------------------------------------------------------
# Step 2: Extract timeseries from live sessions
# ---------------------------------------------------------------------------
async def extract_trajectory(combo_name, session_id):
    """Extract timeseries data from a live session via the in-memory prob object.

    Since we can't reach prob directly from another process, we'd need a
    get_trajectory MCP tool. For now, return None and fall back to hardcoded data.
    """
    # NOTE: The MCP server doesn't expose a get_trajectory tool yet.
    # Direct prob access requires being in the same process as the server.
    # This function is a placeholder for when the tool is added.
    return None


# ---------------------------------------------------------------------------
# Step 3: Generate figures
# ---------------------------------------------------------------------------
def generate_figures(trajectories):
    """Generate analysis figures from trajectory data and hardcoded results."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import seaborn as sns

    sns.set_theme(style="whitegrid", context="paper")

    # Try to use serif font
    for font in ["Times New Roman", "DejaVu Serif", "serif"]:
        try:
            plt.rcParams["font.family"] = font
            break
        except Exception:
            continue

    plt.rcParams.update({
        "axes.labelsize": 11,
        "axes.titlesize": 13,
        "xtick.labelsize": 9,
        "ytick.labelsize": 9,
    })

    os.makedirs("figures", exist_ok=True)
    generated = []

    # --- Figure 1: Trajectory panels (only if we have trajectory data) ---
    traj_files = []
    if os.path.isdir("trajectories"):
        traj_files = [f for f in os.listdir("trajectories") if f.endswith(".json") and f != "reference.json"]

    if len(traj_files) >= 2:
        fig, axes = plt.subplots(2, 2, figsize=(14, 10), sharex=True)
        panels = [
            (axes[0, 0], "altitude_ft", "Altitude (ft)"),
            (axes[0, 1], "mach", "Mach"),
            (axes[1, 0], "mass_kg", "Mass (kg)"),
            (axes[1, 1], "throttle", "Throttle"),
        ]

        # Load reference trajectory if available
        ref_path = "trajectories/reference.json"
        if os.path.exists(ref_path):
            with open(ref_path) as f:
                ref_traj = json.load(f)
            for ax, var_key, label in panels:
                if var_key in ref_traj and ref_traj.get("time_s"):
                    ax.plot(ref_traj["time_s"], ref_traj[var_key],
                            color="black", linewidth=2.5, linestyle="solid",
                            label=f"SLSQP reference ({REFERENCE['fuel_burned_kg']:.0f} kg)", zorder=10)

        for traj_file in sorted(traj_files):
            combo_clean = traj_file.replace(".json", "")
            combo_name = f"aviary_{combo_clean}"
            filepath = os.path.join("trajectories", traj_file)
            with open(filepath) as f:
                traj = json.load(f)

            fuel = traj.get("fuel_burned_kg", 0)
            color = get_color(combo_name)
            ls = get_linestyle(combo_name)

            for ax, var_key, label in panels:
                if var_key in traj and traj.get("time_s"):
                    ax.plot(traj["time_s"], traj[var_key],
                            color=color, linestyle=ls, linewidth=1.2,
                            label=f"{combo_clean} ({fuel:.0f} kg)")

                    # Phase boundary lines
                    if "phase_labels" in traj:
                        labels = traj["phase_labels"]
                        times = traj["time_s"]
                        for i in range(1, len(labels)):
                            if labels[i] != labels[i - 1]:
                                ax.axvline(x=times[i], color="grey", linestyle="dashed",
                                           linewidth=0.5, alpha=0.5)

        for ax, var_key, label in panels:
            ax.set_ylabel(label)
        axes[1, 0].set_xlabel("Time (s)")
        axes[1, 1].set_xlabel("Time (s)")

        # Legend outside right
        handles, labels_leg = axes[0, 0].get_legend_handles_labels()
        if handles:
            fig.legend(handles, labels_leg, loc="center right",
                       bbox_to_anchor=(1.22, 0.5), fontsize=8)

        fig.suptitle("Trajectory Comparison Across OS Architectures", fontsize=14, y=0.98)
        fig.tight_layout(rect=[0, 0, 0.82, 0.95])

        for ext in ["pdf", "png"]:
            path = f"figures/trajectory_comparison.{ext}"
            fig.savefig(path, dpi=300, bbox_inches="tight")
            generated.append(path)
        plt.close(fig)
        print("Figure 1 (trajectory panels) generated.")
    else:
        print(f"Figure 1 SKIPPED — need >= 2 trajectory files, found {len(traj_files)}.")

    # --- Figure 2: Optimality gap bar chart ---
    fuel_data = {}
    # Try to read from trajectory JSONs first
    if os.path.isdir("trajectories"):
        for traj_file in os.listdir("trajectories"):
            if traj_file.endswith(".json") and traj_file != "reference.json":
                combo_clean = traj_file.replace(".json", "")
                with open(os.path.join("trajectories", traj_file)) as f:
                    traj = json.load(f)
                if "fuel_burned_kg" in traj and traj["fuel_burned_kg"] is not None:
                    fuel_data[combo_clean] = traj["fuel_burned_kg"]

    # Fall back to hardcoded values for anything missing
    for combo_clean, fuel in FUEL_HARDCODED.items():
        if combo_clean not in fuel_data and fuel is not None:
            fuel_data[combo_clean] = fuel

    if fuel_data:
        ref_fuel = REFERENCE["fuel_burned_kg"]
        items = [(name, fuel, (fuel - ref_fuel) / ref_fuel * 100)
                 for name, fuel in fuel_data.items()]
        items.sort(key=lambda x: x[2])

        names = [x[0] for x in items]
        gaps = [x[2] for x in items]
        fuels = [x[1] for x in items]
        colors = []
        for n in names:
            os_type = n.split("_")[0]
            colors.append(OS_COLORS.get(os_type, "#333333"))

        fig, ax = plt.subplots(figsize=(10, 6))
        bars = ax.barh(range(len(names)), gaps, color=colors, edgecolor="white", height=0.6)

        # Annotate with fuel values
        for i, (bar, fuel_val) in enumerate(zip(bars, fuels)):
            w = bar.get_width()
            offset = 0.3 if w >= 0 else -0.3
            ha = "left" if w >= 0 else "right"
            ax.text(w + offset, i, f"{fuel_val:.0f} kg", va="center", ha=ha, fontsize=8)

        ax.set_yticks(range(len(names)))
        ax.set_yticklabels([n.replace("_", " ").title() for n in names], fontsize=9)
        ax.axvline(x=0, color="black", linewidth=1.5, linestyle="-")
        ax.text(0.2, len(names) - 0.3, "SLSQP reference", fontsize=8,
                color="black", fontstyle="italic")
        ax.set_xlabel("Optimality Gap (%)")
        ax.set_title("Fuel Burn Optimality Gap vs SLSQP Reference (7,001 kg)")

        # OS type legend
        from matplotlib.patches import Patch
        legend_elements = [Patch(facecolor=c, label=os_type.title())
                           for os_type, c in OS_COLORS.items()]
        ax.legend(handles=legend_elements, loc="lower right", fontsize=9)

        fig.tight_layout()
        for ext in ["pdf", "png"]:
            path = f"figures/optimality_gap.{ext}"
            fig.savefig(path, dpi=300, bbox_inches="tight")
            generated.append(path)
        plt.close(fig)
        print("Figure 2 (optimality gap) generated.")

    # --- Figure 3: Parameter heatmap ---
    # Filter out combinations with no data
    param_data = {k: v for k, v in PARAMS_HARDCODED.items()
                  if v.get("AREA", 0) > 0}

    if param_data:
        columns = ["AREA", "ASPECT_RATIO", "SPAN", "SCALE_FACTOR"]
        col_labels = ["Area (m²)", "Aspect Ratio", "Span (m)", "Scale Factor"]
        row_names = list(param_data.keys())

        # Add reference row at top
        ref_params = REFERENCE["params"].copy()
        ref_params["SPAN"] = math.sqrt(ref_params["ASPECT_RATIO"] * ref_params["AREA"])
        row_names = ["SLSQP_reference"] + row_names

        # Build raw data matrix
        raw = np.zeros((len(row_names), len(columns)))
        raw[0] = [ref_params.get(c, 0) for c in columns]
        for i, name in enumerate(row_names[1:], start=1):
            raw[i] = [param_data[name].get(c, 0) for c in columns]

        # Normalize each column to [0, 1] for colormap
        col_mins = raw.min(axis=0)
        col_maxs = raw.max(axis=0)
        col_ranges = col_maxs - col_mins
        col_ranges[col_ranges == 0] = 1  # avoid division by zero
        normalized = (raw - col_mins) / col_ranges

        # Annotation strings (1 decimal place)
        annot = np.array([[f"{v:.1f}" for v in row] for row in raw])

        fig, ax = plt.subplots(figsize=(10, 8))
        sns.heatmap(normalized, ax=ax, cmap="RdYlGn_r", annot=annot, fmt="",
                    xticklabels=col_labels,
                    yticklabels=[n.replace("_", " ").title() for n in row_names],
                    linewidths=0.5, linecolor="white",
                    cbar_kws={"label": "Normalized value (column-wise)"})

        # Black border on reference row
        ax.add_patch(plt.Rectangle((0, 0), len(columns), 1,
                                   fill=False, edgecolor="black", linewidth=2.5))

        ax.set_title("Final Design Parameters by OS Architecture")
        fig.tight_layout()

        for ext in ["pdf", "png"]:
            path = f"figures/parameter_heatmap.{ext}"
            fig.savefig(path, dpi=300, bbox_inches="tight")
            generated.append(path)
        plt.close(fig)
        print("Figure 3 (parameter heatmap) generated.")

    return generated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
async def main():
    print("=" * 60)
    print("Aviary MCP — Trajectory Extraction & Analysis")
    print("=" * 60)

    # Step 1: Check sessions
    print("\n--- Step 1: Checking sessions on MCP server ---")
    try:
        found, expired = await check_sessions()
    except Exception as e:
        print(f"Could not connect to MCP server: {e}")
        print("All sessions will be treated as expired.")
        found = {}
        expired = list(SESSION_IDS.keys())

    print(f"\nSessions found: {len(found)}, expired: {len(expired)}")

    # Step 2: Extract trajectories from live sessions
    os.makedirs("trajectories", exist_ok=True)
    extracted = []

    for combo_name, sid in found.items():
        print(f"\nExtracting trajectory for {combo_name}...")
        traj = await extract_trajectory(combo_name, sid)
        if traj is not None:
            clean = combo_name.replace("aviary_", "")
            path = f"trajectories/{clean}.json"
            with open(path, "w") as f:
                json.dump(traj, f, indent=2)
            extracted.append(path)
            print(f"  Saved to {path}")
        else:
            print(f"  No trajectory data available (get_trajectory tool not yet implemented)")

    # Step 3: Generate figures
    print("\n--- Step 3: Generating figures ---")
    generated = generate_figures(extracted)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    print(f"Sessions found: {len(found)} / {len(SESSION_IDS)}")
    print(f"Sessions expired: {len(expired)} / {len(SESSION_IDS)}")
    print(f"Trajectories extracted: {len(extracted)}")
    print(f"Figures generated: {len(generated)}")
    for path in generated:
        print(f"  {path}")
    if not generated:
        print("  (none)")


if __name__ == "__main__":
    asyncio.run(main())
