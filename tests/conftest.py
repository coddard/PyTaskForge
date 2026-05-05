"""
Shared pytest fixtures for the PyTaskForge test suite.

Provides:
  - An in-memory SQLite async engine (isolated per test session).
  - An httpx AsyncClient wired to the FastAPI app with PTF_DEV_MODE=true.
  - A temporary jobs/ directory with a pre-seeded hello_world.py script.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker

# ── Force dev-mode so tests never need real JWT tokens ────────────────────────
os.environ["PTF_DEV_MODE"] = "true"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

# Import AFTER env vars are set so Settings picks them up
from backend.core.config import settings  # noqa: E402
from backend.main import app              # noqa: E402
from backend.models.database import Base, get_db  # noqa: E402


# ── In-memory database ────────────────────────────────────────────────────────

_TEST_DB_URL = "sqlite+aiosqlite:///:memory:"

_test_engine = create_async_engine(
    _TEST_DB_URL,
    connect_args={"check_same_thread": False},
    future=True,
)

_TestSessionLocal = sessionmaker(
    bind=_test_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)

# Patch AsyncSessionLocal in the database module so background services
# (webhooks, pipeline_runner, scheduler) use the same in-memory test DB.
import backend.models.database as _db_module  # noqa: E402
_db_module.AsyncSessionLocal = _TestSessionLocal


@pytest_asyncio.fixture(scope="session", autouse=True)
async def create_test_tables():
    """Create all ORM tables in the in-memory DB once per session."""
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    async with _test_engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)


@pytest_asyncio.fixture
async def db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield a transactional DB session that is rolled back after each test."""
    async with _TestSessionLocal() as session:
        yield session
        await session.rollback()


# ── FastAPI test client ───────────────────────────────────────────────────────

@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Yield an AsyncClient pointed at the FastAPI app.

    The real DB dependency is overridden with the test session so that
    every request operates on the same rollback-isolated transaction.
    """

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# Alias — new tests use "async_client" name
@pytest_asyncio.fixture
async def async_client(db_session: AsyncSession) -> AsyncGenerator[AsyncClient, None]:
    """Alias for `client` fixture used by new feature tests."""

    async def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as ac:
        yield ac

    app.dependency_overrides.clear()


# ── Temporary jobs directory ──────────────────────────────────────────────────

@pytest.fixture(scope="session")
def tmp_jobs_dir(tmp_path_factory) -> Path:
    """Create a temporary directory that mirrors the production jobs/ dir."""
    jobs_dir = tmp_path_factory.mktemp("jobs")

    # Seed a working hello_world.py script
    script = jobs_dir / "hello_world.py"
    script.write_text(
        'import os, sys\n'
        'run_id = os.environ.get("PYTASKFORGE_RUN_ID", "unknown")\n'
        'print(f"Hello from PyTaskForge! run_id={run_id}")\n'
        'sys.exit(0)\n',
        encoding="utf-8",
    )

    # Seed a script that deliberately fails
    fail_script = jobs_dir / "always_fails.py"
    fail_script.write_text("raise RuntimeError('intentional failure')\n", encoding="utf-8")

    return jobs_dir

