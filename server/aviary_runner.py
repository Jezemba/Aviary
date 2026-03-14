"""Aviary Level 2 API wrapper for the MCP server.

Encapsulates the Aviary problem setup, parameter mutation, simulation execution,
and result extraction. Each AviarySession holds one AviaryProblem instance.
"""

import math
import time
import logging
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError

import aviary.api as av

# Import default phase_info — path varies by Aviary version
try:
    from aviary.models.missions.height_energy_default import phase_info as default_phase_info
except ImportError:
    from aviary.interface.default_phase_info.height_energy import phase_info as default_phase_info

from design_space import VARIABLE_NAME_MAP

logger = logging.getLogger(__name__)

# Aircraft CSV path (bundled with Aviary pip package)
AIRCRAFT_CSV = "models/test_aircraft/aircraft_for_bench_FwFm.csv"

# Default mission configuration
DEFAULT_MISSION = {
    "range_nmi": 1500,
    "num_passengers": 162,
    "cruise_mach": 0.785,
    "cruise_altitude_ft": 35000,
    "optimizer_max_iter": 200,
}

# Default passenger mass (kg) — standard IATA assumption
PASSENGER_MASS_KG = 90.7  # ~200 lbs including baggage

# Max passengers for the default aircraft
MAX_PASSENGERS = 200

# Custom design variables that are safe to add (verified working with v0.9.10)
# SCALED_SLS_THRUST is a computed output and can't be a direct design var.
# SCALE_FACTOR and AREA are demonstrated in the official examples.
CUSTOM_DESIGN_VARS = {
    "Aircraft.Engine.SCALE_FACTOR": {"lower": 0.8, "upper": 1.5, "units": None, "ref": 1.0},
    "Aircraft.Wing.AREA": {"lower": 100.0, "upper": 160.0, "units": "m**2", "ref": 130.0},
}


def _resolve_aviary_var(name):
    """Convert a PRD-style dotted variable name to the aviary accessor attribute.

    Returns the aviary variable accessor (e.g., av.Aircraft.Wing.ASPECT_RATIO)
    for use with get_val/set_val.
    """
    parts = name.split(".")
    obj = av
    for part in parts:
        obj = getattr(obj, part)
    return obj


def _build_phase_info(mission_config):
    """Build a customized phase_info dict from mission configuration.

    Only modifies target_range in post_mission. Cruise Mach and altitude are set
    via aviary_inputs (Mission.Design variables) rather than by modifying phase_info
    keys directly, which avoids unit compatibility issues across Aviary versions.
    """
    pi = deepcopy(default_phase_info)

    range_nmi = mission_config.get("range_nmi", DEFAULT_MISSION["range_nmi"])

    # Only update target range — other mission params set via aviary_inputs
    pi["post_mission"]["target_range"] = (float(range_nmi), "nmi")

    return pi


def create_aviary_problem(aircraft_params=None, mission_config=None):
    """Create and set up a fully configured AviaryProblem ready to run.

    Parameters
    ----------
    aircraft_params : dict, optional
        Dict of PRD-style variable names to float values to override.
    mission_config : dict, optional
        Mission configuration dict with range_nmi, num_passengers, etc.

    Returns
    -------
    prob : av.AviaryProblem
        Fully set up problem ready for run_aviary_problem().
    """
    if mission_config is None:
        mission_config = dict(DEFAULT_MISSION)
    if aircraft_params is None:
        aircraft_params = {}

    phase_info = _build_phase_info(mission_config)
    max_iter = mission_config.get("optimizer_max_iter", DEFAULT_MISSION["optimizer_max_iter"])

    prob = av.AviaryProblem(verbosity=0)

    # Step 1: Load inputs
    prob.load_inputs(AIRCRAFT_CSV, phase_info)

    # Apply aircraft parameter overrides to aviary_inputs BEFORE preprocessing
    for param_name, value in aircraft_params.items():
        aviary_var = _resolve_aviary_var(param_name)
        try:
            prob.aviary_inputs.set_val(aviary_var, float(value))
        except Exception as e:
            logger.warning(f"Could not set {param_name} on aviary_inputs: {e}")

    # Set mission-level parameters via aviary_inputs
    cruise_mach = mission_config.get("cruise_mach", DEFAULT_MISSION["cruise_mach"])
    cruise_alt_ft = mission_config.get("cruise_altitude_ft", DEFAULT_MISSION["cruise_altitude_ft"])
    range_nmi = mission_config.get("range_nmi", DEFAULT_MISSION["range_nmi"])

    try:
        prob.aviary_inputs.set_val(av.Mission.Design.CRUISE_ALTITUDE, cruise_alt_ft, units="ft")
    except Exception as e:
        logger.warning(f"Could not set cruise altitude: {e}")
    try:
        prob.aviary_inputs.set_val(av.Mission.Summary.CRUISE_MACH, cruise_mach)
    except Exception as e:
        logger.warning(f"Could not set cruise mach: {e}")
    try:
        prob.aviary_inputs.set_val(av.Mission.Design.RANGE, range_nmi, units="nmi")
    except Exception as e:
        logger.warning(f"Could not set range: {e}")

    # Step 2: Check and preprocess
    prob.check_and_preprocess_inputs()

    # Step 3: Build model (individual methods for v0.9.x compatibility)
    prob.add_pre_mission_systems()
    prob.add_phases()
    prob.add_post_mission_systems()
    prob.link_phases()

    # Step 4: Add driver
    prob.add_driver("SLSQP", max_iter=max_iter)

    # Step 5: Add default design variables (sizing)
    prob.add_design_variables()

    # Step 6: Add custom design variables (only ones verified to work)
    for param_name, bounds in CUSTOM_DESIGN_VARS.items():
        aviary_var = _resolve_aviary_var(param_name)
        kwargs = {"lower": bounds["lower"], "upper": bounds["upper"], "ref": bounds["ref"]}
        if bounds["units"]:
            kwargs["units"] = bounds["units"]
        try:
            prob.model.add_design_var(aviary_var, **kwargs)
        except Exception as e:
            logger.warning(f"Could not add design var {param_name}: {e}")

    # Step 7: Add objective
    prob.add_objective()

    # Step 8: Setup
    prob.setup()

    # Step 9: Set initial guesses (REQUIRED before run_aviary_problem)
    prob.set_initial_guesses()

    # Apply parameter overrides AFTER setup via set_val
    for param_name, value in aircraft_params.items():
        aviary_var = _resolve_aviary_var(param_name)
        try:
            prob.set_val(aviary_var, float(value))
        except Exception as e:
            logger.warning(f"Could not set_val {param_name} after setup: {e}")

    return prob


