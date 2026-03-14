#!/usr/bin/env python
"""Connection test script for the Aviary MCP server.

Standalone script (no pytest). Connects to localhost:8600 and exercises all 7 tools
in the correct lifecycle order. Prints PASS or FAIL for each tool with timing.

Usage:
    conda run -n aviary python test_aviary_mcp_connection.py
"""

import asyncio
import json
import time
import sys

from fastmcp import Client


SERVER_URL = "http://localhost:8600/mcp"

results = []


def report(tool_name, passed, elapsed, detail=""):
    status = "PASS" if passed else "FAIL"
    msg = f"  [{status}] {tool_name} ({elapsed:.2f}s)"
    if detail:
        msg += f" — {detail}"
    print(msg)
    results.append({"tool": tool_name, "passed": passed, "elapsed": elapsed})


async def main():
    print(f"Connecting to Aviary MCP server at {SERVER_URL}...\n")

    async with Client(SERVER_URL) as client:
        # --- Tool 1: get_design_space ---
        t0 = time.time()
        try:
            resp = await client.call_tool("get_design_space", {"category": "all"})
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            passed = (
                data.get("success") is True
                and isinstance(data.get("parameters"), list)
                and len(data["parameters"]) > 0
                and "coupling_note" in data
            )
            # Test category filter
            resp2 = await client.call_tool("get_design_space", {"category": "wing"})
            data2 = json.loads(resp2.content[0].text) if hasattr(resp2.content[0], "text") else resp2.content[0]
            wing_only = all(p["category"] == "wing" for p in data2.get("parameters", []))
            passed = passed and wing_only
            report("get_design_space", passed, time.time() - t0,
                   f"{len(data['parameters'])} params, category filter OK={wing_only}")
        except Exception as e:
            report("get_design_space", False, time.time() - t0, str(e))

        # --- Tool 2: create_session ---
        session_id = None
        t0 = time.time()
        try:
            resp = await client.call_tool("create_session", {})
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            session_id = data.get("session_id")
            passed = data.get("success") is True and session_id is not None
            report("create_session", passed, time.time() - t0, f"session_id={session_id}")
        except Exception as e:
            report("create_session", False, time.time() - t0, str(e))

        if session_id is None:
            print("\nCannot continue without a session. Exiting.")
            sys.exit(1)

        # --- Tool 3: set_aircraft_parameters ---
        t0 = time.time()
        try:
            resp = await client.call_tool("set_aircraft_parameters", {
                "session_id": session_id,
                "parameters": {"Aircraft.Wing.ASPECT_RATIO": 12.0},
            })
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            passed = (
                data.get("success") is True
                and isinstance(data.get("applied"), list)
                and len(data["applied"]) == 1
                and data["applied"][0]["new_value"] == 12.0
            )
            report("set_aircraft_parameters", passed, time.time() - t0,
                   f"applied={data.get('applied')}")
        except Exception as e:
            report("set_aircraft_parameters", False, time.time() - t0, str(e))

        # --- Tool 4: configure_mission ---
        t0 = time.time()
        try:
            resp = await client.call_tool("configure_mission", {
                "session_id": session_id,
                "range_nmi": 1200,
                "num_passengers": 150,
            })
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            summary = data.get("mission_summary", {})
            passed = (
                data.get("success") is True
                and summary.get("range_nmi") == 1200
                and summary.get("num_passengers") == 150
            )
            report("configure_mission", passed, time.time() - t0,
                   f"range={summary.get('range_nmi')} nmi, pax={summary.get('num_passengers')}")
        except Exception as e:
            report("configure_mission", False, time.time() - t0, str(e))

        # --- Tool 5: run_simulation ---
        t0 = time.time()
        try:
            resp = await client.call_tool("run_simulation", {
                "session_id": session_id,
                "timeout_seconds": 600,
            })
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            passed = (
                data.get("success") is True
                and "exit_code" in data
                and "runtime_seconds" in data
            )
            report("run_simulation", passed, time.time() - t0,
                   f"converged={data.get('converged')}, exit_code={data.get('exit_code')}, "
                   f"runtime={data.get('runtime_seconds')}s")
        except Exception as e:
            report("run_simulation", False, time.time() - t0, str(e))

        # --- Tool 6: get_results ---
        t0 = time.time()
        try:
            resp = await client.call_tool("get_results", {
                "session_id": session_id,
            })
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            required_fields = ["fuel_burned_kg", "gtow_kg", "wing_mass_kg",
                             "reserve_fuel_kg", "zero_fuel_weight_kg"]
            has_all = all(f in data for f in required_fields)
            passed = data.get("success") is True and has_all
            report("get_results", passed, time.time() - t0,
                   f"fuel={data.get('fuel_burned_kg')} kg, gtow={data.get('gtow_kg')} kg")
        except Exception as e:
            report("get_results", False, time.time() - t0, str(e))

        # --- Tool 7: check_constraints ---
        t0 = time.time()
        try:
            resp = await client.call_tool("check_constraints", {
                "session_id": session_id,
                "constraints": [
                    {
                        "variable": "fuel_burned_kg",
                        "operator": "<=",
                        "value": 20000,
                        "units": "kg",
                        "label": "Max fuel burn",
                    }
                ],
            })
            data = json.loads(resp.content[0].text) if hasattr(resp.content[0], "text") else resp.content[0]
            passed = (
                data.get("success") is True
                and "all_satisfied" in data
                and isinstance(data.get("results"), list)
                and len(data["results"]) > 0
                and "margin" in data["results"][0]
            )
            report("check_constraints", passed, time.time() - t0,
                   f"all_satisfied={data.get('all_satisfied')}, "
                   f"margin={data['results'][0].get('margin') if data.get('results') else 'N/A'}")
        except Exception as e:
            report("check_constraints", False, time.time() - t0, str(e))

    # Summary
    print("\n" + "=" * 50)
    n_pass = sum(1 for r in results if r["passed"])
    n_total = len(results)
    total_time = sum(r["elapsed"] for r in results)
    print(f"Results: {n_pass}/{n_total} passed ({total_time:.1f}s total)")

    if n_pass == n_total:
        print("ALL TESTS PASSED")
        sys.exit(0)
    else:
        failed = [r["tool"] for r in results if not r["passed"]]
        print(f"FAILED: {', '.join(failed)}")
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
