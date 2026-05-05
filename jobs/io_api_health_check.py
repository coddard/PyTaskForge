"""
PyTaskForge Sample Job – IO-Bound: Public API Health Check
==========================================================
Queries several well-known public HTTP endpoints and reports their
HTTP status code and response time. Demonstrates IO-bound concurrency
with ``urllib.request`` (stdlib only – no third-party deps required).

Expected behaviour
------------------
- Prints one result line per endpoint: [OK] or [FAIL].
- Prints a summary table on completion.
- Exits with code 0 if all checks pass, code 1 if any fail.

Scheduling recommendation
--------------------------
  trigger   : cron  (every 5 minutes: */5 * * * *)
  mode      : venv  (stdlib only)
  env vars  : REQUEST_TIMEOUT_SECONDS (default 10)
              PYTASKFORGE_RUN_ID (injected automatically)
"""
from __future__ import annotations

import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import List

RUN_ID: str = os.environ.get("PYTASKFORGE_RUN_ID", "local")
TIMEOUT: int = int(os.environ.get("REQUEST_TIMEOUT_SECONDS", "10"))

ENDPOINTS: List[str] = [
    "https://httpbin.org/status/200",
    "https://httpbin.org/get",
    "https://jsonplaceholder.typicode.com/posts/1",
    "https://api.github.com",
    "https://httpbin.org/status/404",  # intentionally non-200 to test FAIL path
]


@dataclass
class CheckResult:
    url: str
    status: int
    elapsed_ms: float
    error: str = ""

    @property
    def ok(self) -> bool:
        return 200 <= self.status < 400


def check_endpoint(url: str, timeout: int) -> CheckResult:
    """Perform a single HTTP GET and return a :class:`CheckResult`."""
    start = time.perf_counter()
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            status: int = resp.status
    except urllib.error.HTTPError as exc:
        status = exc.code
    except Exception as exc:
        elapsed = (time.perf_counter() - start) * 1000
        return CheckResult(url=url, status=0, elapsed_ms=elapsed, error=str(exc))

    elapsed = (time.perf_counter() - start) * 1000
    return CheckResult(url=url, status=status, elapsed_ms=elapsed)


def main() -> None:
    print(f"[INFO] run_id={RUN_ID}  checking {len(ENDPOINTS)} endpoint(s)", flush=True)
    results: List[CheckResult] = []

    for url in ENDPOINTS:
        result = check_endpoint(url, TIMEOUT)
        tag = "[OK  ]" if result.ok else "[FAIL]"
        print(
            f"{tag} {result.status:>3}  {result.elapsed_ms:>7.1f} ms  {result.url}"
            + (f"  ERROR: {result.error}" if result.error else ""),
            flush=True,
        )
        results.append(result)

    passed = sum(1 for r in results if r.ok)
    failed = len(results) - passed
    print(
        f"\n[SUMMARY] {passed}/{len(results)} passed, {failed} failed  "
        f"(avg {sum(r.elapsed_ms for r in results) / len(results):.1f} ms)",
        flush=True,
    )

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()

