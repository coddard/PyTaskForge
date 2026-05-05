"""
Unit tests – Execution Engine (VenvExecutor & DockerExecutor)
==============================================================
All tests mock external side-effects (subprocess, Docker SDK) so the
suite runs without a real venv creation or Docker daemon.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.services.executor import (
    DockerExecutor,
    VenvExecutor,
    collect_output,
    get_executor,
)


# ── Factory ───────────────────────────────────────────────────────────────────

class TestGetExecutor:
    def test_venv_mode_returns_venv_executor(self) -> None:
        executor = get_executor("venv")
        assert isinstance(executor, VenvExecutor)

    def test_docker_mode_returns_docker_executor(self) -> None:
        executor = get_executor("docker")
        assert isinstance(executor, DockerExecutor)

    def test_docker_mode_with_custom_image(self) -> None:
        executor = get_executor("docker", docker_image="python:3.12-slim")
        assert isinstance(executor, DockerExecutor)
        assert executor._base_image == "python:3.12-slim"

    def test_unknown_mode_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown execution mode"):
            get_executor("kubernetes")

    def test_mode_is_case_insensitive(self) -> None:
        assert isinstance(get_executor("VENV"), VenvExecutor)
        assert isinstance(get_executor("Docker"), DockerExecutor)


# ── VenvExecutor – unit level ─────────────────────────────────────────────────

class TestVenvExecutorHelpers:
    def test_python_executable_path_on_posix(self) -> None:
        venv = Path("/tmp/test_venv")
        expected = str(venv / "bin" / "python")
        if sys.platform != "win32":
            assert VenvExecutor._python_executable(venv) == expected

    def test_pip_executable_path_on_posix(self) -> None:
        venv = Path("/tmp/test_venv")
        expected = str(venv / "bin" / "pip")
        if sys.platform != "win32":
            assert VenvExecutor._pip_executable(venv) == expected


class TestVenvExecutorRun:
    @pytest.mark.asyncio
    async def test_successful_run_produces_info_lines(
        self, tmp_jobs_dir: Path
    ) -> None:
        """Mock venv creation + subprocess to verify output prefix logic."""
        script = tmp_jobs_dir / "hello_world.py"

        executor = VenvExecutor()

        # Mock _create_venv to return a fake path instantly
        fake_venv = tmp_jobs_dir / "fake_venv"
        fake_venv.mkdir(exist_ok=True)
        executor._venv_path = fake_venv

        # Build a mock async process that outputs one line
        mock_stdout = AsyncMock()
        mock_stdout.readline = AsyncMock(
            side_effect=[b"Hello from PyTaskForge!\n", b""]
        )
        mock_stderr = AsyncMock()
        mock_stderr.readline = AsyncMock(side_effect=[b""])

        mock_proc = MagicMock()
        mock_proc.stdout = mock_stdout
        mock_proc.stderr = mock_stderr
        mock_proc.returncode = 0

        async def _fake_wait():
            return 0

        mock_proc.wait = _fake_wait

        with (
            patch.object(executor, "_create_venv", return_value=fake_venv),
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
        ):
            gen = await executor.run(script_path=script)
            output = await collect_output(gen)

        assert "[INFO]" in output

    @pytest.mark.asyncio
    async def test_cleanup_removes_venv_directory(
        self, tmp_path: Path
    ) -> None:
        executor = VenvExecutor()
        fake_venv = tmp_path / "fake_venv"
        fake_venv.mkdir()
        executor._venv_path = fake_venv

        await executor.cleanup()

        assert not fake_venv.exists()

    @pytest.mark.asyncio
    async def test_cleanup_is_safe_when_venv_not_created(self) -> None:
        executor = VenvExecutor()
        # Should not raise even if _venv_path is None
        await executor.cleanup()


# ── DockerExecutor – unit level ───────────────────────────────────────────────

class TestDockerExecutorHelpers:
    def test_render_dockerfile_without_requirements(self) -> None:
        content = DockerExecutor._render_dockerfile("python:3.11-slim", False)
        assert "FROM python:3.11-slim" in content
        assert "requirements.txt" not in content

    def test_render_dockerfile_with_requirements(self) -> None:
        content = DockerExecutor._render_dockerfile("python:3.11-slim", True)
        assert "COPY requirements.txt" in content
        assert "pip install" in content

    def test_missing_docker_package_raises_runtime_error(self) -> None:
        executor = DockerExecutor()
        with patch.dict("sys.modules", {"docker": None}):
            with pytest.raises(RuntimeError, match="docker"):
                executor._docker_client()


# ── Scheduler service – trigger builder ──────────────────────────────────────

class TestSchedulerTriggerBuilder:
    def test_interval_trigger_created(self) -> None:
        from backend.services.scheduler import TaskScheduler
        from apscheduler.triggers.interval import IntervalTrigger

        trigger = TaskScheduler._build_trigger("interval", {"seconds": 30})
        assert isinstance(trigger, IntervalTrigger)

    def test_cron_trigger_created(self) -> None:
        from backend.services.scheduler import TaskScheduler
        from apscheduler.triggers.cron import CronTrigger

        trigger = TaskScheduler._build_trigger("cron", {"hour": "9", "minute": "0"})
        assert isinstance(trigger, CronTrigger)

    def test_date_trigger_created(self) -> None:
        from backend.services.scheduler import TaskScheduler
        from apscheduler.triggers.date import DateTrigger

        trigger = TaskScheduler._build_trigger(
            "date", {"run_date": "2099-01-01T00:00:00"}
        )
        assert isinstance(trigger, DateTrigger)

    def test_unknown_trigger_raises_value_error(self) -> None:
        from backend.services.scheduler import TaskScheduler

        with pytest.raises(ValueError, match="Unknown trigger type"):
            TaskScheduler._build_trigger("kafka", {})