def run_problem(prob, timeout_seconds=300):
    """Run the Aviary problem with a wall-clock timeout.

    Returns
    -------
    dict with keys: converged, exit_code, runtime_seconds, iterations, summary
    """
    start = time.time()

    # Run in a thread so we can enforce timeout
    executor = ThreadPoolExecutor(max_workers=1)
    future = executor.submit(
        prob.run_aviary_problem,
        suppress_solver_print=True,
        run_driver=True,
        simulate=False,
        make_plots=False,
    )

    timed_out = False
    try:
        future.result(timeout=timeout_seconds)
    except FuturesTimeoutError:
        timed_out = True
        logger.warning("Aviary run timed out after %d seconds", timeout_seconds)
    except Exception as e:
        logger.error("Aviary run failed: %s", e)
        raise
    finally:
        executor.shutdown(wait=False)

    elapsed = time.time() - start

    # Extract results
    try:
        exit_code = _get_optimizer_exit_code(prob)
    except Exception:
        exit_code = -1

    converged = (exit_code == 0) and not timed_out

    # Extract summary values
    summary = {}
    try:
        summary["fuel_burned_kg"] = float(prob.get_val(av.Mission.Summary.FUEL_BURNED, units="kg")[0])
    except Exception:
        summary["fuel_burned_kg"] = None
    try:
        summary["gtow_kg"] = float(prob.get_val(av.Mission.Summary.GROSS_MASS, units="kg")[0])
    except Exception:
        summary["gtow_kg"] = None
    try:
        summary["wing_mass_kg"] = float(prob.get_val(av.Aircraft.Wing.MASS, units="kg")[0])
    except Exception:
        summary["wing_mass_kg"] = None

    # Get iteration count from driver
    iterations = _get_iteration_count(prob)

    return {
        "converged": converged,
        "exit_code": exit_code,
        "runtime_seconds": round(elapsed, 2),
        "iterations": iterations,
        "summary": summary,
        "timed_out": timed_out,
    }


def extract_results(prob, converged):
    """Extract full results from a completed run.

    Returns
    -------
    dict with all standard output fields.
    """
    results = {"converged": converged}

    try:
        results["fuel_burned_kg"] = float(prob.get_val(av.Mission.Summary.FUEL_BURNED, units="kg")[0])
    except Exception:
        results["fuel_burned_kg"] = None

    try:
        results["gtow_kg"] = float(prob.get_val(av.Mission.Summary.GROSS_MASS, units="kg")[0])
    except Exception:
        results["gtow_kg"] = None

    try:
        results["wing_mass_kg"] = float(prob.get_val(av.Aircraft.Wing.MASS, units="kg")[0])
    except Exception:
        results["wing_mass_kg"] = None

    try:
        results["reserve_fuel_kg"] = float(prob.get_val(av.Mission.Design.RESERVE_FUEL, units="kg")[0])
    except Exception:
        results["reserve_fuel_kg"] = None

    # Zero fuel weight = GTOW - fuel_burned - reserve_fuel
    if results["gtow_kg"] is not None and results["fuel_burned_kg"] is not None:
        reserve = results["reserve_fuel_kg"] or 0.0
        results["zero_fuel_weight_kg"] = results["gtow_kg"] - results["fuel_burned_kg"] - reserve
    else:
        results["zero_fuel_weight_kg"] = None

    return results


