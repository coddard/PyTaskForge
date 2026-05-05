"""
PyTaskForge – PulseAlert Notification Dispatcher
==================================================
Dispatches notifications to external channels (Slack, Discord, generic
webhooks) after a job run completes.

Design principles:
  - Alert failures MUST NOT crash or affect the job execution flow.
  - All network calls use httpx with a short timeout.
  - Channel-specific payload formatting is encapsulated in private helpers.
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Optional

import httpx

from backend.models.database import AlertChannel, AlertPolicy, AlertTrigger, Job, RunHistory, RunStatus

logger = logging.getLogger(__name__)

# HTTP timeout for outbound alert requests (seconds)
_ALERT_HTTP_TIMEOUT: float = 10.0


def _calc_duration(run: RunHistory) -> str:
    """Return a human-readable duration string for *run*."""
    if run.started_at and run.finished_at:
        delta = run.finished_at - run.started_at
        return f"{delta.total_seconds():.1f}s"
    return "N/A"


def _build_payload(channel: AlertChannel, job: Job, run: RunHistory) -> dict:
    """Build a channel-appropriate notification payload.

    Args:
        channel: The target notification channel.
        job:     The job that was executed.
        run:     The completed run record.

    Returns:
        A dict ready for JSON serialisation.
    """
    status_emoji = {
        RunStatus.FAILED: "🔴",
        RunStatus.TIMEOUT: "⏱️",
        RunStatus.SUCCESS: "✅",
    }.get(run.status, "ℹ️")

    message = (
        f"{status_emoji} *PyTaskForge Alert*\n"
        f"Job: *{job.name}* (ID: {job.id})\n"
        f"Status: `{run.status.value.upper()}`\n"
        f"Exit Code: `{run.exit_code}`\n"
        f"Duration: `{_calc_duration(run)}`\n"
        f"Started: `{run.started_at}`"
    )

    if channel == AlertChannel.SLACK:
        return {"text": message}

    if channel == AlertChannel.DISCORD:
        return {"content": message}

    # Generic HTTP webhook / email relay
    return {
        "event": "job_alert",
        "job_id": job.id,
        "job_name": job.name,
        "status": run.status.value,
        "exit_code": run.exit_code,
        "duration": _calc_duration(run),
        "started_at": run.started_at.isoformat() if run.started_at else None,
        "message": message,
    }


async def dispatch_alert(policy: AlertPolicy, run: RunHistory, job: Job) -> None:
    """Send a notification for *run* to the channel defined in *policy*.

    This function intentionally swallows HTTP errors so alert failures
    never propagate to the job execution pipeline.

    Args:
        policy: The alert policy that triggered the notification.
        run:    The completed run record.
        job:    The parent job of the run.
    """
    payload = _build_payload(policy.channel, job, run)
    try:
        async with httpx.AsyncClient(timeout=_ALERT_HTTP_TIMEOUT) as client:
            response = await client.post(policy.target_url, json=payload)
            response.raise_for_status()
            logger.info(
                "Alert dispatched: policy_id=%s job_id=%s channel=%s status=%s",
                policy.id, job.id, policy.channel, run.status,
            )
    except httpx.HTTPStatusError as exc:
        logger.warning(
            "Alert HTTP error: policy_id=%s url=%s status=%d",
            policy.id, policy.target_url, exc.response.status_code,
        )
    except httpx.RequestError as exc:
        logger.warning(
            "Alert network error: policy_id=%s url=%s error=%s",
            policy.id, policy.target_url, exc,
        )
    except Exception as exc:
        logger.exception("Unexpected alert dispatch error: policy_id=%s %s", policy.id, exc)


async def check_and_alert(job: Job, run: RunHistory, db) -> None:
    """Evaluate all active alert policies for *job* and dispatch matches.

    Called automatically by the scheduler after every run completion.
    Network failures in :func:`dispatch_alert` do NOT propagate here.

    Args:
        job: The job that completed.
        run: The final run record (must have status, started_at, finished_at).
        db:  Active async database session (used if policies need refresh).
    """
    if not job.alert_policies:
        return

    duration_seconds: Optional[float] = None
    if run.started_at and run.finished_at:
        duration_seconds = (run.finished_at - run.started_at).total_seconds()

    for policy in job.alert_policies:
        if not policy.is_active:
            continue

        should_alert = False

        if policy.trigger == AlertTrigger.ON_FAILURE and run.status == RunStatus.FAILED:
            should_alert = True
        elif policy.trigger == AlertTrigger.ON_SUCCESS and run.status == RunStatus.SUCCESS:
            should_alert = True
        elif policy.trigger == AlertTrigger.ON_TIMEOUT and run.status == RunStatus.TIMEOUT:
            should_alert = True
        elif policy.trigger == AlertTrigger.ON_SLA_BREACH:
            if (
                policy.sla_max_duration_seconds is not None
                and duration_seconds is not None
                and duration_seconds > policy.sla_max_duration_seconds
            ):
                should_alert = True

        if should_alert:
            await dispatch_alert(policy, run, job)

