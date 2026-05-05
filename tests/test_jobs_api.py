"""
Integration tests – Jobs CRUD API
===================================
Tests every jobs endpoint (create, read, update, delete, run, history)
through the FastAPI app in PTF_DEV_MODE.
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient

# ── Helpers ───────────────────────────────────────────────────────────────────

_JOB_PAYLOAD = {
    "name": "Test Job",
    "description": "Created by the test suite",
    "script_path": "hello_world.py",
    "execution_mode": "venv",
    "trigger_type": "interval",
    "trigger_config": {"seconds": 3600},
    "env_vars": {"TEST_KEY": "test_value"},
    "timeout_seconds": 60,
}


async def _create_job(client: AsyncClient, overrides: dict | None = None) -> dict:
    """Helper: POST a job and return the response body."""
    payload = {**_JOB_PAYLOAD, **(overrides or {})}
    response = await client.post("/api/jobs", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


# ── Create ────────────────────────────────────────────────────────────────────

class TestCreateJob:
    @pytest.mark.asyncio
    async def test_create_returns_201(self, client: AsyncClient) -> None:
        response = await client.post("/api/jobs", json=_JOB_PAYLOAD)
        assert response.status_code == 201

    @pytest.mark.asyncio
    async def test_created_job_has_correct_name(self, client: AsyncClient) -> None:
        body = await _create_job(client)
        assert body["name"] == "Test Job"

    @pytest.mark.asyncio
    async def test_created_job_status_is_active(self, client: AsyncClient) -> None:
        body = await _create_job(client)
        assert body["status"] == "active"

    @pytest.mark.asyncio
    async def test_create_with_cron_trigger(self, client: AsyncClient) -> None:
        body = await _create_job(
            client,
            {"trigger_type": "cron", "trigger_config": {"hour": "9", "minute": "0"}},
        )
        assert body["trigger_type"] == "cron"


# ── Read ──────────────────────────────────────────────────────────────────────

class TestReadJob:
    @pytest.mark.asyncio
    async def test_list_jobs_returns_list(self, client: AsyncClient) -> None:
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_get_single_job(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        response = await client.get(f"/api/jobs/{job['id']}")
        assert response.status_code == 200
        assert response.json()["id"] == job["id"]

    @pytest.mark.asyncio
    async def test_get_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        response = await client.get("/api/jobs/999999")
        assert response.status_code == 404


# ── Update ────────────────────────────────────────────────────────────────────

class TestUpdateJob:
    @pytest.mark.asyncio
    async def test_update_name(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        response = await client.put(
            f"/api/jobs/{job['id']}", json={"name": "Updated Name"}
        )
        assert response.status_code == 200
        assert response.json()["name"] == "Updated Name"

    @pytest.mark.asyncio
    async def test_update_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        response = await client.put("/api/jobs/999999", json={"name": "Ghost"})
        assert response.status_code == 404


# ── Delete ────────────────────────────────────────────────────────────────────

class TestDeleteJob:
    @pytest.mark.asyncio
    async def test_delete_returns_204(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        response = await client.delete(f"/api/jobs/{job['id']}")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_deleted_job_not_retrievable(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        await client.delete(f"/api/jobs/{job['id']}")
        response = await client.get(f"/api/jobs/{job['id']}")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_404(self, client: AsyncClient) -> None:
        response = await client.delete("/api/jobs/999999")
        assert response.status_code == 404


# ── Run & History ─────────────────────────────────────────────────────────────

class TestRunJob:
    @pytest.mark.asyncio
    async def test_run_now_returns_202(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        response = await client.post(f"/api/jobs/{job['id']}/run")
        assert response.status_code == 202

    @pytest.mark.asyncio
    async def test_run_nonexistent_job_returns_404(self, client: AsyncClient) -> None:
        response = await client.post("/api/jobs/999999/run")
        assert response.status_code == 404

    @pytest.mark.asyncio
    async def test_history_returns_list(self, client: AsyncClient) -> None:
        job = await _create_job(client)
        response = await client.get(f"/api/jobs/{job['id']}/history")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