def get_current_param_value(prob, param_name):
    """Get the current value of a parameter from the problem."""
    aviary_var = _resolve_aviary_var(param_name)
    try:
        return float(prob.get_val(aviary_var)[0])
    except Exception:
        try:
            return float(prob.aviary_inputs.get_val(aviary_var))
        except Exception:
            return None


def validate_parameters(aircraft_params=None, mission_config=None, timeout_seconds=30):
    """Validate parameters by running static checks and a quick model evaluation.

    Layer 1 — Static checks (instant):
      - NaN / inf detection
      - Bounds violations
      - Wing geometry coupling: AR = span² / area

    Layer 2 — Quick model evaluation (~5-10s):
      - Builds the full Aviary problem
      - Calls run_aviary_problem(run_driver=False) — single function eval, no optimizer
      - Checks outputs for NaN / inf

    Returns
    -------
    dict with keys:
      valid (bool), static_checks (list of violations), model_eval (dict or None),
      runtime_seconds (float)
    """
    start = time.time()

    if aircraft_params is None:
        aircraft_params = {}
    if mission_config is None:
        mission_config = dict(DEFAULT_MISSION)

    # Import design space metadata for bounds/defaults
    from design_space import DESIGN_PARAMETERS

    param_lookup = {p["name"]: p for p in DESIGN_PARAMETERS}
    violations = []

    # ---- Layer 1: Static checks ----

    # 1a. NaN / inf / negative-where-positive-required
    for name, value in aircraft_params.items():
        try:
            fval = float(value)
        except (TypeError, ValueError):
            violations.append({
                "parameter": name,
                "check": "type",
                "message": f"Cannot convert {value!r} to float",
                "severity": "error",
            })
            continue

        if not math.isfinite(fval):
            violations.append({
                "parameter": name,
                "check": "finite",
                "message": f"{name} = {fval} is NaN or infinity",
                "severity": "error",
            })
            continue

        # 1b. Bounds check
        meta = param_lookup.get(name)
        if meta:
            if fval < meta["min"] or fval > meta["max"]:
                violations.append({
                    "parameter": name,
                    "check": "bounds",
                    "message": f"{name} = {fval} outside [{meta['min']}, {meta['max']}]",
                    "severity": "warning",
                })

    # 1c. Wing geometry coupling: AR = span² / area
    # Get effective values (user overrides or defaults)
    def _effective(name):
        if name in aircraft_params:
            return float(aircraft_params[name])
        meta = param_lookup.get(name)
        return meta["current_default"] if meta else None

    ar = _effective("Aircraft.Wing.ASPECT_RATIO")
    span = _effective("Aircraft.Wing.SPAN")
    area = _effective("Aircraft.Wing.AREA")

    if ar is not None and span is not None and area is not None and area > 0:
        computed_ar = (span ** 2) / area
        rel_error = abs(ar - computed_ar) / ar if ar > 0 else abs(ar - computed_ar)
        if rel_error > 0.05:  # 5% tolerance
            violations.append({
                "parameter": "Aircraft.Wing.{ASPECT_RATIO, SPAN, AREA}",
                "check": "coupling",
                "message": (
                    f"Wing geometry inconsistency: AR={ar:.3f} but span²/area="
                    f"{computed_ar:.3f} (relative error {rel_error:.1%}). "
                    f"Change at most one of ASPECT_RATIO, SPAN, AREA per call."
                ),
                "severity": "warning",
            })

    # If there are hard errors (NaN/inf/type), skip the expensive model eval
    hard_errors = [v for v in violations if v["severity"] == "error"]
    if hard_errors:
        elapsed = time.time() - start
        return {
            "valid": False,
            "static_checks": violations,
            "model_eval": None,
            "runtime_seconds": round(elapsed, 2),
        }

    # ---- Layer 2: Quick model evaluation ----
    model_eval = {"success": False, "error": None, "outputs": {}}

    try:
        prob = create_aviary_problem(aircraft_params, mission_config)

        # Run model once WITHOUT the optimizer (single function evaluation)
        executor = ThreadPoolExecutor(max_workers=1)
        future = executor.submit(
            prob.run_aviary_problem,
            suppress_solver_print=True,
            run_driver=False,   # key: no optimization, just evaluate once
            simulate=False,
            make_plots=False,
        )

        try:
            future.result(timeout=timeout_seconds)
        except FuturesTimeoutError:
            model_eval["error"] = f"Model evaluation timed out after {timeout_seconds}s"
            elapsed = time.time() - start
            return {
                "valid": False,
                "static_checks": violations,
                "model_eval": model_eval,
                "runtime_seconds": round(elapsed, 2),
            }
        finally:
            executor.shutdown(wait=False)

        # Extract key outputs and check for NaN/inf
        nan_outputs = []
        outputs = {}

        check_vars = [
            ("fuel_burned_kg", av.Mission.Summary.FUEL_BURNED, "kg"),
            ("gtow_kg", av.Mission.Summary.GROSS_MASS, "kg"),
            ("wing_mass_kg", av.Aircraft.Wing.MASS, "kg"),
        ]

        for label, var, units in check_vars:
            try:
                val = float(prob.get_val(var, units=units)[0])
                outputs[label] = val
                if not math.isfinite(val):
                    nan_outputs.append(label)
            except Exception:
                outputs[label] = None
                nan_outputs.append(label)

        model_eval["success"] = True
        model_eval["outputs"] = outputs
        model_eval["nan_outputs"] = nan_outputs

        if nan_outputs:
            model_eval["error"] = f"NaN/inf detected in: {', '.join(nan_outputs)}"
            violations.append({
                "parameter": "model_evaluation",
                "check": "nan_in_outputs",
                "message": model_eval["error"],
                "severity": "error",
            })

    except Exception as e:
        model_eval["error"] = str(e)
        violations.append({
            "parameter": "model_evaluation",
            "check": "setup_or_eval_failure",
            "message": f"Model evaluation failed: {e}",
            "severity": "error",
        })

    elapsed = time.time() - start
    has_errors = any(v["severity"] == "error" for v in violations)

    return {
        "valid": not has_errors,
        "static_checks": violations,
        "model_eval": model_eval,
        "runtime_seconds": round(elapsed, 2),
    }


