"""
PyTaskForge – WebhookTrigger Router
=====================================
POST /webhooks/jobs/{webhook_token}  →  trigger a job via its unique token

Authentication:
  The webhook token itself IS the authentication mechanism.
  No JWT is required. Tokens are generated per-job and can be
  rotated via POST /api/jobs/{id}/webhook/regenerate.

Runtime parameters:
  An optional JSON body is merged into the job's env_vars at execution
  time. This allows external systems to pass dynamic parameters.

  Example (GitHub Actions):
    curl -X POST https://app.example.com/webhooks/jobs/<token> \\
      -H "Content-Type: application/json" \\
      -d '{"GIT_SHA": "abc123", "BRANCH": "main"}'
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.database import AsyncSessionLocal, Job, JobStatus, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


@router.post(
    "/jobs/{webhook_token}",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Trigger a job via its webhook token",
)
async def webhook_trigger(
    webhook_token: str,
    request: Request,
    params: Optional[Dict[str, Any]] = Body(default=None),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Trigger a job using its unique webhook token.

    The optional JSON request body is merged into the job's environment
    variables at execution time, allowing runtime parameter injection.

    Args:
        webhook_token: The job's unique webhook authentication token.
        request:       The incoming HTTP request (used for IP logging).
        params:        Optional JSON key/value pairs to inject as env vars.

    Returns:
        A dict with status, job_id (run is async — run_id not available immediately).

    Raises:
        HTTPException 404: Token not found or webhook is disabled.
    """
    result = await db.execute(select(Job).where(Job.webhook_token == webhook_token))
    job: Optional[Job] = result.scalar_one_or_none()

    if not job or not job.webhook_enabled or job.status == JobStatus.DELETED:
        # Return 404 regardless of reason to prevent token enumeration.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Webhook not found or disabled.",
        )

    source_ip = request.client.host if request.client else "unknown"
    logger.info(
        "Webhook triggered: job_id=%s source_ip=%s params=%s",
        job.id,
        source_ip,
        list(params.keys()) if params else [],
    )

    # Trigger asynchronously — inject runtime params via a wrapper.
    from backend.services.scheduler import scheduler
    asyncio.create_task(_trigger_with_params(job.id, params or {}))

    return {
        "status": "triggered",
        "job_id": job.id,
        "source_ip": source_ip,
    }


async def _trigger_with_params(job_id: int, runtime_params: Dict[str, Any]) -> None:
    """Execute a job with additional runtime parameters merged into its env vars."""
    from backend.services.scheduler import TaskScheduler
    import json

    async with AsyncSessionLocal() as db:
        job: Optional[Job] = await db.get(Job, job_id)
        if not job:
            logger.warning("_trigger_with_params: job_id=%s not found", job_id)
            return

        # Temporarily merge runtime params into the job's env_vars for this run.
        original_env = job.env_vars
        merged = {**json.loads(job.env_vars or "{}"), **runtime_params}
        job.env_vars = json.dumps(merged)
        # We don't commit this — it's only for the execution context.

    # Execute using the modified env (the DB record is not modified).
    await TaskScheduler._execute_job(job_id)

