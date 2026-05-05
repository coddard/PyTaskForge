"""
PyTaskForge – LiveLens Analytics Service
==========================================
Async query functions that power the operational intelligence dashboard.

All functions accept an active ``AsyncSession`` and return plain
dicts/lists ready for JSON serialisation.
"""
from __future__ import annotations

import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy import Float, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import Job, JobStatus, RunHistory, RunStatus

logger = logging.getLogger(__name__)

# ── System-level summary ──────────────────────────────────────────────────────

async def get_system_summary(db: AsyncSession) -> Dict[str, Any]:
    """Return high-level system health statistics.

    Returns:
        A dict with keys: ``active_jobs``, ``runs_last_24h``,
        ``success_count``, ``failure_count``, ``timeout_count``,
        ``failure_rate_pct``.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(hours=24)

    active_jobs_result = await db.execute(
        select(func.count(Job.id)).where(Job.status == JobStatus.ACTIVE)
    )
    active_jobs: int = active_jobs_result.scalar_one() or 0

    runs_result = await db.execute(
        select(RunHistory.status, func.count(RunHistory.id).label("cnt"))
        .where(RunHistory.started_at >= cutoff)
        .group_by(RunHistory.status)
    )
    status_counts: Dict[str, int] = {row.status: row.cnt for row in runs_result}

    success = status_counts.get(RunStatus.SUCCESS, 0)
    failed = status_counts.get(RunStatus.FAILED, 0)
    timeout = status_counts.get(RunStatus.TIMEOUT, 0)
    total = success + failed + timeout

    failure_rate = round((failed + timeout) / total * 100, 1) if total > 0 else 0.0

    return {
        "active_jobs": active_jobs,
        "runs_last_24h": total,
        "success_count": success,
        "failure_count": failed,
        "timeout_count": timeout,
        "failure_rate_pct": failure_rate,
    }


# ── Per-job execution heatmap ─────────────────────────────────────────────────

async def get_job_execution_heatmap(
    job_id: int,
    db: AsyncSession,
    days: int = 90,
) -> List[Dict[str, Any]]:
    """Return daily execution statistics for the last *days* days.

    Returns a list of dicts: ``{date, total, success, failed, timeout}``,
    one entry per calendar day. Days with no runs are included with zeros.

    Args:
        job_id: Target job ID.
        db:     Active async session.
        days:   Number of calendar days to include (default 90).
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=days)

    result = await db.execute(
        select(
            func.date(RunHistory.started_at).label("run_date"),
            func.count(RunHistory.id).label("total"),
            func.sum(case((RunHistory.status == RunStatus.SUCCESS, 1), else_=0)).label("success"),
            func.sum(case((RunHistory.status == RunStatus.FAILED, 1), else_=0)).label("failed"),
            func.sum(case((RunHistory.status == RunStatus.TIMEOUT, 1), else_=0)).label("timeout"),
        )
        .where(RunHistory.job_id == job_id, RunHistory.started_at >= cutoff)
        .group_by(func.date(RunHistory.started_at))
        .order_by(func.date(RunHistory.started_at))
    )

    rows = {
        row.run_date: {
            "date": str(row.run_date),
            "total": row.total or 0,
            "success": int(row.success or 0),
            "failed": int(row.failed or 0),
            "timeout": int(row.timeout or 0),
        }
        for row in result
    }

    # Fill in zero entries for days with no runs.
    heatmap: List[Dict[str, Any]] = []
    today = datetime.now(timezone.utc).date()
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        day_str = str(day)
        heatmap.append(
            rows.get(
                day_str,
                {"date": day_str, "total": 0, "success": 0, "failed": 0, "timeout": 0},
            )
        )
    return heatmap


# ── Per-job duration trend ─────────────────────────────────────────────────────

async def get_job_duration_trend(
    job_id: int,
    db: AsyncSession,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    """Return execution duration for the last *limit* completed runs.

    Args:
        job_id: Target job ID.
        db:     Active async session.
        limit:  Maximum number of recent runs to return.

    Returns:
        List of ``{run_id, started_at, duration_seconds, status}`` dicts.
    """
    result = await db.execute(
        select(RunHistory)
        .where(
            RunHistory.job_id == job_id,
            RunHistory.started_at.isnot(None),
            RunHistory.finished_at.isnot(None),
        )
        .order_by(RunHistory.id.desc())
        .limit(limit)
    )

    records = []
    for run in reversed(result.scalars().all()):
        duration = (run.finished_at - run.started_at).total_seconds()
        records.append({
            "run_id": run.id,
            "started_at": run.started_at.isoformat() if run.started_at else None,
            "duration_seconds": round(duration, 2),
            "status": run.status.value,
        })
    return records


# ── Anomaly detection ─────────────────────────────────────────────────────────

async def get_anomalous_jobs(
    db: AsyncSession,
    z_score_threshold: float = 2.0,
    min_runs: int = 5,
) -> List[Dict[str, Any]]:
    """Return jobs whose most recent run duration is anomalously long.

    A job is flagged when its last run duration exceeds
    ``mean + z_score_threshold × std_dev`` of its historical runs.

    Args:
        db:                Active async session.
        z_score_threshold: Standard deviations above the mean to flag (default 2.0).
        min_runs:          Minimum number of historical runs required to compute statistics.

    Returns:
        List of ``{job_id, job_name, last_duration, mean_duration, std_dev, z_score}`` dicts,
        sorted by z-score descending.
    """
    # Fetch all jobs with at least min_runs completed runs.
    jobs_result = await db.execute(
        select(Job).where(Job.status == JobStatus.ACTIVE)
    )
    active_jobs = jobs_result.scalars().all()

    anomalies = []

    for job in active_jobs:
        runs_result = await db.execute(
            select(RunHistory)
            .where(
                RunHistory.job_id == job.id,
                RunHistory.started_at.isnot(None),
                RunHistory.finished_at.isnot(None),
            )
            .order_by(RunHistory.id.desc())
            .limit(100)  # cap analysis window
        )
        runs = runs_result.scalars().all()

        if len(runs) < min_runs:
            continue

        durations = [
            (r.finished_at - r.started_at).total_seconds()
            for r in runs
            if r.started_at and r.finished_at
        ]

        if not durations:
            continue

        mean = sum(durations) / len(durations)
        variance = sum((d - mean) ** 2 for d in durations) / len(durations)
        std_dev = math.sqrt(variance)

        last_duration = durations[0]  # most recent run is first

        if std_dev == 0:
            continue  # all runs took the same time — no anomaly possible

        z_score = (last_duration - mean) / std_dev
        if z_score >= z_score_threshold:
            anomalies.append({
                "job_id": job.id,
                "job_name": job.name,
                "last_duration_seconds": round(last_duration, 2),
                "mean_duration_seconds": round(mean, 2),
                "std_dev_seconds": round(std_dev, 2),
                "z_score": round(z_score, 2),
            })

    return sorted(anomalies, key=lambda x: x["z_score"], reverse=True)