def extract_trajectory(prob):
    """Extract timeseries trajectory data from a completed Aviary run.

    Reads Dymos timeseries variables from all three height-energy phases
    (climb, cruise, descent) and concatenates them into single arrays.

    Parameters
    ----------
    prob : av.AviaryProblem
        A problem that has completed run_aviary_problem().

    Returns
    -------
    dict with keys:
        time_s, altitude_ft, mach, mass_kg, throttle, drag_N, distance_nmi,
        phase_labels (list of str per point), num_points (int)
    """
    phases = ["climb", "cruise", "descent"]

    # Map of output key -> (Dymos timeseries variable name, units)
    var_map = {
        "time_s": ("time", "s"),
        "altitude_ft": ("altitude", "ft"),
        "mach": ("mach", None),
        "mass_kg": ("mass", "kg"),
        "distance_nmi": ("distance", "nmi"),
        "throttle": ("throttle", None),
        "drag_N": ("drag", "N"),
    }

    trajectory = {key: [] for key in var_map}
    trajectory["phase_labels"] = []

    for phase in phases:
        # Determine how many points this phase has by reading time
        try:
            time_vals = prob.get_val(
                f"traj.phases.{phase}.timeseries.time", units="s"
            )
            n_points = len(time_vals)
        except Exception as e:
            logger.warning("Could not read timeseries for phase '%s': %s", phase, e)
            continue

        trajectory["phase_labels"].extend([phase] * n_points)

        for key, (var_name, units) in var_map.items():
            try:
                if units:
                    vals = prob.get_val(
                        f"traj.phases.{phase}.timeseries.{var_name}", units=units
                    )
                else:
                    vals = prob.get_val(
                        f"traj.phases.{phase}.timeseries.{var_name}"
                    )
                trajectory[key].extend(float(v) for v in vals.flatten())
            except Exception:
                # Variable not available in this phase — fill with None
                trajectory[key].extend([None] * n_points)

    trajectory["num_points"] = len(trajectory["phase_labels"])
    return trajectory


def _get_optimizer_exit_code(prob):
    """Extract convergence status from the problem's driver.

    Uses prob.driver.fail (standard OpenMDAO boolean):
      - False = optimizer converged successfully → exit_code 0
      - True  = optimizer did NOT converge       → exit_code 1
    """
    try:
        if hasattr(prob, "driver") and hasattr(prob.driver, "fail"):
            return 0 if not prob.driver.fail else 1
    except Exception:
        pass
    return -1


def _get_iteration_count(prob):
    """Extract the iteration count from the optimizer."""
    try:
        if hasattr(prob.driver, "iter_count"):
            return prob.driver.iter_count
        if hasattr(prob.driver, "result") and hasattr(prob.driver.result, "nit"):
            return prob.driver.result.nit
    except Exception:
        pass
    return -1
