"""
PyTaskForge Sample Job – Long-Running: Periodic Heartbeat Logger
================================================================
Emits a timestamped heartbeat line every INTERVAL_SECONDS for a
total of DURATION_SECONDS.  Purpose: validate that live WebSocket
log streaming stays connected and that logs continue to flow during
a multi-minute run.

Expected behaviour
------------------
- Prints "[BEAT] tick N / TOTAL  elapsed=Xs" every INTERVAL_SECONDS.
- Prints a final "[DONE]" summary line.
- Exits with code 0.

Scheduling recommendation
--------------------------
  trigger   : interval (every 30 minutes)
  mode      : venv  (stdlib only)
  env vars  : DURATION_SECONDS  (default 120)
              INTERVAL_SECONDS  (default 2)
              PYTASKFORGE_RUN_ID (injected automatically)
"""
from __future__ import annotations

import os
import sys
import time
from datetime import datetime, timezone

RUN_ID: str = os.environ.get("PYTASKFORGE_RUN_ID", "local")
DURATION: int = int(os.environ.get("DURATION_SECONDS", "120"))
INTERVAL: float = float(os.environ.get("INTERVAL_SECONDS", "2"))


def main() -> None:
    total_ticks = int(DURATION / INTERVAL)
    start = time.monotonic()

    print(
        f"[START] run_id={RUN_ID}  duration={DURATION}s  "
        f"interval={INTERVAL}s  total_ticks={total_ticks}",
        flush=True,
    )

    for tick in range(1, total_ticks + 1):
        elapsed = time.monotonic() - start
        now_utc = datetime.now(timezone.utc).strftime("%H:%M:%S")
        print(
            f"[BEAT]  tick {tick:>4}/{total_ticks}  "
            f"elapsed={elapsed:>6.1f}s  utc={now_utc}",
            flush=True,
        )
        time.sleep(INTERVAL)

    total_elapsed = time.monotonic() - start
    print(f"[DONE]  run_id={RUN_ID}  total_elapsed={total_elapsed:.2f}s", flush=True)


if __name__ == "__main__":
    main()
    sys.exit(0)

