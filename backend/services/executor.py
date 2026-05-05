"""
PyTaskForge – Task Execution Engine
=====================================
Two isolation modes, both implementing :class:`BaseExecutor`:

  VenvExecutor   – Isolated Python virtual environment per run.
  DockerExecutor – Ephemeral Docker container; removed after execution.

Both executors stream stdout/stderr as an ``AsyncGenerator[str, None]``
so lines are forwarded live over WebSockets *and* persisted to the DB.
"""
from __future__ import annotations

import asyncio
import logging
import os
import shutil
import sys
import tempfile
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncGenerator, Dict, Optional

from backend.core.config import settings

logger = logging.getLogger(__name__)

# Maximum bytes per log line (DoS guard)
_MAX_LINE_BYTES: int = 64 * 1_024  # 64 KB

# Log line prefixes used by both executors
_TAG_OUT: str = "[OUT]"
_TAG_ERR: str = "[ERR]"
_TAG_INFO: str = "[INFO]"
_TAG_PIP: str = "[PIP]"
_TAG_BUILD: str = "[BUILD]"
_TAG_SYSTEM: str = "[SYSTEM]"


# ── Stream multiplexer ────────────────────────────────────────────────────────

async def _interleave_streams(
    proc: asyncio.subprocess.Process,
) -> AsyncGenerator[str, None]:
    """Yield stdout and stderr lines interleaved in arrival order.

    Uses an :class:`asyncio.Queue` as a rendezvous point so both
    streams are consumed concurrently without blocking each other.

    Args:
        proc: Running subprocess with ``stdout`` and ``stderr`` pipes.

    Yields:
        Decoded log lines prefixed with ``[OUT]`` or ``[ERR]``.
    """
    assert proc.stdout is not None, "stdout pipe is required"
    assert proc.stderr is not None, "stderr pipe is required"

    queue: asyncio.Queue[Optional[str]] = asyncio.Queue()

    async def _drain(stream: asyncio.StreamReader, tag: str) -> None:
        while True:
            try:
                line = await stream.readline()
            except Exception as exc:
                logger.debug("Stream read error (%s): %s", tag, exc)
                break
            if not line:
                break
            await queue.put(f"{tag} {line.decode(errors='replace')}")
        await queue.put(None)  # sentinel

    producers = [
        asyncio.create_task(_drain(proc.stdout, _TAG_OUT)),
        asyncio.create_task(_drain(proc.stderr, _TAG_ERR)),
    ]

    finished = 0
    while finished < len(producers):
        item = await queue.get()
        if item is None:
            finished += 1
        else:
            yield item

    for task in producers:
        task.cancel()


# ── Abstract executor interface ───────────────────────────────────────────────

class BaseExecutor(ABC):
    """Contract that every execution backend must fulfil."""

    @abstractmethod
    async def run(
        self,
        script_path: Path,
        requirements: Optional[str],
        env_vars: Optional[Dict[str, str]],
        timeout: Optional[int],
    ) -> AsyncGenerator[str, None]:
        """Execute *script_path* and stream output lines.

        Args:
            script_path:  Absolute path to the .py file.
            requirements: Contents of requirements.txt (pip dependencies).
            env_vars:     Extra environment variables for the subprocess.
            timeout:      Hard wall-clock limit in seconds; None = unlimited.

        Yields:
            Log lines prefixed with ``[OUT]``, ``[ERR]``, or ``[INFO]``.
        """
        ...

    @abstractmethod
    async def cleanup(self) -> None:
        """Release all resources created during execution."""
        ...


# ── Venv Executor ─────────────────────────────────────────────────────────────

