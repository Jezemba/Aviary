# Aviary MCP Server

MCP server that exposes NASA's [Aviary](https://github.com/OpenMDAO/Aviary) aircraft design optimizer as 9 tools over streamable-HTTP. An AI agent (or any MCP client) can explore the design space, modify aircraft parameters, run gradient-based trajectory optimization, and retrieve results — all through standard MCP calls.

## Related Repositories

- **[MAS-Aviary](https://github.com/Jezemba/MAS-Aviary)** — Multi-agent LLM framework that uses this MCP server as its backend for aircraft design optimization. MAS-Aviary orchestrates specialized agents (exploration, modification, simulation, analysis) that collaborate to find fuel-optimal aircraft configurations by calling the tools exposed here.

## Quick start

### 1. Create the conda environment

Aviary 0.9.10 requires specific versions of OpenMDAO and Dymos. Newer versions introduce a unit-compatibility bug that breaks trajectory optimization. The pinned versions below are the only combination known to work.

```bash
conda create -n aviary python=3.11 -y
conda activate aviary
pip install -r requirements.txt
```

`requirements.txt` pins:

| Package | Version | Why |
|---------|---------|-----|
| aviary | 0.9.10 | NASA aircraft design/optimization |
| openmdao | 3.36.0 | MDO framework — 3.41+ breaks aviary unit handling |
| dymos | 1.13.1 | Trajectory optimization — 1.15+ breaks aviary unit handling |
| fastmcp | >=3.0.0 | MCP server framework |
| pytest | latest | Test runner |

### 2. Start the server

```bash
conda activate aviary
python aviary_mcp_server.py
```

The server listens on **`http://localhost:8600/mcp`** (streamable-HTTP transport).

### 3. Connect a client

Point any MCP-compatible client at `http://localhost:8600/mcp`. The server exposes 9 tools that follow a session-based lifecycle.

> **MAS-Aviary integration:** The [MAS-Aviary](https://github.com/Jezemba/MAS-Aviary) multi-agent framework connects to this endpoint automatically. See the [MAS-Aviary quickstart guide](https://github.com/Jezemba/MAS-Aviary#quick-start) for instructions on running the full multi-agent optimization pipeline.

```
get_design_space  →  create_session  →  set_aircraft_parameters  →  configure_mission
                                              ↓
                     check_constraints  ←  get_results / get_trajectory  ←  run_simulation
                                              ↑
                                     validate_parameters (optional pre-check)
```

### 4. Verify the installation

```bash
# Unit tests (no server required)
python -m pytest tests/test_aviary_mcp_tools.py -v -m "not slow"

# End-to-end connection test (server must be running)
python test_aviary_mcp_connection.py
```

## Tools

| # | Tool | Description |
|---|------|-------------|
| 1 | `get_design_space` | List 10 modifiable aircraft parameters with defaults, units, and bounds |
| 2 | `create_session` | Start a new session (loads 737/A320-class baseline aircraft) |
| 3 | `set_aircraft_parameters` | Modify wing, fuselage, or engine parameters with validation |
| 4 | `configure_mission` | Set range, passengers, cruise Mach, altitude, optimizer iterations (defaults: 1500 nmi, 162 pax, Mach 0.785, FL350) |
| 5 | `validate_parameters` | Static checks + quick model evaluation (~6-9s) without running the optimizer |
| 6 | `run_simulation` | Run SLSQP trajectory optimization (default 200 iterations, ~40-60s) |
| 7 | `get_results` | Read fuel burn, GTOW, wing mass, reserve fuel, zero-fuel weight |
| 8 | `get_trajectory` | Extract timeseries arrays: altitude, Mach, mass, throttle, drag, distance vs time |
| 9 | `check_constraints` | Evaluate pass/fail against user-defined limits (<=, >=, ==) |

## Design parameters

| Parameter | Default | Units | Range |
|-----------|---------|-------|-------|
| Aircraft.Wing.ASPECT_RATIO | 11.22 | — | 7–14 |
| Aircraft.Wing.AREA | 124.6 | m² | 100–160 |
| Aircraft.Wing.SPAN | 37.35 | m | 28–48 |
| Aircraft.Wing.SWEEP | 25.0 | deg | 15–40 |
| Aircraft.Wing.TAPER_RATIO | 0.278 | — | 0.15–0.45 |
| Aircraft.Fuselage.LENGTH | 37.79 | m | 28–50 |
| Aircraft.Fuselage.MAX_HEIGHT | 4.06 | m | 3–5.5 |
| Aircraft.Fuselage.MAX_WIDTH | 3.76 | m | 3–5.5 |
| Aircraft.Engine.SCALED_SLS_THRUST | 28928 | lbf | 20k–45k (read-only, derived from SCALE_FACTOR) |
| Aircraft.Engine.SCALE_FACTOR | 1.0 | — | 0.8–1.5 |

Wing area, span, and aspect ratio are coupled: `AR = span² / area`. Change at most one per call.

## Mission defaults

| Parameter | Default |
|-----------|---------|
| Range | 1,500 nmi |
| Passengers | 162 |
| Cruise Mach | 0.785 |
| Cruise altitude | FL350 (35,000 ft) |
| Optimizer iterations | 200 (SLSQP) |

## Project structure

```
aviary_mcp_server.py        MCP server — 9 tool definitions
aviary_runner.py             Aviary Level 2 API wrapper
session_manager.py           Session lifecycle, 30-min idle timeout
design_space.py              Parameter metadata and validation
requirements.txt             Pinned dependencies
start_aviary_server.py       Convenience start script
tests/                       pytest suite (25 unit + 31 integration)
test_aviary_mcp_connection.py  End-to-end smoke test
run_reference_benchmark.py   Standalone benchmark (~7,001 kg fuel baseline)
CHANGELOG.md                 Version history
RESULTS.md                   Batch evaluation of 8 agent configurations
docs/index.html              Project landing page
figures/                     Generated plots
```

## Troubleshooting

**`unitless` vs `1` unit error during optimization:**
You have a newer version of openmdao or dymos installed. Downgrade to the pinned versions:
```bash
pip install openmdao==3.36.0 dymos==1.13.1
```

**`build_model()` not found:**
This method does not exist in Aviary 0.9.10. The server uses the correct call sequence internally — if you see this error, your aviary version is wrong.

**Simulation hangs or times out:**
`run_simulation` accepts a `timeout_seconds` parameter (default 300). Complex configurations may need more time. Check that the optimizer iteration count (`configure_mission` → `optimizer_max_iter`) is reasonable.
