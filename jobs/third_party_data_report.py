"""
PyTaskForge Sample Job – Third-Party Libraries: Data Report
============================================================
Fetches a small dataset from a public REST API, loads it into a
``pandas`` DataFrame, and prints a statistical summary.

Demonstrates that PyTaskForge can install arbitrary third-party
packages via a per-job requirements.txt **before** running the script.

Requirements (stored in this job's requirements field in the UI):
    requests==2.31.0
    pandas==2.2.2

Expected behaviour
------------------
- Pip installs requests + pandas inside the isolated venv (logged live).
- Downloads JSON from JSONPlaceholder /posts.
- Prints DataFrame info and descriptive statistics to stdout.
- Exits with code 0.

Scheduling recommendation
--------------------------
  trigger   : cron  (daily at 06:00 – "0 6 * * *")
  mode      : venv  (requirements injected via the job's requirements field)
  env vars  : DATA_API_URL  (default https://jsonplaceholder.typicode.com/posts)
              MAX_ROWS      (default 100)
              PYTASKFORGE_RUN_ID (injected automatically)
"""
from __future__ import annotations

import os
import sys

RUN_ID: str = os.environ.get("PYTASKFORGE_RUN_ID", "local")
API_URL: str = os.environ.get("DATA_API_URL", "https://jsonplaceholder.typicode.com/posts")
MAX_ROWS: int = int(os.environ.get("MAX_ROWS", "100"))


def main() -> None:
    # Imported here so the venv executor has time to install them first.
    import requests  # type: ignore
    import pandas as pd  # type: ignore

    print(f"[INFO] run_id={RUN_ID}  fetching up to {MAX_ROWS} records from {API_URL}", flush=True)

    response = requests.get(API_URL, timeout=15)
    response.raise_for_status()

    data = response.json()[:MAX_ROWS]
    df = pd.DataFrame(data)

    print(f"\n[INFO] Dataset shape: {df.shape[0]} rows × {df.shape[1]} columns", flush=True)
    print("\n[INFO] Column dtypes:", flush=True)
    print(df.dtypes.to_string(), flush=True)

    if "userId" in df.columns:
        print("\n[STATS] Posts per userId:", flush=True)
        print(df["userId"].value_counts().to_string(), flush=True)

    if "title" in df.columns:
        title_lengths = df["title"].str.len()
        print(f"\n[STATS] Title length – mean={title_lengths.mean():.1f}  "
              f"min={title_lengths.min()}  max={title_lengths.max()}", flush=True)

    print("\n[RESULT] Data report completed successfully.", flush=True)


if __name__ == "__main__":
    main()
    sys.exit(0)