class VenvExecutor(BaseExecutor):
    """Run a script inside a freshly created Python virtual environment.

    Lifecycle:
      1. Create a new venv under ``settings.VENV_BASE_DIR / <run_id>``.
      2. Install *requirements* with pip (if provided).
      3. Execute the script with the venv interpreter.
      4. :meth:`cleanup` removes the venv directory.
    """

    def __init__(self) -> None:
        self._run_id: str = str(uuid.uuid4())
        self._venv_path: Optional[Path] = None

    # ── Private helpers ───────────────────────────────────────────────────────

    async def _create_venv(self) -> Path:
        """Provision a new virtual environment; return its directory."""
        venv_path = settings.VENV_BASE_DIR / self._run_id
        logger.info("Creating venv at: %s", venv_path)
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-m", "venv", str(venv_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        if proc.returncode != 0:
            raise RuntimeError(
                f"Venv creation failed (exit {proc.returncode}): "
                f"{stderr.decode(errors='replace')}"
            )
        return venv_path

    @staticmethod
    def _python_executable(venv_path: Path) -> str:
        """Return the Python interpreter path for *venv_path*."""
        if sys.platform == "win32":
            return str(venv_path / "Scripts" / "python.exe")
        return str(venv_path / "bin" / "python")

    @staticmethod
    def _pip_executable(venv_path: Path) -> str:
        """Return the pip executable path for *venv_path*."""
        if sys.platform == "win32":
            return str(venv_path / "Scripts" / "pip.exe")
        return str(venv_path / "bin" / "pip")

    async def _install_requirements(
        self,
        venv_path: Path,
        requirements: str,
    ) -> AsyncGenerator[str, None]:
        """Install *requirements* into *venv_path* and stream pip output."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        ) as req_file:
            req_file.write(requirements)
            req_path = req_file.name

        try:
            logger.info("Installing requirements from: %s", req_path)
            proc = await asyncio.create_subprocess_exec(
                self._pip_executable(venv_path),
                "install", "-r", req_path, "--no-cache-dir",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            async for raw_line in _interleave_streams(proc):
                yield f"{_TAG_PIP} {raw_line.lstrip('[OUT] ').lstrip('[ERR] ')}"
            await proc.wait()
            if proc.returncode != 0:
                yield f"{_TAG_ERR} pip install failed (exit {proc.returncode})\n"
        finally:
            os.unlink(req_path)

    # ── Public interface ──────────────────────────────────────────────────────

    async def run(
        self,
        script_path: Path,
        requirements: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Run *script_path* inside an isolated venv."""
        return self._execute(script_path, requirements, env_vars, timeout)

    async def _execute(
        self,
        script_path: Path,
        requirements: Optional[str],
        env_vars: Optional[Dict[str, str]],
        timeout: Optional[int],
    ) -> AsyncGenerator[str, None]:
        started_at = datetime.now(timezone.utc)
        yield f"{_TAG_INFO} Job started at {started_at.isoformat()}\n"
        yield f"{_TAG_INFO} Script: {script_path}\n"
        yield f"{_TAG_INFO} Mode: venv | run_id={self._run_id}\n"

        try:
            self._venv_path = await self._create_venv()
            yield f"{_TAG_INFO} Venv ready: {self._venv_path}\n"

            if requirements and requirements.strip():
                async for line in self._install_requirements(self._venv_path, requirements):
                    yield line

            env = {**os.environ, **(env_vars or {}), "PYTASKFORGE_RUN_ID": self._run_id}

            python = self._python_executable(self._venv_path)
            yield f"{_TAG_INFO} Running: {python} {script_path}\n"

            proc = await asyncio.create_subprocess_exec(
                python, str(script_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
                cwd=str(script_path.parent),
            )

            try:
                async for line in _interleave_streams(proc):
                    yield line

                if timeout:
                    await asyncio.wait_for(proc.wait(), timeout=float(timeout))
                else:
                    await proc.wait()

            except asyncio.TimeoutError:
                proc.kill()
                yield f"{_TAG_ERR} Execution timed out after {timeout}s – process killed.\n"
                raise

            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            yield f"{_TAG_INFO} Finished | exit={proc.returncode} | elapsed={duration:.2f}s\n"

        except asyncio.TimeoutError:
            yield f"{_TAG_ERR} Job timed out.\n"
            raise
        except Exception as exc:
            logger.exception("VenvExecutor error: %s", exc)
            yield f"{_TAG_ERR} Unexpected error: {exc}\n"
            raise

    async def cleanup(self) -> None:
        """Remove the temporary venv directory."""
        if self._venv_path and self._venv_path.exists():
            try:
                shutil.rmtree(self._venv_path, ignore_errors=True)
                logger.info("Venv removed: %s", self._venv_path)
            except Exception as exc:
                logger.warning("Failed to remove venv: %s", exc)


# ── Docker Executor ───────────────────────────────────────────────────────────

class DockerExecutor(BaseExecutor):
    """Run a script inside an ephemeral Docker container.

    Lifecycle:
      1. Copy the script (and requirements) into a temporary build context.
      2. Build a one-off Docker image with ``docker build``.
      3. Start the container, stream logs, wait for exit.
      4. :meth:`cleanup` removes the container, image, and build context.

    Requires:
        Docker daemon running and the ``docker`` Python package installed::

            pip install docker
    """

    def __init__(self, image: Optional[str] = None) -> None:
        self._run_id: str = str(uuid.uuid4())
        self._image_name: str = f"pytaskforge-run-{self._run_id[:8]}"
        self._base_image: str = image or settings.DOCKER_DEFAULT_IMAGE
        self._container_id: Optional[str] = None
        self._build_context: Optional[Path] = None
        self._client = None  # docker.DockerClient – lazy initialisation

    def _docker_client(self):
        """Return a cached Docker client, initialising it on first call."""
        if self._client is None:
            try:
                import docker  # type: ignore
                self._client = docker.from_env()
            except ImportError as exc:
                raise RuntimeError(
                    "The 'docker' package is required for Docker mode. "
                    "Install it with: pip install docker"
                ) from exc
            except Exception as exc:
                raise RuntimeError(
                    f"Could not connect to the Docker daemon: {exc}. "
                    "Ensure Docker Desktop or Docker Engine is running."
                ) from exc
        return self._client

    @staticmethod
    def _render_dockerfile(base_image: str, has_requirements: bool) -> str:
        """Render a minimal Dockerfile string for the given parameters."""
        lines = [
            f"FROM {base_image}",
            "WORKDIR /app",
            "COPY . /app/",
        ]
        if has_requirements:
            lines += [
                "COPY requirements.txt /app/requirements.txt",
                "RUN pip install --no-cache-dir -r requirements.txt",
            ]
        lines.append('CMD ["python", "script.py"]')
        return "\n".join(lines) + "\n"

    async def run(
        self,
        script_path: Path,
        requirements: Optional[str] = None,
        env_vars: Optional[Dict[str, str]] = None,
        timeout: Optional[int] = None,
    ) -> AsyncGenerator[str, None]:
        """Run *script_path* inside a disposable Docker container."""
        return self._execute(script_path, requirements, env_vars, timeout)

    async def _execute(
        self,
        script_path: Path,
        requirements: Optional[str],
        env_vars: Optional[Dict[str, str]],
        timeout: Optional[int],
    ) -> AsyncGenerator[str, None]:
        started_at = datetime.now(timezone.utc)
        yield f"{_TAG_INFO} Job started at {started_at.isoformat()}\n"
        yield f"{_TAG_INFO} Script: {script_path}\n"
        yield f"{_TAG_INFO} Mode: docker | image={self._base_image} | run_id={self._run_id}\n"

        ctx = Path(tempfile.mkdtemp(prefix="ptf_docker_"))
        self._build_context = ctx

        try:
            shutil.copy2(script_path, ctx / "script.py")
            has_requirements = bool(requirements and requirements.strip())
            if has_requirements:
                (ctx / "requirements.txt").write_text(requirements, encoding="utf-8")  # type: ignore[arg-type]

            (ctx / "Dockerfile").write_text(
                self._render_dockerfile(self._base_image, has_requirements),
                encoding="utf-8",
            )

            yield f"{_TAG_INFO} Building Docker image: {self._image_name}\n"

            client = self._docker_client()
            loop = asyncio.get_running_loop()

            try:
                _, build_logs = await loop.run_in_executor(
                    None,
                    lambda: client.images.build(
                        path=str(ctx), tag=self._image_name, rm=True, forcerm=True
                    ),
                )
                for log_entry in build_logs:
                    if "stream" in log_entry:
                        yield f"{_TAG_BUILD} {log_entry['stream']}"
            except Exception as exc:
                yield f"{_TAG_ERR} Docker build failed: {exc}\n"
                raise

            yield f"{_TAG_INFO} Build complete – starting container …\n"

            env_list = [f"{k}={v}" for k, v in (env_vars or {}).items()]
            env_list.append(f"PYTASKFORGE_RUN_ID={self._run_id}")

            container = await loop.run_in_executor(
                None,
                lambda: client.containers.run(
                    self._image_name,
                    detach=True,
                    environment=env_list,
                    network_mode=settings.DOCKER_NETWORK,
                    mem_limit=settings.DOCKER_MEM_LIMIT,
                    cpu_period=settings.DOCKER_CPU_PERIOD,
                    cpu_quota=settings.DOCKER_CPU_QUOTA,
                    remove=False,
                ),
            )
            self._container_id = container.id
            yield f"{_TAG_INFO} Container ID: {container.short_id}\n"

            async def _stream_container_logs() -> AsyncGenerator[str, None]:
                log_stream = await loop.run_in_executor(
                    None, lambda: container.logs(stream=True, follow=True)
                )
                for chunk in log_stream:
                    yield f"{_TAG_OUT} {chunk.decode(errors='replace') if isinstance(chunk, bytes) else chunk}"

            if timeout:
                try:
                    async for line in asyncio.timeout(_stream_container_logs(), timeout):
                        yield line
                except asyncio.TimeoutError:
                    await loop.run_in_executor(None, container.kill)
                    yield f"{_TAG_ERR} Container timed out after {timeout}s – killed.\n"
                    raise
            else:
                async for line in _stream_container_logs():
                    yield line

            result = await loop.run_in_executor(None, container.wait)
            exit_code: int = result.get("StatusCode", -1)
            duration = (datetime.now(timezone.utc) - started_at).total_seconds()
            yield f"{_TAG_INFO} Finished | exit={exit_code} | elapsed={duration:.2f}s\n"

        except asyncio.TimeoutError:
            raise
        except Exception as exc:
            logger.exception("DockerExecutor error: %s", exc)
            yield f"{_TAG_ERR} Unexpected error: {exc}\n"
            raise

    async def cleanup(self) -> None:
        """Remove the container, image, and temporary build context."""
        loop = asyncio.get_running_loop()
        client = self._docker_client()

        if self._container_id:
            try:
                container = await loop.run_in_executor(
                    None, lambda: client.containers.get(self._container_id)
                )
                await loop.run_in_executor(None, lambda: container.remove(force=True))
                logger.info("Container removed: %s", self._container_id)
            except Exception as exc:
                logger.warning("Failed to remove container: %s", exc)

        try:
            await loop.run_in_executor(
                None, lambda: client.images.remove(self._image_name, force=True)
            )
            logger.info("Docker image removed: %s", self._image_name)
        except Exception as exc:
            logger.warning("Failed to remove Docker image: %s", exc)

        if self._build_context and self._build_context.exists():
            shutil.rmtree(self._build_context, ignore_errors=True)


# ── Factory function ──────────────────────────────────────────────────────────

def get_executor(mode: str, docker_image: Optional[str] = None) -> BaseExecutor:
    """Return the appropriate executor for *mode*.

    Args:
        mode:         ``'venv'`` or ``'docker'``.
        docker_image: Base image for Docker mode; ignored for venv mode.

    Returns:
        A :class:`BaseExecutor` instance ready to call :meth:`run`.

    Raises:
        ValueError: *mode* is not one of the recognised values.
    """
    normalised = mode.lower().strip()
    if normalised == "venv":
        return VenvExecutor()
    if normalised == "docker":
        return DockerExecutor(image=docker_image)
    raise ValueError(
        f"Unknown execution mode '{mode}'. Valid values are 'venv' and 'docker'."
    )


# ── Utility ───────────────────────────────────────────────────────────────────

async def collect_output(generator: AsyncGenerator[str, None]) -> str:
    """Drain an async generator into a single concatenated string.

    Useful in non-WebSocket contexts (e.g., unit tests, CLI).
    """
    return "".join([line async for line in generator])
