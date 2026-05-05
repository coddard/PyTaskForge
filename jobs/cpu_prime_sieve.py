"""
PyTaskForge Sample Job – CPU-Bound: Prime Number Sieve
======================================================
Uses the Sieve of Eratosthenes to find all prime numbers up to LIMIT.
Designed to stress one CPU core for several seconds so that isolation
and resource-limit settings (Docker CPU quota) are observable.

Expected behaviour
------------------
- Prints progress lines every 10 % of the sieve sweep.
- Prints the total prime count and elapsed time on completion.
- Exits with code 0 on success.

Scheduling recommendation
--------------------------
  trigger   : interval (every 10 minutes)
  mode      : venv  (no third-party deps needed)
  env vars  : SIEVE_LIMIT (default 2_000_000)
              PYTASKFORGE_RUN_ID (injected automatically by the executor)
"""
from __future__ import annotations

import math
import os
import sys
import time

LIMIT: int = int(os.environ.get("SIEVE_LIMIT", "2000000"))
RUN_ID: str = os.environ.get("PYTASKFORGE_RUN_ID", "local")


def sieve_of_eratosthenes(limit: int) -> list[int]:
    """Return all prime numbers up to *limit* (inclusive)."""
    if limit < 2:
        return []

    is_prime = bytearray([1]) * (limit + 1)
    is_prime[0] = is_prime[1] = 0

    sqrt_limit = math.isqrt(limit)
    report_every = max(1, sqrt_limit // 10)

    for i in range(2, sqrt_limit + 1):
        if is_prime[i]:
            is_prime[i * i :: i] = bytearray(len(is_prime[i * i :: i]))

        if i % report_every == 0:
            pct = int(i / sqrt_limit * 100)
            print(f"[PROGRESS] sieve sweep {pct}% complete (i={i:,})", flush=True)

    return [n for n, flag in enumerate(is_prime) if flag]


def main() -> None:
    print(f"[INFO] run_id={RUN_ID}  limit={LIMIT:,}", flush=True)
    start = time.perf_counter()

    primes = sieve_of_eratosthenes(LIMIT)

    elapsed = time.perf_counter() - start
    print(
        f"[RESULT] Found {len(primes):,} primes up to {LIMIT:,} "
        f"in {elapsed:.3f}s  (largest={primes[-1]:,})",
        flush=True,
    )


if __name__ == "__main__":
    main()
    sys.exit(0)

