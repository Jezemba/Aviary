#!/usr/bin/env python
"""Start script for the Aviary MCP server.

Activates the 'aviary' conda environment and launches the server.
Can be run directly: python start_aviary_server.py
"""

import subprocess
import sys
import os


def main():
    server_script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "aviary_mcp_server.py")

    # Use conda run to execute within the aviary environment
    cmd = ["conda", "run", "-n", "aviary", "--no-capture-output", "python", server_script]

    print(f"Starting Aviary MCP server via: {' '.join(cmd)}")
    print("Server will listen on http://0.0.0.0:8600/mcp")
    print("Press Ctrl+C to stop.\n")

    try:
        proc = subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        print("\nServer stopped.")
    except subprocess.CalledProcessError as e:
        print(f"Server exited with code {e.returncode}", file=sys.stderr)
        sys.exit(e.returncode)


if __name__ == "__main__":
    main()
