"""
PyTaskForge – Database Models
==============================
SQLAlchemy 2.x Declarative API with async (aiosqlite) support.

Tables:
  users        – System accounts
  jobs         – Scheduled task definitions
  run_history  – Per-execution audit log
  secrets      – VaultGuard encrypted secrets
  alert_policies – PulseAlert notification rules
  pipelines    – JobFlow DAG pipeline definitions
  pipeline_edges – JobFlow dependency edges
"""
import enum
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

from backend.core.config import settings

# ── Engine & Session factory ──────────────────────────────────────────────────

engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.DEBUG,
    future=True,
    connect_args={"check_same_thread": False},  # required for SQLite
)

AsyncSessionLocal: sessionmaker = sessionmaker(  # type: ignore[type-arg]
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncSession:  # type: ignore[override]
    """FastAPI dependency that yields a managed async DB session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


# ── Declarative base ──────────────────────────────────────────────────────────

class Base(AsyncAttrs, DeclarativeBase):
    """Shared base class for all ORM models."""
    pass


# ── Domain enumerations ───────────────────────────────────────────────────────

class JobStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    DELETED = "deleted"


class ExecutionMode(str, enum.Enum):
    VENV = "venv"
    DOCKER = "docker"


class TriggerType(str, enum.Enum):
    CRON = "cron"
    INTERVAL = "interval"
    DATE = "date"


class RunStatus(str, enum.Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"
    TIMEOUT = "timeout"


class AlertChannel(str, enum.Enum):
    SLACK = "slack"
    DISCORD = "discord"
    EMAIL = "email"
    WEBHOOK = "webhook"


class AlertTrigger(str, enum.Enum):
    ON_FAILURE = "on_failure"
    ON_SUCCESS = "on_success"
    ON_TIMEOUT = "on_timeout"
    ON_SLA_BREACH = "on_sla_breach"


class PipelineStatus(str, enum.Enum):
    ACTIVE = "active"
    PAUSED = "paused"
    ARCHIVED = "archived"


class EdgeCondition(str, enum.Enum):
    SUCCESS = "success"
    FAILURE = "failure"
    ALWAYS = "always"


# ── ORM models ────────────────────────────────────────────────────────────────

class User(Base):
    """System user account."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False, index=True)
    email: Mapped[str] = mapped_column(String(128), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(128), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    is_superuser: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
    )

    jobs: Mapped[List["Job"]] = relationship("Job", back_populates="owner", lazy="selectin")

    def __repr__(self) -> str:
        return f"<User id={self.id} username={self.username!r}>"


class Job(Base):
    """Scheduled task definition."""

    __tablename__ = "jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Path to the script file, relative to the jobs/ directory
    script_path: Mapped[str] = mapped_column(String(512), nullable=False)

    status: Mapped[JobStatus] = mapped_column(
        Enum(JobStatus), default=JobStatus.ACTIVE, nullable=False
    )

    execution_mode: Mapped[ExecutionMode] = mapped_column(
        Enum(ExecutionMode), default=ExecutionMode.VENV, nullable=False
    )
    docker_image: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)

    trigger_type: Mapped[TriggerType] = mapped_column(Enum(TriggerType), nullable=False)

    # JSON-serialised trigger kwargs.
    # cron example    → {"hour": "9", "minute": "0"}
    # interval example → {"seconds": 30}
    # date example    → {"run_date": "2025-01-01T09:00:00"}
    trigger_config: Mapped[str] = mapped_column(Text, nullable=False, default="{}")

    # pip requirements.txt content (venv mode)
    requirements: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # JSON-serialised environment variable overrides
    env_vars: Mapped[Optional[str]] = mapped_column(Text, nullable=True, default="{}")

    # Execution wall-clock limit in seconds; None = unlimited
    timeout_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Reference to the APScheduler job ID
    scheduler_job_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    # WebhookTrigger fields
    webhook_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    webhook_token: Mapped[Optional[str]] = mapped_column(
        String(64), unique=True, nullable=True, index=True
    )

    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    owner: Mapped[Optional["User"]] = relationship("User", back_populates="jobs")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    run_histories: Mapped[List["RunHistory"]] = relationship(
        "RunHistory", back_populates="job", cascade="all, delete-orphan", lazy="noload"
    )
    alert_policies: Mapped[List["AlertPolicy"]] = relationship(
        "AlertPolicy", back_populates="job", cascade="all, delete-orphan", lazy="selectin"
    )

    def __repr__(self) -> str:
        return f"<Job id={self.id} name={self.name!r} status={self.status}>"


