"""
PyTaskForge – FastAPI Application Entry Point
==============================================
Registers all routers, middleware, WebSocket endpoints,
and the application lifespan (startup / shutdown hooks).

WebSocket endpoint:
  ws://host/ws/jobs/{job_id}/logs  →  live log stream

Dev-mode warning:
  Every HTTP response includes an X-Dev-Mode-Warning header when
  PTF_DEV_MODE=true is set in the environment.
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address

from backend.core.config import settings
from backend.core.security import decode_token
from backend.models.database import init_db
from backend.routers import auth, jobs
from backend.services.scheduler import (
    scheduler,
    register_ws_channel,
    unregister_ws_channel,
)

# ── Logging setup ─────────────────────────────────────────────────────────────

def _configure_logging() -> None:
    """Configure root logger with either JSON or plain-text format."""
    log_level = logging.DEBUG if settings.DEBUG else logging.INFO

    if settings.LOG_FORMAT.lower() == "json":
        try:
            from pythonjsonlogger import jsonlogger  # type: ignore

            handler = logging.StreamHandler()
            formatter = jsonlogger.JsonFormatter(
                fmt="%(asctime)s %(levelname)s %(name)s %(message)s"
            )
            handler.setFormatter(formatter)
            logging.basicConfig(level=log_level, handlers=[handler])
        except ImportError:
            # Graceful fallback if python-json-logger is not installed.
            logging.basicConfig(
                level=log_level,
                format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
            )
            logging.getLogger(__name__).warning(
                "python-json-logger not installed; falling back to text logging."
            )
    else:
        logging.basicConfig(
            level=log_level,
            format="%(asctime)s  %(levelname)-8s  %(name)s – %(message)s",
        )


_configure_logging()
logger = logging.getLogger(__name__)

_DEV_MODE_WARNING = (
    "PTF_DEV_MODE is ACTIVE - Authentication is DISABLED. "
    "Do NOT use in production!"
)

# ── Rate limiter ──────────────────────────────────────────────────────────────
limiter = Limiter(key_func=get_remote_address)


# ── Lifespan ──────────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application startup and shutdown hooks."""
    logger.info("Starting %s v%s …", settings.APP_NAME, settings.APP_VERSION)

    if settings.PTF_DEV_MODE:
        logger.warning("!!! %s !!!", _DEV_MODE_WARNING)

    await init_db()
    scheduler.start()
    await _reload_active_jobs()

    yield  # application is running

    scheduler.shutdown()
    logger.info("%s stopped.", settings.APP_NAME)


async def _reload_active_jobs() -> None:
    """Re-register all active jobs with the scheduler on startup."""
    from sqlalchemy import select
    from backend.models.database import AsyncSessionLocal, Job, JobStatus

    async with AsyncSessionLocal() as db:
        result = await db.execute(select(Job).where(Job.status == JobStatus.ACTIVE))
        active_jobs = result.scalars().all()
        for job in active_jobs:
            try:
                sched_id = scheduler.schedule_job(job)
                job.scheduler_job_id = sched_id
            except Exception as exc:
                logger.warning("Could not schedule job id=%s: %s", job.id, exc)
        await db.commit()

    logger.info("%d active job(s) loaded into scheduler.", len(active_jobs))


# ── Application instance ──────────────────────────────────────────────────────

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description=(
        "Schedule, manage, and monitor Python scripts "
        "running in isolated environments."
    ),
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

# ── Rate limiter middleware & error handler ───────────────────────────────────
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
app.add_middleware(SlowAPIMiddleware)

# ── CORS ──────────────────────────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Dev-mode middleware ───────────────────────────────────────────────────────

@app.middleware("http")
async def inject_dev_mode_warning(request: Request, call_next):
    """Append a plain-ASCII warning header on every response in dev mode."""
    response = await call_next(request)
    if settings.PTF_DEV_MODE:
        response.headers["X-Dev-Mode-Warning"] = _DEV_MODE_WARNING
    return response


# ── Routers ───────────────────────────────────────────────────────────────────

app.include_router(auth.router)
app.include_router(jobs.router)

# VaultGuard secrets router
from backend.routers import secrets as secrets_router  # noqa: E402
app.include_router(secrets_router.router)

# WebhookTrigger router
from backend.routers import webhooks as webhooks_router  # noqa: E402
app.include_router(webhooks_router.router)

# PulseAlert router
from backend.routers import alerts as alerts_router  # noqa: E402
app.include_router(alerts_router.router)

# Analytics / LiveLens router
from backend.routers import analytics as analytics_router  # noqa: E402
app.include_router(analytics_router.router)

# JobFlow pipelines router
from backend.routers import pipelines as pipelines_router  # noqa: E402
app.include_router(pipelines_router.router)


# ── Health check ─────────────────────────────────────────────────────────────

@app.get("/api/health", tags=["system"])
async def health_check():
    """Return basic service health information."""
    return {
        "status": "ok",
        "app": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "dev_mode": settings.PTF_DEV_MODE,
        "scheduler_running": scheduler._scheduler.running,
    }


# ── WebSocket: live log stream ────────────────────────────────────────────────

@app.websocket("/ws/jobs/{job_id}/logs")
async def job_log_stream(websocket: WebSocket, job_id: int) -> None:
    """Stream live log output for a running job over WebSocket.

    Authentication:
      Normal mode: pass ``?token=<JWT>`` as a query parameter.
      Dev mode:    no token required (a warning message is sent instead).
    """
    if not settings.PTF_DEV_MODE:
        token: Optional[str] = websocket.query_params.get("token")
        if not token:
            await websocket.close(code=4001, reason="Token required.")
            return
        try:
            decode_token(token)
        except Exception:
            await websocket.close(code=4003, reason="Invalid token.")
            return

    await websocket.accept()
    logger.info("WebSocket connected: job_id=%s", job_id)

    if settings.PTF_DEV_MODE:
        await websocket.send_text(f"[WARN] {_DEV_MODE_WARNING}\n")

    async def _send(message: str) -> None:
        await websocket.send_text(message)

    register_ws_channel(job_id, _send)

    try:
        await websocket.send_text(
            f"[INFO] Connected to log channel for job_id={job_id}. "
            "Output will appear here when the job fires.\n"
        )
        # Keep the connection alive; handle ping/pong from the client
        while True:
            data = await websocket.receive_text()
            if data.strip() == "ping":
                await websocket.send_text("pong")
    except WebSocketDisconnect:
        logger.info("WebSocket disconnected: job_id=%s", job_id)
    finally:
        unregister_ws_channel(job_id, _send)


# ── Static frontend ───────────────────────────────────────────────────────────

_frontend_dist = os.path.join(os.path.dirname(__file__), "..", "frontend", "dist")
if os.path.isdir(_frontend_dist):
    app.mount("/", StaticFiles(directory=_frontend_dist, html=True), name="frontend")
    logger.info("Serving frontend from: %s", _frontend_dist)


# ── Global error handlers ─────────────────────────────────────────────────────

@app.exception_handler(404)
async def not_found_handler(request: Request, exc):
    if request.url.path.startswith("/api/") or request.url.path.startswith("/ws/"):
        return JSONResponse(status_code=404, content={"detail": "Resource not found."})
    return JSONResponse(status_code=404, content={"detail": "Page not found."})


@app.exception_handler(500)
async def internal_error_handler(request: Request, exc):
    logger.exception("Internal server error: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check the server logs."},
    )

