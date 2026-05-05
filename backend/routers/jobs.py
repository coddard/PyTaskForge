"""
PyTaskForge – Jobs Router
==========================
GET    /api/jobs              →  list all non-deleted jobs (paginated)
POST   /api/jobs              →  create a new job
GET    /api/jobs/{id}         →  get job details
PUT    /api/jobs/{id}         →  update a job
DELETE /api/jobs/{id}         →  soft-delete a job
POST   /api/jobs/{id}/run     →  trigger a job immediately
GET    /api/jobs/{id}/history →  fetch run records (paginated)
POST   /api/jobs/{id}/webhook/regenerate  →  rotate the webhook token
"""
from __future__ import annotations

import json
import secrets
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import TokenData, require_authenticated
from backend.models.database import (
    ExecutionMode,
    Job,
    JobStatus,
    RunHistory,
    TriggerType,
    get_db,
)
from backend.services.scheduler import scheduler

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


# ── Request / response schemas ────────────────────────────────────────────────

class JobCreate(BaseModel):
    name: str
    description: Optional[str] = None
    script_path: str
    execution_mode: ExecutionMode = ExecutionMode.VENV
    docker_image: Optional[str] = None
    trigger_type: TriggerType
    trigger_config: dict = {}
    requirements: Optional[str] = None
    env_vars: dict = {}
    timeout_seconds: Optional[int] = None

    @field_validator("trigger_config", "env_vars", mode="before")
    @classmethod
    def _parse_json_string(cls, value):
        return json.loads(value) if isinstance(value, str) else value

    @model_validator(mode="after")
    def _validate_trigger_config(self) -> "JobCreate":
        """Validate that trigger_config contains the required keys for trigger_type."""
        _validate_trigger(self.trigger_type, self.trigger_config)
        return self


class JobUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    script_path: Optional[str] = None
    execution_mode: Optional[ExecutionMode] = None
    docker_image: Optional[str] = None
    trigger_type: Optional[TriggerType] = None
    trigger_config: Optional[dict] = None
    requirements: Optional[str] = None
    env_vars: Optional[dict] = None
    timeout_seconds: Optional[int] = None
    status: Optional[JobStatus] = None

    @model_validator(mode="after")
    def _validate_trigger_config(self) -> "JobUpdate":
        """Validate trigger_config when both trigger_type and trigger_config are present."""
        if self.trigger_type is not None and self.trigger_config is not None:
            _validate_trigger(self.trigger_type, self.trigger_config)
        return self


class JobResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    script_path: str
    status: JobStatus
    execution_mode: ExecutionMode
    docker_image: Optional[str]
    trigger_type: TriggerType
    trigger_config: dict
    env_vars: dict
    timeout_seconds: Optional[int]
    scheduler_job_id: Optional[str]
    webhook_enabled: bool = False
    webhook_token: Optional[str] = None

    model_config = {"from_attributes": True}

    @field_validator("trigger_config", "env_vars", mode="before")
    @classmethod
    def _coerce_json(cls, value):
        if isinstance(value, str):
            return json.loads(value)
        return value or {}


# ── Trigger validation helper ─────────────────────────────────────────────────

def _validate_trigger(trigger_type: TriggerType, config: dict) -> None:
    """Raise ValueError if *config* is invalid for *trigger_type*.

    Args:
        trigger_type: The scheduling trigger type.
        config: The trigger configuration dictionary.

    Raises:
        ValueError: The config is missing required keys for the given trigger type.
    """
    CRON_FIELDS = {"year", "month", "day", "week", "day_of_week", "hour", "minute", "second"}
    INTERVAL_FIELDS = {"weeks", "days", "hours", "minutes", "seconds"}

    if trigger_type == TriggerType.CRON:
        if not config:
            raise ValueError(
                "Cron trigger_config must contain at least one field: "
                f"{sorted(CRON_FIELDS)}. Example: {{\"hour\": \"9\", \"minute\": \"0\"}}"
            )
        invalid = set(config.keys()) - CRON_FIELDS
        if invalid:
            raise ValueError(
                f"Invalid cron trigger fields: {invalid}. "
                f"Allowed fields: {sorted(CRON_FIELDS)}"
            )

    elif trigger_type == TriggerType.INTERVAL:
        valid_interval_keys = set(config.keys()) & INTERVAL_FIELDS
        if not valid_interval_keys:
            raise ValueError(
                "Interval trigger_config must contain at least one of: "
                f"{sorted(INTERVAL_FIELDS)}. Example: {{\"minutes\": 30}}"
            )

    elif trigger_type == TriggerType.DATE:
        if "run_date" not in config:
            raise ValueError(
                "Date trigger_config must contain 'run_date'. "
                "Example: {\"run_date\": \"2026-12-31T09:00:00\"}"
            )


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.get("", response_model=List[JobResponse], summary="List all active jobs")
async def list_jobs(
    limit: int = Query(50, ge=1, le=500, description="Maximum number of jobs to return"),
    offset: int = Query(0, ge=0, description="Number of jobs to skip"),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[Job]:
    result = await db.execute(
        select(Job)
        .where(Job.status != JobStatus.DELETED)
        .offset(offset)
        .limit(limit)
    )
    return result.scalars().all()


@router.post(
    "",
    response_model=JobResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new scheduled job",
)
async def create_job(
    body: JobCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenData = Depends(require_authenticated),
) -> Job:
    job = Job(
        name=body.name,
        description=body.description,
        script_path=body.script_path,
        execution_mode=body.execution_mode,
        docker_image=body.docker_image,
        trigger_type=body.trigger_type,
        trigger_config=json.dumps(body.trigger_config),
        requirements=body.requirements,
        env_vars=json.dumps(body.env_vars),
        timeout_seconds=body.timeout_seconds,
        owner_id=token.user_id,
        status=JobStatus.ACTIVE,
        webhook_token=secrets.token_urlsafe(32),
        webhook_enabled=False,
    )
    db.add(job)
    await db.commit()
    await db.refresh(job)

    try:
        sched_id = scheduler.schedule_job(job)
        job.scheduler_job_id = sched_id
        await db.commit()
        await db.refresh(job)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Failed to schedule job: {exc}",
        ) from exc

    return job


