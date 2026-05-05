"""
PyTaskForge – PulseAlert Router
================================
GET    /api/jobs/{id}/alerts          →  list alert policies for a job
POST   /api/jobs/{id}/alerts          →  create an alert policy
PUT    /api/jobs/{id}/alerts/{aid}    →  update an alert policy
DELETE /api/jobs/{id}/alerts/{aid}    →  delete an alert policy
POST   /api/jobs/{id}/alerts/{aid}/test  →  send a test notification
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import TokenData, require_authenticated
from backend.models.database import (
    AlertChannel,
    AlertPolicy,
    AlertTrigger,
    Job,
    JobStatus,
    RunHistory,
    RunStatus,
    get_db,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/jobs", tags=["alerts"])


# ── Schemas ───────────────────────────────────────────────────────────────────

class AlertPolicyCreate(BaseModel):
    channel: AlertChannel
    trigger: AlertTrigger
    target_url: str
    sla_max_duration_seconds: Optional[int] = None
    is_active: bool = True


class AlertPolicyUpdate(BaseModel):
    channel: Optional[AlertChannel] = None
    trigger: Optional[AlertTrigger] = None
    target_url: Optional[str] = None
    sla_max_duration_seconds: Optional[int] = None
    is_active: Optional[bool] = None


class AlertPolicyResponse(BaseModel):
    id: int
    job_id: int
    channel: AlertChannel
    trigger: AlertTrigger
    target_url: str
    sla_max_duration_seconds: Optional[int]
    is_active: bool

    model_config = {"from_attributes": True}


# ── Helper ────────────────────────────────────────────────────────────────────

async def _get_job_or_404(job_id: int, db: AsyncSession) -> Job:
    """Fetch a job by ID or raise HTTP 404."""
    job = await db.get(Job, job_id)
    if not job or job.status == JobStatus.DELETED:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get(
    "/{job_id}/alerts",
    response_model=List[AlertPolicyResponse],
    summary="List alert policies for a job",
)
async def list_alert_policies(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[AlertPolicy]:
    await _get_job_or_404(job_id, db)
    result = await db.execute(
        select(AlertPolicy).where(AlertPolicy.job_id == job_id)
    )
    return result.scalars().all()


@router.post(
    "/{job_id}/alerts",
    response_model=AlertPolicyResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create an alert policy for a job",
)
async def create_alert_policy(
    job_id: int,
    body: AlertPolicyCreate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> AlertPolicy:
    await _get_job_or_404(job_id, db)
    policy = AlertPolicy(
        job_id=job_id,
        channel=body.channel,
        trigger=body.trigger,
        target_url=body.target_url,
        sla_max_duration_seconds=body.sla_max_duration_seconds,
        is_active=body.is_active,
    )
    db.add(policy)
    await db.commit()
    await db.refresh(policy)
    return policy


@router.put(
    "/{job_id}/alerts/{alert_id}",
    response_model=AlertPolicyResponse,
    summary="Update an alert policy",
)
async def update_alert_policy(
    job_id: int,
    alert_id: int,
    body: AlertPolicyUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> AlertPolicy:
    await _get_job_or_404(job_id, db)
    policy = await db.get(AlertPolicy, alert_id)
    if not policy or policy.job_id != job_id:
        raise HTTPException(status_code=404, detail="Alert policy not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        setattr(policy, field, value)

    await db.commit()
    await db.refresh(policy)
    return policy


@router.delete(
    "/{job_id}/alerts/{alert_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an alert policy",
)
async def delete_alert_policy(
    job_id: int,
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> None:
    await _get_job_or_404(job_id, db)
    policy = await db.get(AlertPolicy, alert_id)
    if not policy or policy.job_id != job_id:
        raise HTTPException(status_code=404, detail="Alert policy not found.")
    await db.delete(policy)
    await db.commit()


@router.post(
    "/{job_id}/alerts/{alert_id}/test",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send a test notification for an alert policy",
)
async def test_alert_policy(
    job_id: int,
    alert_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> dict:
    """Send a synthetic test notification to verify the alert policy is working."""
    from backend.services.notifier import dispatch_alert

    job = await _get_job_or_404(job_id, db)
    policy = await db.get(AlertPolicy, alert_id)
    if not policy or policy.job_id != job_id:
        raise HTTPException(status_code=404, detail="Alert policy not found.")

    # Build a synthetic run for the test notification.
    fake_run = RunHistory(
        id=0,
        job_id=job_id,
        status=RunStatus.FAILED,
        started_at=datetime.now(timezone.utc),
        finished_at=datetime.now(timezone.utc),
        exit_code=1,
        log_output="[TEST] This is a test alert notification from PyTaskForge.",
    )

    await dispatch_alert(policy=policy, run=fake_run, job=job)
    return {"detail": "Test notification dispatched.", "channel": policy.channel}

