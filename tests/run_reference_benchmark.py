#!/usr/bin/env python
"""Reference benchmark run — executes Aviary coupled optimization directly via Level 2 API.

This script runs the reference optimization that produces the benchmark fuel burn number.
Results are written to logs/reference_benchmark.json.

Usage:
    conda run -n aviary python run_reference_benchmark.py
"""

import json
import time
from copy import deepcopy
from pathlib import Path

import aviary.api as av

try:
    from aviary.models.missions.height_energy_default import phase_info as default_phase_info
except ImportError:
    from aviary.interface.default_phase_info.height_energy import phase_info as default_phase_info


def main():
    print("=" * 60)
    print("Aviary Reference Benchmark Run")
    print("=" * 60)

    # Ensure logs directory exists
    logs_dir = Path(__file__).parent / "logs"
    logs_dir.mkdir(exist_ok=True)

    # Use default phase_info, only adjust target_range.
    # Cruise mach/altitude are set via aviary_inputs after load_inputs.
    phase_info = deepcopy(default_phase_info)
    phase_info["post_mission"]["target_range"] = (1500.0, "nmi")

    aircraft_csv = "models/test_aircraft/aircraft_for_bench_FwFm.csv"

    print(f"\nAircraft: {aircraft_csv}")
    print("Optimizer: SLSQP, max_iter=200")
    print("Objective: Minimize fuel burned")
    print()

    start_time = time.time()

    # Level 2 API call sequence
    prob = av.AviaryProblem(verbosity=0)

    print("Loading inputs...")
    prob.load_inputs(aircraft_csv, phase_info)

    # Set mission-level parameters via aviary_inputs
    prob.aviary_inputs.set_val(av.Mission.Design.CRUISE_ALTITUDE, 35000, units="ft")
    prob.aviary_inputs.set_val(av.Mission.Summary.CRUISE_MACH, 0.785)
    prob.aviary_inputs.set_val(av.Mission.Design.RANGE, 1500, units="nmi")

    prob.check_and_preprocess_inputs()

    print("Building model...")
    prob.add_pre_mission_systems()
    prob.add_phases()
    prob.add_post_mission_systems()
    prob.link_phases()

    print("Adding driver and design variables...")
    prob.add_driver("SLSQP", max_iter=200)
    prob.add_design_variables()

    # Add custom design variables (verified working with v0.9.10)
    prob.model.add_design_var(av.Aircraft.Engine.SCALE_FACTOR, lower=0.8, upper=1.5, ref=1.0)
    prob.model.add_design_var(av.Aircraft.Wing.AREA, lower=100.0, upper=160.0, units="m**2", ref=130.0)

    prob.add_objective()

    print("Setting up problem...")
    prob.setup()

    # Set initial guesses (REQUIRED before run_aviary_problem)
    prob.set_initial_guesses()

    print("Running optimization...")
    prob.run_aviary_problem(suppress_solver_print=True, make_plots=False)

    elapsed = time.time() - start_time

    # Extract results via prob.get_val() (run_aviary_problem returns None)
    fuel_burned_kg = float(prob.get_val(av.Mission.Summary.FUEL_BURNED, units="kg")[0])
    gross_mass_kg = float(prob.get_val(av.Mission.Summary.GROSS_MASS, units="kg")[0])
    wing_mass_kg = float(prob.get_val(av.Aircraft.Wing.MASS, units="kg")[0])

    # Convergence status from prob.driver.fail (standard OpenMDAO boolean)
    exit_code = 0 if not prob.driver.fail else 1

    # Get final design variable values
    aspect_ratio = float(prob.get_val(av.Aircraft.Wing.ASPECT_RATIO)[0])
    wing_area_m2 = float(prob.get_val(av.Aircraft.Wing.AREA, units="m**2")[0])
    sls_thrust_lbf = float(prob.get_val(av.Aircraft.Engine.SCALED_SLS_THRUST, units="lbf")[0])
    scale_factor = float(prob.get_val(av.Aircraft.Engine.SCALE_FACTOR)[0])

    # Try to get reserve fuel
    reserve_fuel_kg = None
    try:
        reserve_fuel_kg = float(prob.get_val(av.Mission.Design.RESERVE_FUEL, units="kg")[0])
    except Exception:
        pass

    results = {
        "benchmark": "aviary_reference_coupled_optimization",
        "aircraft": aircraft_csv,
        "optimizer": "SLSQP",
        "max_iter": 200,
        "runtime_seconds": round(elapsed, 2),
        "exit_code": exit_code,
        "results": {
            "fuel_burned_kg": round(fuel_burned_kg, 2),
            "gross_mass_kg": round(gross_mass_kg, 2),
            "wing_mass_kg": round(wing_mass_kg, 2),
            "reserve_fuel_kg": round(reserve_fuel_kg, 2) if reserve_fuel_kg else None,
        },
        "final_design_variables": {
            "Aircraft.Wing.ASPECT_RATIO": round(aspect_ratio, 4),
            "Aircraft.Wing.AREA_m2": round(wing_area_m2, 4),
            "Aircraft.Engine.SCALED_SLS_THRUST_lbf": round(sls_thrust_lbf, 2),
            "Aircraft.Engine.SCALE_FACTOR": round(scale_factor, 4),
        },
        "mission": {
            "range_nmi": 1500,
            "num_passengers": 162,
            "cruise_mach": 0.785,
            "cruise_altitude_ft": 35000,
        },
    }

    # Write results
    output_path = logs_dir / "reference_benchmark.json"
    with open(output_path, "w") as f:
        json.dump(results, f, indent=2)

    print("\n" + "=" * 60)
    print("RESULTS")
    print("=" * 60)
    print(f"Fuel burned:       {fuel_burned_kg:.2f} kg")
    print(f"Gross mass:        {gross_mass_kg:.2f} kg")
    print(f"Wing mass:         {wing_mass_kg:.2f} kg")
    if reserve_fuel_kg:
        print(f"Reserve fuel:      {reserve_fuel_kg:.2f} kg")
    print(f"Exit code:         {exit_code}")
    print(f"Runtime:           {elapsed:.1f} s")
    print(f"\nFinal design variables:")
    print(f"  Aspect ratio:    {aspect_ratio:.4f}")
    print(f"  Wing area:       {wing_area_m2:.4f} m^2")
    print(f"  SLS thrust:      {sls_thrust_lbf:.2f} lbf")
    print(f"  Scale factor:    {scale_factor:.4f}")
    print(f"\nResults written to: {output_path}")


if __name__ == "__main__":
    main()
