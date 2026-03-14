"""Pytest suite for the Aviary MCP server tools.

Tests marked with @pytest.mark.slow require the running server on localhost:8600.
Non-slow tests exercise the design_space and session_manager modules directly.
"""

import asyncio
import json
import sys
import os
import time
import uuid

import pytest

# Add project root to path so we can import modules
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from design_space import (
    DESIGN_PARAMETERS,
    VALID_PARAMETER_NAMES,
    COUPLING_NOTE,
    get_design_space,
)
from session_manager import SessionManager, AviarySession


# ============================================================
# Unit tests — design_space module (no server needed)
# ============================================================

class TestGetDesignSpace:
    """Tests for get_design_space function."""

    def test_all_categories_returns_correct_structure(self):
        result = get_design_space("all")
        assert result["success"] is True
        assert isinstance(result["parameters"], list)
        assert len(result["parameters"]) == 10  # 5 wing + 3 fuselage + 2 engine
        for p in result["parameters"]:
            assert "name" in p
            assert "display_name" in p
            assert "category" in p
            assert "current_default" in p
            assert "units" in p
            assert "min" in p
            assert "max" in p
            assert "description" in p

    def test_wing_category_filter(self):
        result = get_design_space("wing")
        assert result["success"] is True
        assert all(p["category"] == "wing" for p in result["parameters"])
        assert len(result["parameters"]) == 5

    def test_fuselage_category_filter(self):
        result = get_design_space("fuselage")
        assert result["success"] is True
        assert all(p["category"] == "fuselage" for p in result["parameters"])
        assert len(result["parameters"]) == 3

    def test_engine_category_filter(self):
        result = get_design_space("engine")
        assert result["success"] is True
        assert all(p["category"] == "engine" for p in result["parameters"])
        assert len(result["parameters"]) == 2

    def test_coupling_note_present(self):
        result = get_design_space("all")
        assert "coupling_note" in result
        assert "AR = span" in result["coupling_note"]

    def test_aircraft_name_present(self):
        result = get_design_space("all")
        assert "aircraft_name" in result
        assert "737" in result["aircraft_name"] or "A320" in result["aircraft_name"]


# ============================================================
# Unit tests — session_manager module (no server needed)
# ============================================================

class TestSessionManager:
    """Tests for SessionManager."""

    def test_create_session(self):
        mgr = SessionManager()
        session = mgr.create_session()
        assert session.session_id is not None
        assert session.created_at is not None
        assert session.aircraft_params == {}
        assert session.mission_config["range_nmi"] == 1500

    def test_get_session(self):
        mgr = SessionManager()
        session = mgr.create_session()
        retrieved = mgr.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_nonexistent_session(self):
        mgr = SessionManager()
        assert mgr.get_session("nonexistent-id") is None

    def test_idle_timeout_cleanup(self):
        mgr = SessionManager(idle_timeout=1)  # 1 second timeout
        session = mgr.create_session()
        sid = session.session_id
        time.sleep(1.5)
        assert mgr.get_session(sid) is None

    def test_session_touch_resets_timeout(self):
        mgr = SessionManager(idle_timeout=2)
        session = mgr.create_session()
        sid = session.session_id
        time.sleep(1)
        # Touch should reset timeout
        retrieved = mgr.get_session(sid)
        assert retrieved is not None
        time.sleep(1)
        # Should still be alive because we touched it 1s ago
        assert mgr.get_session(sid) is not None

    def test_recreate_after_expiry(self):
        mgr = SessionManager(idle_timeout=1)
        session1 = mgr.create_session()
        time.sleep(1.5)
        assert mgr.get_session(session1.session_id) is None
        session2 = mgr.create_session()
        assert session2.session_id != session1.session_id
        assert mgr.get_session(session2.session_id) is not None

    def test_remove_session(self):
        mgr = SessionManager()
        session = mgr.create_session()
        mgr.remove_session(session.session_id)
        assert mgr.get_session(session.session_id) is None

    def test_active_session_count(self):
        mgr = SessionManager()
        assert mgr.active_session_count() == 0
        mgr.create_session()
        mgr.create_session()
        assert mgr.active_session_count() == 2


