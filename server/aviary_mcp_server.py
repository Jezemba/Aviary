"""Aviary MCP Server — exposes NASA Aviary aircraft design optimization over streamable-HTTP.

9 tools: get_design_space, create_session, set_aircraft_parameters, configure_mission,
validate_parameters, run_simulation, get_results, get_trajectory, check_constraints.
"""

import logging
from fastmcp import FastMCP

from design_space import (
    DESIGN_PARAMETERS,
    VALID_PARAMETER_NAMES,
    VARIABLE_NAME_MAP,
    get_design_space as _get_design_space,
)
from session_manager import SessionManager
from aviary_runner import (
    create_aviary_problem,
    run_problem,
    extract_results,
    extract_trajectory as _extract_trajectory,
    get_current_param_value,
    validate_parameters as _validate_parameters,
    DEFAULT_MISSION,
    MAX_PASSENGERS,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Initialize FastMCP server
mcp = FastMCP("Aviary MCP Server")

# Initialize session manager
session_mgr = SessionManager()
session_mgr.start_cleanup_thread()

# --- Helpers ---

def _error(error_code, message):
    """Return a standard error response."""
    return {"success": False, "error": message, "error_code": error_code}


def _get_session_or_error(session_id):
    """Look up a session by ID, return (session, None) or (None, error_dict)."""
    session = session_mgr.get_session(session_id)
    if session is None:
        return None, _error("INVALID_SESSION", f"Session '{session_id}' not found or expired.")
    return session, None


def _get_param_info(name):
    """Get the design parameter metadata by name."""
    for p in DESIGN_PARAMETERS:
        if p["name"] == name:
            return p
    return None


# --- Tool 1: get_design_space ---

@mcp.tool()
def get_design_space(category: str = "all") -> dict:
    """Returns the full list of modifiable aircraft design parameters with their
    current default values, units, and recommended bounds.

    Read-only and stateless — no session_id required. Call this first to understand
    what can be changed before creating a session.

    Args:
        category: Filter by category. One of: "wing", "fuselage", "engine", "all" (default: "all")
    """
    if category not in ("all", "wing", "fuselage", "engine"):
        category = "all"
    return _get_design_space(category)


# --- Tool 2: create_session ---

@mcp.tool()
def create_session() -> dict:
    """Creates a new Aviary session. Loads the default aircraft (737/A320-class
    single-aisle transport) and returns a session_id.

    The session persists server-side until explicitly closed or the idle timeout
    elapses (30 minutes). This is always the first call in any design workflow.
    """
    try:
        session = session_mgr.create_session()
        return {
            "success": True,
            "session_id": session.session_id,
            "aircraft": "Single-Aisle Transport (737/A320 class) — aircraft_for_bench_FwFm.csv",
            "created_at": session.created_at,
        }
    except Exception as e:
        logger.exception("Failed to create session")
        return _error("SERVER_INIT_ERROR", f"Failed to create session: {e}")


# --- Tool 3: set_aircraft_parameters ---

@mcp.tool()
def set_aircraft_parameters(session_id: str, parameters: dict) -> dict:
    """Modifies one or more aircraft design parameters in the session.

    Does not run a simulation — only updates session state. Parameter names must
    be valid Aviary variable strings as returned by get_design_space. Values outside
    the recommended range are accepted with a warning.

    Args:
        session_id: Session handle from create_session.
        parameters: Key-value dict mapping Aviary variable name to new value (float).
            Example: {"Aircraft.Wing.ASPECT_RATIO": 12.5, "Aircraft.Engine.SCALED_SLS_THRUST": 130000}
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    applied = []
    warnings = []

    for name, value in parameters.items():
        # Validate parameter name
        if name not in VALID_PARAMETER_NAMES:
            return _error("UNKNOWN_PARAMETER", f"Unknown parameter: '{name}'. Use get_design_space to see valid names.")

        # Validate value type
        try:
            value = float(value)
        except (TypeError, ValueError):
            return _error("TYPE_ERROR", f"Value for '{name}' could not be coerced to float: {value}")

        # Get parameter metadata for bounds checking
        param_info = _get_param_info(name)
        old_value = session.aircraft_params.get(name, param_info["current_default"])

        # Check recommended range
        if value < param_info["min"] or value > param_info["max"]:
            warnings.append(
                f"{name} = {value} is outside recommended range "
                f"[{param_info['min']}, {param_info['max']}]"
            )

        # Apply
        session.aircraft_params[name] = value
        applied.append({
            "name": name,
            "old_value": old_value,
            "new_value": value,
            "units": param_info["units"],
        })

    return {
        "success": True,
        "session_id": session_id,
        "applied": applied,
        "warnings": warnings,
    }


# --- Tool 4: configure_mission ---

@mcp.tool()
def configure_mission(
    session_id: str,
    range_nmi: float = None,
    num_passengers: int = None,
    cruise_mach: float = None,
    cruise_altitude_ft: float = None,
    optimizer_max_iter: int = None,
) -> dict:
    """Defines the mission profile the aircraft will fly. Does not run a simulation.

    The mission is defined by range, number of passengers, cruise Mach number,
    and cruise altitude. Defaults are set for a typical medium-haul mission.

    Args:
        session_id: Session handle.
        range_nmi: Mission range in nautical miles. Default: 1500.
        num_passengers: Number of passengers. Default: 162. Set to 0 for ferry mission.
        cruise_mach: Target cruise Mach number. Default: 0.785. Bounds: 0.65–0.90.
        cruise_altitude_ft: Target cruise altitude in feet. Default: 35000. Bounds: 25000–43000.
        optimizer_max_iter: Maximum SLSQP iterations. Default: 200.
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    mc = session.mission_config

    if range_nmi is not None:
        mc["range_nmi"] = float(range_nmi)

    if num_passengers is not None:
        num_passengers = int(num_passengers)
        if num_passengers < 0:
            return _error("INVALID_PASSENGER_COUNT", "num_passengers must be >= 0.")
        if num_passengers > MAX_PASSENGERS:
            return _error("INVALID_PASSENGER_COUNT", f"num_passengers must be <= {MAX_PASSENGERS}.")
        mc["num_passengers"] = num_passengers

    if cruise_mach is not None:
        cruise_mach = float(cruise_mach)
        if cruise_mach < 0.65 or cruise_mach > 0.90:
            return _error("OUT_OF_BOUNDS", f"cruise_mach must be between 0.65 and 0.90, got {cruise_mach}.")
        mc["cruise_mach"] = cruise_mach

    if cruise_altitude_ft is not None:
        cruise_altitude_ft = float(cruise_altitude_ft)
        if cruise_altitude_ft < 25000 or cruise_altitude_ft > 43000:
            return _error("OUT_OF_BOUNDS", f"cruise_altitude_ft must be between 25000 and 43000, got {cruise_altitude_ft}.")
        mc["cruise_altitude_ft"] = cruise_altitude_ft

    if optimizer_max_iter is not None:
        mc["optimizer_max_iter"] = int(optimizer_max_iter)

    # Compute payload mass
    passenger_mass_kg = 90.7  # standard IATA
    payload_kg = round(mc["num_passengers"] * passenger_mass_kg, 1)

    return {
        "success": True,
        "session_id": session_id,
        "mission_summary": {
            "range_nmi": mc["range_nmi"],
            "num_passengers": mc["num_passengers"],
            "payload_kg": payload_kg,
            "cruise_mach": mc["cruise_mach"],
            "cruise_altitude_ft": mc["cruise_altitude_ft"],
            "optimizer_max_iter": mc["optimizer_max_iter"],
        },
    }


# --- Tool 5: validate_parameters ---

@mcp.tool()
def validate_parameters(session_id: str, timeout_seconds: int = 30) -> dict:
    """Validates the current aircraft parameters and mission config WITHOUT running
    a full optimization. Use this between set_aircraft_parameters / configure_mission
    and run_simulation to catch bad inputs early.

    Performs two layers of validation:
      1. Static checks (instant): NaN/inf detection, bounds violations, wing geometry
         coupling constraint (AR = span²/area).
      2. Quick model evaluation (~5-10s): builds the Aviary problem and runs a single
         function evaluation (no optimizer). Catches solver errors, singular matrices,
         unit failures, and NaN in computed outputs.

    Returns VALID or a list of specific violations with severity and suggested fixes.

    Args:
        session_id: Session handle from create_session.
        timeout_seconds: Max time for model evaluation. Default: 30.
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    try:
        logger.info("Session %s: validating parameters...", session_id)
        result = _validate_parameters(
            aircraft_params=session.aircraft_params,
            mission_config=session.mission_config,
            timeout_seconds=timeout_seconds,
        )
    except Exception as e:
        logger.exception("Session %s: validation failed", session_id)
        return _error("VALIDATION_ERROR", f"Parameter validation failed: {e}")

    # Build human-readable summary
    n_errors = sum(1 for v in result["static_checks"] if v["severity"] == "error")
    n_warnings = sum(1 for v in result["static_checks"] if v["severity"] == "warning")

    if result["valid"]:
        summary = "VALID — all static checks passed and model evaluation produced finite outputs."
        if n_warnings > 0:
            summary += f" ({n_warnings} warning(s) — see details.)"
    else:
        parts = ["INVALID"]
        if n_errors > 0:
            parts.append(f"{n_errors} error(s)")
        if n_warnings > 0:
            parts.append(f"{n_warnings} warning(s)")
        summary = " — ".join(parts) + ". Fix errors before running simulation."

    return {
        "success": True,
        "session_id": session_id,
        "valid": result["valid"],
        "summary": summary,
        "violations": result["static_checks"],
        "model_eval": result["model_eval"],
        "runtime_seconds": result["runtime_seconds"],
    }


# --- Tool 6: run_simulation ---
# (was Tool 5 before validate_parameters was added)

@mcp.tool()
def run_simulation(session_id: str, timeout_seconds: int = 300) -> dict:
    """Triggers the Aviary trajectory optimization for the current session.

    Runs Aviary's internal SLSQP optimizer to solve the coupled aircraft-trajectory
    problem. Blocks until completion or timeout.

    Args:
        session_id: Session handle.
        timeout_seconds: Wall-clock timeout in seconds. Default: 300.
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    try:
        # Create fresh AviaryProblem with current session state
        logger.info("Session %s: creating Aviary problem...", session_id)
        prob = create_aviary_problem(
            aircraft_params=session.aircraft_params,
            mission_config=session.mission_config,
        )
        session.prob = prob
    except Exception as e:
        logger.exception("Session %s: Aviary setup failed", session_id)
        return _error("AVIARY_SETUP_ERROR", f"Aviary problem setup failed: {e}")

    try:
        logger.info("Session %s: running simulation...", session_id)
        run_result = run_problem(prob, timeout_seconds=timeout_seconds)
    except Exception as e:
        logger.exception("Session %s: simulation failed", session_id)
        if "timeout" in str(e).lower():
            return _error("TIMEOUT", f"Simulation timed out after {timeout_seconds}s: {e}")
        return _error("AVIARY_SETUP_ERROR", f"Simulation failed: {e}")

    # Store results in session
    session.last_run_results = extract_results(prob, run_result["converged"])
    session.last_run_converged = run_result["converged"]
    session.last_run_exit_code = run_result["exit_code"]

    return {
        "success": True,
        "session_id": session_id,
        "converged": run_result["converged"],
        "exit_code": run_result["exit_code"],
        "runtime_seconds": run_result["runtime_seconds"],
        "iterations": run_result["iterations"],
        "summary": run_result["summary"],
    }


# --- Tool 7: get_results ---

@mcp.tool()
def get_results(session_id: str, variables: list = None) -> dict:
    """Reads performance output variables from the most recently completed simulation.

    Can be called after run_simulation, including after a timed-out run.

    Args:
        session_id: Session handle.
        variables: List of specific output variable names to return. If omitted, all standard outputs returned.
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    if session.last_run_results is None:
        return _error("NO_RESULTS", "run_simulation has not been called yet in this session.")

    results = session.last_run_results

    # Build design_parameters echo
    design_parameters = {
        "aircraft_params": dict(session.aircraft_params),
        "mission_config": dict(session.mission_config),
    }

    return {
        "success": True,
        "session_id": session_id,
        "converged": results.get("converged", False),
        "fuel_burned_kg": results.get("fuel_burned_kg"),
        "gtow_kg": results.get("gtow_kg"),
        "wing_mass_kg": results.get("wing_mass_kg"),
        "reserve_fuel_kg": results.get("reserve_fuel_kg"),
        "zero_fuel_weight_kg": results.get("zero_fuel_weight_kg"),
        "design_parameters": design_parameters,
    }


# --- Tool 8: get_trajectory ---

@mcp.tool()
def get_trajectory(session_id: str, variables: list = None) -> dict:
    """Returns trajectory timeseries data from the most recently completed simulation.

    Provides per-phase (climb/cruise/descent) timeseries: time, altitude, Mach,
    mass, throttle, drag, and distance. Each array is concatenated across all phases
    with a matching phase_labels array to identify which phase each point belongs to.

    Must be called after a successful run_simulation. The session must still be active
    (within the 30-minute idle timeout).

    Args:
        session_id: Session handle from create_session.
        variables: Optional list of specific variables to return. Available:
            time_s, altitude_ft, mach, mass_kg, throttle, drag_N, distance_nmi.
            If omitted, all variables are returned.
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    if session.prob is None:
        return _error("NO_RESULTS", "run_simulation has not been called yet in this session.")

    try:
        trajectory = _extract_trajectory(session.prob)
    except Exception as e:
        logger.exception("Session %s: trajectory extraction failed", session_id)
        return _error("TRAJECTORY_ERROR", f"Failed to extract trajectory data: {e}")

    # Filter to requested variables if specified
    all_vars = ["time_s", "altitude_ft", "mach", "mass_kg", "throttle", "drag_N", "distance_nmi"]
    if variables:
        invalid = [v for v in variables if v not in all_vars]
        if invalid:
            return _error("UNKNOWN_VARIABLE", f"Unknown trajectory variable(s): {invalid}. Available: {all_vars}")
        data = {v: trajectory[v] for v in variables}
    else:
        data = {v: trajectory[v] for v in all_vars}

    data["phase_labels"] = trajectory["phase_labels"]
    data["num_points"] = trajectory["num_points"]

    return {
        "success": True,
        "session_id": session_id,
        "trajectory": data,
    }


# --- Tool 9: check_constraints ---

@mcp.tool()
def check_constraints(session_id: str, constraints: list) -> dict:
    """Evaluates whether the latest simulation results satisfy user-defined constraints.

    Returns a structured pass/fail assessment with margin values.

    Args:
        session_id: Session handle.
        constraints: Array of constraint objects. Each has: variable, operator, value, units (optional), label.
            Supported variables: fuel_burned_kg, gtow_kg, wing_mass_kg, reserve_fuel_kg, zero_fuel_weight_kg.
            Operators: "<=", ">=", "==".
    """
    session, err = _get_session_or_error(session_id)
    if err:
        return err

    if session.last_run_results is None:
        return _error("NO_RESULTS", "run_simulation has not been called yet in this session.")

    SUPPORTED_VARIABLES = {
        "fuel_burned_kg", "gtow_kg", "wing_mass_kg",
        "reserve_fuel_kg", "zero_fuel_weight_kg",
    }
    VALID_OPERATORS = {"<=", ">=", "=="}

    results_data = session.last_run_results
    constraint_results = []
    all_satisfied = True

    for c in constraints:
        variable = c.get("variable", "")
        operator = c.get("operator", "")
        target_value = c.get("value")
        units = c.get("units", "")
        label = c.get("label", f"{variable} {operator} {target_value}")

        if variable not in SUPPORTED_VARIABLES:
            return _error("UNKNOWN_VARIABLE", f"Unknown constraint variable: '{variable}'. Supported: {sorted(SUPPORTED_VARIABLES)}")

        if operator not in VALID_OPERATORS:
            return _error("INVALID_OPERATOR", f"Invalid operator: '{operator}'. Must be one of: <=, >=, ==")

        actual_value = results_data.get(variable)
        if actual_value is None:
            constraint_results.append({
                "label": label,
                "variable": variable,
                "operator": operator,
                "target_value": target_value,
                "actual_value": None,
                "units": units,
                "satisfied": False,
                "margin": None,
            })
            all_satisfied = False
            continue

        target_value = float(target_value)

        # Evaluate constraint
        if operator == "<=":
            satisfied = actual_value <= target_value
            margin = target_value - actual_value  # positive = passing
        elif operator == ">=":
            satisfied = actual_value >= target_value
            margin = actual_value - target_value  # positive = passing
        elif operator == "==":
            tolerance = abs(target_value) * 0.01 if target_value != 0 else 0.01
            satisfied = abs(actual_value - target_value) <= tolerance
            margin = tolerance - abs(actual_value - target_value)  # positive = passing

        if not satisfied:
            all_satisfied = False

        constraint_results.append({
            "label": label,
            "variable": variable,
            "operator": operator,
            "target_value": target_value,
            "actual_value": round(actual_value, 2),
            "units": units,
            "satisfied": satisfied,
            "margin": round(margin, 2),
        })

    # Build human-readable summary
    n_satisfied = sum(1 for r in constraint_results if r["satisfied"])
    n_total = len(constraint_results)
    summary_parts = [f"{n_satisfied} of {n_total} constraints satisfied."]
    for r in constraint_results:
        if not r["satisfied"] and r["margin"] is not None:
            summary_parts.append(
                f"{r['variable']} {'exceeds limit' if r['operator'] == '<=' else 'below minimum'} by {abs(r['margin']):.0f} {r.get('units', '')}."
            )

    return {
        "success": True,
        "session_id": session_id,
        "all_satisfied": all_satisfied,
        "results": constraint_results,
        "summary": " ".join(summary_parts),
    }


# --- Server entry point ---

def main():
    """Start the Aviary MCP server."""
    logger.info("Starting Aviary MCP Server on 0.0.0.0:8600...")
    mcp.run(transport="streamable-http", host="0.0.0.0", port=8600, path="/mcp")


if __name__ == "__main__":
    main()
