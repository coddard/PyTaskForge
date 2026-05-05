"""
Unit tests – Security helpers
==============================
Tests for password hashing, JWT token creation/decoding,
the JWTAuthBackend, and FastAPI dependency helpers.
"""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from backend.core.security import (
    JWTAuthBackend,
    TokenData,
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)


# ── Password hashing ──────────────────────────────────────────────────────────

class TestPasswordHashing:
    def test_hash_is_not_plaintext(self) -> None:
        hashed = hash_password("secret123")
        assert hashed != "secret123"

    def test_correct_password_verifies(self) -> None:
        hashed = hash_password("correct")
        assert verify_password("correct", hashed) is True

    def test_wrong_password_rejected(self) -> None:
        hashed = hash_password("correct")
        assert verify_password("wrong", hashed) is False


# ── JWT helpers ───────────────────────────────────────────────────────────────

class TestJWT:
    def test_encode_decode_roundtrip(self) -> None:
        token = create_access_token({"sub": "alice", "uid": 42, "scopes": ["admin"]})
        data = decode_token(token)
        assert data.username == "alice"
        assert data.user_id == 42
        assert "admin" in data.scopes

    def test_expired_token_raises_401(self) -> None:
        token = create_access_token(
            {"sub": "alice"}, expires_delta=timedelta(seconds=-1)
        )
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401

    def test_tampered_token_raises_401(self) -> None:
        token = create_access_token({"sub": "alice"})
        tampered = token[:-4] + "XXXX"
        with pytest.raises(HTTPException) as exc_info:
            decode_token(tampered)
        assert exc_info.value.status_code == 401

    def test_token_without_sub_raises_401(self) -> None:
        token = create_access_token({"uid": 99})  # missing "sub"
        with pytest.raises(HTTPException) as exc_info:
            decode_token(token)
        assert exc_info.value.status_code == 401


# ── JWTAuthBackend ────────────────────────────────────────────────────────────

class TestJWTAuthBackend:
    def _make_user(self, username: str, password: str, is_active: bool = True):
        user = MagicMock()
        user.id = 1
        user.username = username
        user.email = f"{username}@example.com"
        user.hashed_password = hash_password(password)
        user.is_active = is_active
        return user

    @pytest.mark.asyncio
    async def test_valid_credentials_return_user_dict(self) -> None:
        user = self._make_user("admin", "password123")
        lookup = AsyncMock(return_value=user)
        backend = JWTAuthBackend(lookup)

        result = await backend.authenticate("admin", "password123")

        assert result is not None
        assert result["username"] == "admin"
        assert result["id"] == 1

    @pytest.mark.asyncio
    async def test_wrong_password_returns_none(self) -> None:
        user = self._make_user("admin", "correct")
        lookup = AsyncMock(return_value=user)
        backend = JWTAuthBackend(lookup)

        result = await backend.authenticate("admin", "wrong")

        assert result is None

    @pytest.mark.asyncio
    async def test_unknown_user_returns_none(self) -> None:
        lookup = AsyncMock(return_value=None)
        backend = JWTAuthBackend(lookup)

        result = await backend.authenticate("ghost", "any")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_returns_dict_for_known_user(self) -> None:
        user = self._make_user("bob", "pw")
        lookup = AsyncMock(return_value=user)
        backend = JWTAuthBackend(lookup)

        result = await backend.get_user("bob")

        assert result is not None
        assert result["username"] == "bob"

    @pytest.mark.asyncio
    async def test_get_user_returns_none_for_unknown(self) -> None:
        lookup = AsyncMock(return_value=None)
        backend = JWTAuthBackend(lookup)
        assert await backend.get_user("nobody") is None

