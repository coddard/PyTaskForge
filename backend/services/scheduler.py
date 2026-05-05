"""
PyTaskForge – APScheduler Integration
=======================================
Wraps APScheduler's ``AsyncIOScheduler`` and maintains a per-job
WebSocket broadcast channel so live log output can be pushed to
connected clients.

Concurrency control:
  A module-level ``asyncio.Semaphore`` limits the number of jobs that
  can execute simultaneously. The cap is configurable via the
  ``MAX_CONCURRENT_JOBS`` environment variable (default: 10).
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, Optional, Set

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.interval import IntervalTrigger
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.models.database import AsyncSessionLocal, Job, JobStatus, RunHistory, RunStatus
from backend.services.executor import get_executor

logger = logging.getLogger(__name__)

# WebSocket broadcast registry: {job_id: {send_callback, …}}
_ws_channels: Dict[int, Set[Callable]] = {}

# Concurrency guard — populated on first use (event loop must be running).
_execution_semaphore: Optional[asyncio.Semaphore] = None


def _get_semaphore() -> asyncio.Semaphore:
    """Return (or lazily create) the execution semaphore."""
    global _execution_semaphore
    if _execution_semaphore is None:
        _execution_semaphore = asyncio.Semaphore(settings.MAX_CONCURRENT_JOBS)
    return _execution_semaphore


# ── WebSocket channel helpers ─────────────────────────────────────────────────

def register_ws_channel(job_id: int, send_fn: Callable) -> None:
    """Register *send_fn* to receive live log lines for *job_id*."""
    _ws_channels.setdefault(job_id, set()).add(send_fn)


def unregister_ws_channel(job_id: int, send_fn: Callable) -> None:
    """Remove *send_fn* from the broadcast channel for *job_id*."""
    if job_id in _ws_channels:
        _ws_channels[job_id].discard(send_fn)


async def _broadcast_to_channel(job_id: int, message: str) -> None:
    """Send *message* to every WebSocket listener subscribed to *job_id*."""
    for send_fn in list(_ws_channels.get(job_id, [])):
        try:
            await send_fn(message)
        except Exception as exc:
            logger.debug("WebSocket broadcast error (job_id=%s): %s", job_id, exc)


# ── Scheduler ─────────────────────────────────────────────────────────────────

class TaskScheduler:
    """Singleton-style wrapper around :class:`AsyncIOScheduler`."""

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="UTC")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the underlying scheduler (idempotent)."""
        if not self._scheduler.running:
            self._scheduler.start()
            logger.info("Scheduler started.")

    def shutdown(self) -> None:
        """Shut down the underlying scheduler gracefully."""
        if self._scheduler.running:
            self._scheduler.shutdown(wait=False)
            logger.info("Scheduler stopped.")

    # ── Trigger factory ───────────────────────────────────────────────────────

    @staticmethod
    def _build_trigger(trigger_type: str, config: dict):
        """Construct an APScheduler trigger from *trigger_type* and *config*.

        Raises:
            ValueError: *trigger_type* is not one of ``cron``, ``interval``, ``date``.
        """
        t = trigger_type.lower()
        if t == "cron":
            return CronTrigger(**config, timezone="UTC")
        if t == "interval":
            return IntervalTrigger(**config)
        if t == "date":
            return DateTrigger(run_date=config.get("run_date"), timezone="UTC")
        raise ValueError(f"Unknown trigger type: '{trigger_type}'")

    # ── Job execution callback ────────────────────────────────────────────────

    @staticmethod
    async def _execute_job(job_id: int) -> None:
        """APScheduler callback: run one job and persist the result.

        Execution is guarded by a semaphore to prevent resource exhaustion
        when many jobs fire simultaneously.
        """
        async with _get_semaphore():
            await TaskScheduler._run_job_inner(job_id)

    @staticmethod
    async def _run_job_inner(job_id: int) -> None:
        """Core job execution logic (called under the semaphore)."""
        async with AsyncSessionLocal() as db:
            job: Optional[Job] = await db.get(Job, job_id)
            if job is None:
                logger.warning("_execute_job: job_id=%s not found – skipping.", job_id)
                return
            if job.status != JobStatus.ACTIVE:
                logger.info("_execute_job: job_id=%s is not active – skipping.", job_id)
                return

            run = RunHistory(
                job_id=job.id,
                status=RunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            db.add(run)
            await db.commit()
            await db.refresh(run)

            log_buffer: list[str] = []
            executor = get_executor(job.execution_mode, job.docker_image)

            try:
                script_path = settings.JOBS_DIR / job.script_path
                raw_env_vars: dict = json.loads(job.env_vars or "{}")

                # Resolve {{ secrets.NAME }} placeholders before execution.
                try:
                    from backend.services.secret_resolver import resolve_secrets
                    env_vars = await resolve_secrets(raw_env_vars, db, job.owner_id)
                except Exception as secret_exc:
                    logger.warning(
                        "Secret resolution failed for job_id=%s: %s", job_id, secret_exc
                    )
                    env_vars = raw_env_vars

                generator = await executor.run(
                    script_path=script_path,
                    requirements=job.requirements,
                    env_vars=env_vars,
                    timeout=job.timeout_seconds,
                )
                async for line in generator:
                    log_buffer.append(line)
                    await _broadcast_to_channel(job.id, line)

                run.status = RunStatus.SUCCESS

            except asyncio.TimeoutError:
                run.status = RunStatus.TIMEOUT
                logger.warning("job_id=%s timed out.", job_id)

            except Exception as exc:
                run.status = RunStatus.FAILED
                logger.exception("job_id=%s execution error: %s", job_id, exc)

            finally:
                await executor.cleanup()
                run.finished_at = datetime.now(timezone.utc)
                run.log_output = "".join(log_buffer)
                await db.commit()
                await _broadcast_to_channel(
                    job.id, f"[SYSTEM] Run finished with status: {run.status}\n"
                )

                # PulseAlert: dispatch notifications for this run.
                try:
                    from backend.services.notifier import check_and_alert
                    await check_and_alert(job=job, run=run, db=db)
                except Exception as alert_exc:
                    logger.warning(
                        "Alert dispatch failed for job_id=%s run_id=%s: %s",
                        job_id, run.id, alert_exc
                    )

    # ── Public scheduling API ─────────────────────────────────────────────────

    def schedule_job(self, job: Job) -> str:
        """Register *job* with APScheduler; return the APScheduler job ID."""
        config = json.loads(job.trigger_config or "{}")
        trigger = self._build_trigger(job.trigger_type, config)
        apscheduler_id = f"job_{job.id}"
        self._scheduler.add_job(
            TaskScheduler._execute_job,
            trigger=trigger,
            id=apscheduler_id,
            args=[job.id],
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Job scheduled: %s (trigger=%s)", apscheduler_id, job.trigger_type)
        return apscheduler_id

    def remove_job(self, scheduler_job_id: str) -> None:
        """Remove *scheduler_job_id* from the scheduler (best-effort)."""
        try:
            self._scheduler.remove_job(scheduler_job_id)
            logger.info("Job removed from scheduler: %s", scheduler_job_id)
        except Exception as exc:
            logger.debug("remove_job error (%s): %s", scheduler_job_id, exc)

    def pause_job(self, scheduler_job_id: str) -> None:
        """Pause *scheduler_job_id* without removing it."""
        self._scheduler.pause_job(scheduler_job_id)

    def resume_job(self, scheduler_job_id: str) -> None:
        """Resume a previously paused *scheduler_job_id*."""
        self._scheduler.resume_job(scheduler_job_id)

    async def trigger_now(self, job_id: int) -> None:
        """Execute *job_id* immediately, bypassing the schedule."""
        await TaskScheduler._execute_job(job_id)


# ── Module-level singleton ────────────────────────────────────────────────────
scheduler = TaskScheduler()