@router.get("/{job_id}", response_model=JobResponse, summary="Get a single job")
async def get_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> Job:
    job = await db.get(Job, job_id)
    if not job or job.status == JobStatus.DELETED:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@router.put("/{job_id}", response_model=JobResponse, summary="Update a job")
async def update_job(
    job_id: int,
    body: JobUpdate,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> Job:
    job = await db.get(Job, job_id)
    if not job or job.status == JobStatus.DELETED:
        raise HTTPException(status_code=404, detail="Job not found.")

    for field, value in body.model_dump(exclude_none=True).items():
        if field in ("trigger_config", "env_vars") and isinstance(value, dict):
            value = json.dumps(value)
        setattr(job, field, value)

    if job.scheduler_job_id:
        scheduler.remove_job(job.scheduler_job_id)
    if job.status == JobStatus.ACTIVE:
        try:
            job.scheduler_job_id = scheduler.schedule_job(job)
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=f"Failed to reschedule job: {exc}",
            ) from exc

    await db.commit()
    await db.refresh(job)
    return job


@router.delete(
    "/{job_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Soft-delete a job",
)
async def delete_job(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> None:
    job = await db.get(Job, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    if job.scheduler_job_id:
        scheduler.remove_job(job.scheduler_job_id)
    job.status = JobStatus.DELETED
    await db.commit()


@router.post(
    "/{job_id}/run",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a job immediately",
)
async def run_job_now(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> dict:
    """Fire the job right now without waiting for the next scheduled trigger."""
    job = await db.get(Job, job_id)
    if not job or job.status == JobStatus.DELETED:
        raise HTTPException(status_code=404, detail="Job not found.")

    import asyncio
    asyncio.create_task(scheduler.trigger_now(job_id))
    return {"detail": f"Job {job_id} has been triggered."}


@router.get(
    "/{job_id}/history",
    response_model=List[dict],
    summary="Get run records for a job (paginated)",
)
async def get_run_history(
    job_id: int,
    limit: int = Query(50, ge=1, le=500, description="Maximum number of records to return"),
    offset: int = Query(0, ge=0, description="Number of records to skip"),
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[dict]:
    result = await db.execute(
        select(RunHistory)
        .where(RunHistory.job_id == job_id)
        .order_by(RunHistory.id.desc())
        .limit(limit)
        .offset(offset)
    )
    return [
        {
            "id": r.id,
            "status": r.status,
            "started_at": r.started_at,
            "finished_at": r.finished_at,
            "exit_code": r.exit_code,
            "log_output": r.log_output,
        }
        for r in result.scalars().all()
    ]


@router.post(
    "/{job_id}/webhook/regenerate",
    summary="Regenerate the webhook token for a job",
)
async def regenerate_webhook_token(
    job_id: int,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> dict:
    """Generate a new webhook token, immediately invalidating the old one."""
    job = await db.get(Job, job_id)
    if not job or job.status == JobStatus.DELETED:
        raise HTTPException(status_code=404, detail="Job not found.")

    job.webhook_token = secrets.token_urlsafe(32)
    await db.commit()
    return {"detail": "Webhook token regenerated.", "webhook_token": job.webhook_token}