# ============================================================
# Unit tests — set_aircraft_parameters validation
# ============================================================

class TestSetAircraftParametersValidation:
    """Tests for parameter validation logic (no server needed)."""

    def test_valid_parameter_names(self):
        assert "Aircraft.Wing.ASPECT_RATIO" in VALID_PARAMETER_NAMES
        assert "Aircraft.Engine.SCALED_SLS_THRUST" in VALID_PARAMETER_NAMES
        assert len(VALID_PARAMETER_NAMES) == 10

    def test_unknown_parameter_detected(self):
        assert "Aircraft.Wing.FAKE_PARAM" not in VALID_PARAMETER_NAMES


# ============================================================
# Unit tests — configure_mission validation
# ============================================================

class TestConfigureMissionValidation:
    """Tests for mission config validation logic."""

    def test_default_mission_values(self):
        mgr = SessionManager()
        session = mgr.create_session()
        mc = session.mission_config
        assert mc["range_nmi"] == 1500
        assert mc["num_passengers"] == 162
        assert mc["cruise_mach"] == 0.785
        assert mc["cruise_altitude_ft"] == 35000
        assert mc["optimizer_max_iter"] == 200

    def test_session_mission_config_mutable(self):
        mgr = SessionManager()
        session = mgr.create_session()
        session.mission_config["range_nmi"] = 2000
        assert session.mission_config["range_nmi"] == 2000


# ============================================================
# Unit tests — validate_parameters static checks (no server needed)
# ============================================================

class TestValidateParametersStatic:
    """Tests for the static validation layer in validate_parameters."""

    def _static_only(self, aircraft_params):
        """Run only the static checks from validate_parameters (no model eval)."""
        import math
        from design_space import DESIGN_PARAMETERS

        param_lookup = {p["name"]: p for p in DESIGN_PARAMETERS}
        violations = []

        for name, value in aircraft_params.items():
            try:
                fval = float(value)
            except (TypeError, ValueError):
                violations.append({"parameter": name, "check": "type", "severity": "error"})
                continue
            if not math.isfinite(fval):
                violations.append({"parameter": name, "check": "finite", "severity": "error"})
                continue
            meta = param_lookup.get(name)
            if meta and (fval < meta["min"] or fval > meta["max"]):
                violations.append({"parameter": name, "check": "bounds", "severity": "warning"})

        # Coupling check
        def _eff(name):
            if name in aircraft_params:
                try:
                    return float(aircraft_params[name])
                except (TypeError, ValueError):
                    return None
            meta = param_lookup.get(name)
            return meta["current_default"] if meta else None

        ar = _eff("Aircraft.Wing.ASPECT_RATIO")
        span = _eff("Aircraft.Wing.SPAN")
        area = _eff("Aircraft.Wing.AREA")
        if ar and span and area and area > 0:
            computed = (span ** 2) / area
            rel_err = abs(ar - computed) / ar if ar > 0 else abs(ar - computed)
            if rel_err > 0.05:
                violations.append({"parameter": "coupling", "check": "coupling", "severity": "warning"})

        return violations

    def test_nan_detected(self):
        v = self._static_only({"Aircraft.Wing.ASPECT_RATIO": float("nan")})
        assert any(x["check"] == "finite" for x in v)

    def test_inf_detected(self):
        v = self._static_only({"Aircraft.Wing.AREA": float("inf")})
        assert any(x["check"] == "finite" for x in v)

    def test_bounds_violation_detected(self):
        v = self._static_only({"Aircraft.Wing.ASPECT_RATIO": 1.0})  # min is 7.0
        assert any(x["check"] == "bounds" for x in v)

    def test_valid_params_no_violations(self):
        v = self._static_only({"Aircraft.Wing.SWEEP": 30.0})
        assert len(v) == 0

    def test_coupling_violation_detected(self):
        v = self._static_only({
            "Aircraft.Wing.ASPECT_RATIO": 14.0,
            "Aircraft.Wing.SPAN": 30.0,
            "Aircraft.Wing.AREA": 160.0,
        })
        assert any(x["check"] == "coupling" for x in v)

    def test_coupling_consistent_no_violation(self):
        # AR = span^2 / area => 11.22 = 37.35^2 / 124.6 ≈ 11.19 (within 5%)
        v = self._static_only({
            "Aircraft.Wing.ASPECT_RATIO": 11.22,
            "Aircraft.Wing.SPAN": 37.35,
            "Aircraft.Wing.AREA": 124.6,
        })
        assert not any(x["check"] == "coupling" for x in v)

    def test_type_error_detected(self):
        v = self._static_only({"Aircraft.Wing.AREA": "not_a_number"})
        assert any(x["check"] == "type" for x in v)


