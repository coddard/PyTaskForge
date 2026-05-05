"""
PyTaskForge sample job – used by the test suite.

Prints a greeting to stdout and exits with code 0.
The PYTASKFORGE_RUN_ID env-var is echoed to verify injection.
"""
import os
import sys

run_id = os.environ.get("PYTASKFORGE_RUN_ID", "unknown")
print(f"Hello from PyTaskForge! run_id={run_id}")
print("Script executed successfully.", file=sys.stdout)
sys.exit(0)

