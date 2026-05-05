"""
PyTaskForge – LiveLens Analytics Router
========================================
GET /api/analytics/summary              →  system-wide health stats
GET /api/analytics/jobs/{id}/heatmap    →  90-day execution heatmap
GET /api/analytics/jobs/{id}/durations  →  duration trend for last 50 runs
GET /api/analytics/anomalies            →  jobs with anomalous recent behaviour
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import TokenData, require_authenticated
from backend.models.database import get_db
from backend.services.analytics import (
    get_anomalous_jobs,
    get_job_duration_trend,
    get_job_execution_heatmap,
    get_system_summary,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/analytics", tags=["analytics"])


@router.get("/summary", summary="System-wide health statistics")
async def analytics_summary(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> Dict[str, Any]:
    """Return aggregate job execution statistics for the last 24 hours."""
    return await get_system_summary(db)


@router.get(
    "/jobs/{job_id}/heatmap",
    summary="90-day execution heatmap for a job",
)
async def job_heatmap(
    job_id: int,
    days: int = Query(90, ge=7, le=365, description="Number of calendar days"),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[Dict[str, Any]]:
    """Return daily run counts and success/failure breakdown for the last *days* days."""
    return await get_job_execution_heatmap(job_id, db, days=days)


@router.get(
    "/jobs/{job_id}/durations",
    summary="Duration trend for the last N runs of a job",
)
async def job_duration_trend(
    job_id: int,
    limit: int = Query(50, ge=5, le=200, description="Number of recent runs"),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[Dict[str, Any]]:
    """Return execution duration time-series for the last *limit* completed runs."""
    return await get_job_duration_trend(job_id, db, limit=limit)


@router.get("/anomalies", summary="Jobs with anomalously long recent runs")
async def anomaly_feed(
    z_threshold: float = Query(
        2.0, ge=1.0, le=5.0,
        description="Z-score threshold to flag a run as anomalous",
    ),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[Dict[str, Any]]:
    """Return jobs whose most recent run duration exceeds *z_threshold* standard
    deviations above their historical mean.
    """
    return await get_anomalous_jobs(db, z_score_threshold=z_threshold)