# ============================================================
# Integration tests — require running server (marked slow)
# ============================================================

@pytest.mark.slow
class TestServerIntegration:
    """Integration tests that require the Aviary MCP server running on localhost:8600."""

    @pytest.fixture(autouse=True)
    def setup_client(self):
        """Setup for server-dependent tests."""
        self.server_url = "http://localhost:8600/mcp"

    async def _call_tool(self, tool_name, args):
        from fastmcp import Client
        async with Client(self.server_url) as client:
            resp = await client.call_tool(tool_name, args)
            return json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]

    def _call(self, tool_name, args):
        return asyncio.get_event_loop().run_until_complete(self._call_tool(tool_name, args))

    # --- get_design_space ---

    def test_get_design_space_all(self):
        data = self._call("get_design_space", {"category": "all"})
        assert data["success"] is True
        assert len(data["parameters"]) == 10

    def test_get_design_space_category_filter(self):
        data = self._call("get_design_space", {"category": "engine"})
        assert data["success"] is True
        assert all(p["category"] == "engine" for p in data["parameters"])

    # --- create_session ---

    def test_create_session(self):
        data = self._call("create_session", {})
        assert data["success"] is True
        assert data["session_id"] is not None

    # --- set_aircraft_parameters ---

    def test_set_valid_parameter(self):
        session = self._call("create_session", {})
        data = self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {"Aircraft.Wing.ASPECT_RATIO": 12.0},
        })
        assert data["success"] is True
        assert len(data["applied"]) == 1
        assert data["applied"][0]["new_value"] == 12.0

    def test_set_unknown_parameter(self):
        session = self._call("create_session", {})
        data = self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {"Aircraft.Wing.FAKE": 1.0},
        })
        assert data["success"] is False
        assert data["error_code"] == "UNKNOWN_PARAMETER"

    def test_set_out_of_range_returns_warning(self):
        session = self._call("create_session", {})
        data = self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {"Aircraft.Wing.ASPECT_RATIO": 100.0},
        })
        assert data["success"] is True
        assert len(data["warnings"]) > 0

    def test_set_multiple_parameters(self):
        session = self._call("create_session", {})
        data = self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {
                "Aircraft.Wing.ASPECT_RATIO": 12.0,
                "Aircraft.Engine.SCALED_SLS_THRUST": 130000,
            },
        })
        assert data["success"] is True
        assert len(data["applied"]) == 2

    # --- configure_mission ---

    def test_configure_mission_defaults(self):
        session = self._call("create_session", {})
        data = self._call("configure_mission", {"session_id": session["session_id"]})
        assert data["success"] is True
        ms = data["mission_summary"]
        assert ms["range_nmi"] == 1500
        assert ms["num_passengers"] == 162

    def test_configure_mission_custom(self):
        session = self._call("create_session", {})
        data = self._call("configure_mission", {
            "session_id": session["session_id"],
            "range_nmi": 2000,
            "num_passengers": 100,
            "cruise_mach": 0.80,
            "cruise_altitude_ft": 37000,
        })
        assert data["success"] is True
        ms = data["mission_summary"]
        assert ms["range_nmi"] == 2000
        assert ms["num_passengers"] == 100

    def test_configure_mission_mach_out_of_bounds(self):
        session = self._call("create_session", {})
        data = self._call("configure_mission", {
            "session_id": session["session_id"],
            "cruise_mach": 0.99,
        })
        assert data["success"] is False
        assert data["error_code"] == "OUT_OF_BOUNDS"

    def test_configure_mission_negative_passengers(self):
        session = self._call("create_session", {})
        data = self._call("configure_mission", {
            "session_id": session["session_id"],
            "num_passengers": -1,
        })
        assert data["success"] is False
        assert data["error_code"] == "INVALID_PASSENGER_COUNT"

    # --- run_simulation ---

    def test_run_simulation_basic(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        data = self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        assert data["success"] is True
        assert "exit_code" in data
        assert "runtime_seconds" in data

    # --- get_results ---

    def test_get_results_before_run(self):
        session = self._call("create_session", {})
        data = self._call("get_results", {"session_id": session["session_id"]})
        assert data["success"] is False
        assert data["error_code"] == "NO_RESULTS"

    def test_get_results_after_run(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("get_results", {"session_id": session["session_id"]})
        assert data["success"] is True
        for field in ["fuel_burned_kg", "gtow_kg", "wing_mass_kg",
                      "reserve_fuel_kg", "zero_fuel_weight_kg"]:
            assert field in data

    # --- check_constraints ---

    def test_check_constraints_passing(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [{
                "variable": "fuel_burned_kg",
                "operator": "<=",
                "value": 100000,
                "units": "kg",
                "label": "Generous fuel limit",
            }],
        })
        assert data["success"] is True
        assert data["all_satisfied"] is True
        assert data["results"][0]["satisfied"] is True
        assert data["results"][0]["margin"] > 0

    def test_check_constraints_failing(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [{
                "variable": "fuel_burned_kg",
                "operator": "<=",
                "value": 1.0,
                "units": "kg",
                "label": "Impossible fuel limit",
            }],
        })
        assert data["success"] is True
        assert data["all_satisfied"] is False
        assert data["results"][0]["satisfied"] is False
        assert data["results"][0]["margin"] < 0

    def test_check_constraints_unknown_variable(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [{
                "variable": "nonexistent_var",
                "operator": "<=",
                "value": 100,
            }],
        })
        assert data["success"] is False
        assert data["error_code"] == "UNKNOWN_VARIABLE"

    def test_check_constraints_invalid_operator(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [{
                "variable": "fuel_burned_kg",
                "operator": "!=",
                "value": 100,
            }],
        })
        assert data["success"] is False
        assert data["error_code"] == "INVALID_OPERATOR"

    def test_check_constraints_all_satisfied_false_if_one_fails(self):
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 5,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        data = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [
                {"variable": "fuel_burned_kg", "operator": "<=", "value": 100000, "label": "pass"},
                {"variable": "fuel_burned_kg", "operator": "<=", "value": 1.0, "label": "fail"},
            ],
        })
        assert data["success"] is True
        assert data["all_satisfied"] is False

    # --- validate_parameters ---

    def test_validate_defaults_is_valid(self):
        session = self._call("create_session", {})
        data = self._call("validate_parameters", {"session_id": session["session_id"]})
        assert data["success"] is True
        assert data["valid"] is True
        assert "VALID" in data["summary"]

    def test_validate_with_good_params(self):
        session = self._call("create_session", {})
        self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {"Aircraft.Wing.SWEEP": 30.0},
        })
        data = self._call("validate_parameters", {"session_id": session["session_id"]})
        assert data["success"] is True
        assert data["valid"] is True

    def test_validate_coupling_violation_warns(self):
        session = self._call("create_session", {})
        self._call("set_aircraft_parameters", {
            "session_id": session["session_id"],
            "parameters": {
                "Aircraft.Wing.ASPECT_RATIO": 14.0,
                "Aircraft.Wing.SPAN": 30.0,
                "Aircraft.Wing.AREA": 160.0,
            },
        })
        data = self._call("validate_parameters", {"session_id": session["session_id"]})
        assert data["success"] is True
        # Should have a coupling warning but model eval may still pass
        has_coupling_warning = any(
            v["check"] == "coupling" for v in data.get("violations", [])
        )
        assert has_coupling_warning

    def test_validate_invalid_session(self):
        data = self._call("validate_parameters", {"session_id": "nonexistent"})
        assert data["success"] is False
        assert data["error_code"] == "INVALID_SESSION"

    def test_validate_model_eval_runs(self):
        """Ensure model_eval is populated with outputs."""
        session = self._call("create_session", {})
        data = self._call("validate_parameters", {"session_id": session["session_id"]})
        assert data["model_eval"] is not None
        assert data["model_eval"]["success"] is True
        assert "gtow_kg" in data["model_eval"]["outputs"]

    # --- Full integration workflow ---

    def test_full_workflow_default_params(self):
        """Full workflow from create_session through check_constraints with defaults."""
        # Create session
        session = self._call("create_session", {})
        assert session["success"] is True

        # Configure with enough iterations for reasonable convergence
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 50,
        })

        # Run simulation
        run = self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 600,
        })
        assert run["success"] is True

        # Get results
        results = self._call("get_results", {"session_id": session["session_id"]})
        assert results["success"] is True
        fuel = results["fuel_burned_kg"]
        assert fuel is not None

        # Fuel should be within a reasonable range (benchmark: ~6975 kg with 200 iter)
        # With only 50 iterations the optimizer may not fully converge, so allow wide range
        assert 4000 < fuel < 15000, f"Fuel burned {fuel} kg outside expected range"

        # Check constraints
        constraints = self._call("check_constraints", {
            "session_id": session["session_id"],
            "constraints": [{
                "variable": "fuel_burned_kg",
                "operator": "<=",
                "value": 15000,
                "units": "kg",
                "label": "Fuel budget",
            }],
        })
        assert constraints["success"] is True
        assert constraints["all_satisfied"] is True

    # --- get_trajectory ---

    def test_get_trajectory_no_run(self):
        """get_trajectory should fail if no simulation has been run."""
        session = self._call("create_session", {})
        data = self._call("get_trajectory", {"session_id": session["session_id"]})
        assert data["success"] is False
        assert data["error_code"] == "NO_RESULTS"

    def test_get_trajectory_invalid_session(self):
        data = self._call("get_trajectory", {"session_id": "nonexistent"})
        assert data["success"] is False
        assert data["error_code"] == "INVALID_SESSION"

    def test_get_trajectory_after_run(self):
        """Full trajectory extraction after a short simulation run."""
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 10,
        })
        run = self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 300,
        })
        assert run["success"] is True

        data = self._call("get_trajectory", {"session_id": session["session_id"]})
        assert data["success"] is True
        traj = data["trajectory"]
        assert traj["num_points"] > 0
        assert len(traj["phase_labels"]) == traj["num_points"]
        assert len(traj["time_s"]) == traj["num_points"]
        assert len(traj["altitude_ft"]) == traj["num_points"]
        assert len(traj["mach"]) == traj["num_points"]
        assert len(traj["mass_kg"]) == traj["num_points"]
        # Verify phases are present
        phases_seen = set(traj["phase_labels"])
        assert "climb" in phases_seen
        assert "cruise" in phases_seen
        assert "descent" in phases_seen

    def test_get_trajectory_variable_filter(self):
        """get_trajectory with a variable filter returns only requested vars."""
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 10,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 300,
        })
        data = self._call("get_trajectory", {
            "session_id": session["session_id"],
            "variables": ["time_s", "altitude_ft"],
        })
        assert data["success"] is True
        traj = data["trajectory"]
        assert "time_s" in traj
        assert "altitude_ft" in traj
        assert "mach" not in traj
        assert "mass_kg" not in traj

    def test_get_trajectory_invalid_variable(self):
        """get_trajectory with an unknown variable name returns error."""
        session = self._call("create_session", {})
        self._call("configure_mission", {
            "session_id": session["session_id"],
            "optimizer_max_iter": 10,
        })
        self._call("run_simulation", {
            "session_id": session["session_id"],
            "timeout_seconds": 300,
        })
        data = self._call("get_trajectory", {
            "session_id": session["session_id"],
            "variables": ["time_s", "fake_variable"],
        })
        assert data["success"] is False
        assert data["error_code"] == "UNKNOWN_VARIABLE"

    # --- Session edge cases ---

    def test_invalid_session_id(self):
        data = self._call("set_aircraft_parameters", {
            "session_id": "nonexistent-session",
            "parameters": {"Aircraft.Wing.ASPECT_RATIO": 12.0},
        })
        assert data["success"] is False
        assert data["error_code"] == "INVALID_SESSION"