class RunHistory(Base):
    """Single execution record for a job."""

    __tablename__ = "run_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job: Mapped["Job"] = relationship("Job", back_populates="run_histories")

    status: Mapped[RunStatus] = mapped_column(
        Enum(RunStatus), default=RunStatus.PENDING, nullable=False
    )

    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    exit_code: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # Combined stdout + stderr output (may be large)
    log_output: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Execution context (container ID, venv path, …)
    execution_info: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    def __repr__(self) -> str:
        return f"<RunHistory id={self.id} job_id={self.job_id} status={self.status}>"


# ── VaultGuard: Encrypted Secrets ─────────────────────────────────────────────

class Secret(Base):
    """AES-256 encrypted named secret for use in job environment variables."""

    __tablename__ = "secrets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    encrypted_value: Mapped[str] = mapped_column(Text, nullable=False)
    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def __repr__(self) -> str:
        return f"<Secret id={self.id} name={self.name!r}>"


# ── PulseAlert: Alert Policies ────────────────────────────────────────────────

class AlertPolicy(Base):
    """Notification policy attached to a job."""

    __tablename__ = "alert_policies"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    channel: Mapped[AlertChannel] = mapped_column(Enum(AlertChannel), nullable=False)
    trigger: Mapped[AlertTrigger] = mapped_column(Enum(AlertTrigger), nullable=False)
    target_url: Mapped[str] = mapped_column(String(1024), nullable=False)
    sla_max_duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    job: Mapped["Job"] = relationship("Job", back_populates="alert_policies")

    def __repr__(self) -> str:
        return f"<AlertPolicy id={self.id} job_id={self.job_id} trigger={self.trigger}>"


# ── JobFlow: Pipeline Models ───────────────────────────────────────────────────

class Pipeline(Base):
    """DAG pipeline definition — an ordered set of dependent jobs."""

    __tablename__ = "pipelines"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[PipelineStatus] = mapped_column(
        Enum(PipelineStatus), default=PipelineStatus.ACTIVE, nullable=False
    )
    owner_id: Mapped[Optional[int]] = mapped_column(
        Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    edges: Mapped[List["PipelineEdge"]] = relationship(
        "PipelineEdge", back_populates="pipeline", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<Pipeline id={self.id} name={self.name!r}>"


class PipelineEdge(Base):
    """Directed dependency edge in a pipeline graph."""

    __tablename__ = "pipeline_edges"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    pipeline_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False, index=True
    )
    upstream_job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    downstream_job_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("jobs.id", ondelete="CASCADE"), nullable=False
    )
    on_condition: Mapped[EdgeCondition] = mapped_column(
        Enum(EdgeCondition), default=EdgeCondition.SUCCESS, nullable=False
    )

    pipeline: Mapped["Pipeline"] = relationship("Pipeline", back_populates="edges")

    def __repr__(self) -> str:
        return (
            f"<PipelineEdge id={self.id} "
            f"{self.upstream_job_id} →[{self.on_condition}]→ {self.downstream_job_id}>"
        )


# ── Database initialisation ───────────────────────────────────────────────────

async def init_db() -> None:
    """Create all tables and seed the bootstrap admin account if absent."""
    import logging as _logging
    from backend.core.security import hash_password
    from sqlalchemy import select

    _log = _logging.getLogger(__name__)

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(User).where(User.username == settings.ADMIN_USERNAME)
        )
        if result.scalar_one_or_none() is None:
            admin = User(
                username=settings.ADMIN_USERNAME,
                email=settings.ADMIN_EMAIL,
                hashed_password=hash_password(settings.ADMIN_PASSWORD),
                is_active=True,
                is_superuser=True,
            )
            session.add(admin)
            await session.commit()
            _log.info("Bootstrap admin account created: '%s'", settings.ADMIN_USERNAME)
