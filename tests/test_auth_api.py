"""
Integration tests – Authentication API
========================================
Tests the full auth flow through the FastAPI app using httpx:
  - POST /api/auth/token  (login)
  - GET  /api/auth/me     (profile)
  - Protected route access with and without a token
"""
from __future__ import annotations

import pytest
from httpx import AsyncClient


class TestLoginEndpoint:
    @pytest.mark.asyncio
    async def test_dev_mode_always_issues_token(self, client: AsyncClient) -> None:
        """In PTF_DEV_MODE any credentials yield a valid token."""
        response = await client.post(
            "/api/auth/token",
            data={"username": "anyone", "password": "anything"},
        )
        assert response.status_code == 200
        body = response.json()
        assert "access_token" in body
        assert body["token_type"] == "bearer"

    @pytest.mark.asyncio
    async def test_token_is_non_empty_string(self, client: AsyncClient) -> None:
        response = await client.post(
            "/api/auth/token",
            data={"username": "test", "password": "test"},
        )
        token = response.json()["access_token"]
        assert isinstance(token, str)
        assert len(token) > 20


class TestMeEndpoint:
    @pytest.mark.asyncio
    async def test_me_returns_dev_user_in_dev_mode(self, client: AsyncClient) -> None:
        response = await client.get("/api/auth/me")
        assert response.status_code == 200
        body = response.json()
        assert body["username"] == "dev-user"
        assert body["dev_mode"] is True

    @pytest.mark.asyncio
    async def test_me_scopes_include_admin_in_dev_mode(self, client: AsyncClient) -> None:
        response = await client.get("/api/auth/me")
        assert "admin" in response.json()["scopes"]


class TestProtectedRoutes:
    @pytest.mark.asyncio
    async def test_jobs_list_accessible_in_dev_mode(self, client: AsyncClient) -> None:
        """In dev mode all protected endpoints are open without a token."""
        response = await client.get("/api/jobs")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

    @pytest.mark.asyncio
    async def test_health_endpoint_is_public(self, client: AsyncClient) -> None:
        response = await client.get("/api/health")
        assert response.status_code == 200
        assert response.json()["status"] == "ok"

