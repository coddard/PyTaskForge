"""
PyTaskForge – Authentication Router
=====================================
POST /api/auth/token  →  obtain a JWT access token (rate-limited: 5/min/IP)
GET  /api/auth/me     →  return the current user's profile
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.config import settings
from backend.core.security import (
    JWTAuthBackend,
    TokenData,
    TokenResponse,
    create_access_token,
    require_authenticated,
)
from backend.models.database import User, get_db

router = APIRouter(prefix="/api/auth", tags=["auth"])


async def _lookup_user_by_username(username: str, db: AsyncSession) -> User | None:
    """Fetch a :class:`User` row by *username*, or return None."""
    result = await db.execute(select(User).where(User.username == username))
    return result.scalar_one_or_none()


@router.post("/token", response_model=TokenResponse, summary="Obtain a JWT access token")
async def login(
    request: Request,
    form: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
) -> TokenResponse:
    """Exchange username + password for a signed JWT access token.

    Rate-limited to 5 requests per minute per IP address.
    In dev mode a token is always issued without credential checks.
    """

    if settings.PTF_DEV_MODE:
        token = create_access_token({"sub": "dev-user", "uid": 0, "scopes": ["admin"]})
        return TokenResponse(
            access_token=token,
            expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        )

    backend = JWTAuthBackend(lambda u: _lookup_user_by_username(u, db))
    user_info = await backend.authenticate(form.username, form.password)

    if user_info is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user_info.get("is_active"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This account is disabled.",
        )

    user_row = await db.get(User, user_info["id"])
    scopes = ["admin"] if (user_row and user_row.is_superuser) else []
    token = create_access_token(
        {"sub": user_info["username"], "uid": user_info["id"], "scopes": scopes}
    )
    return TokenResponse(
        access_token=token,
        expires_in=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
    )


@router.get("/me", summary="Return the current user's profile")
async def get_current_user_profile(
    token_data: TokenData = Depends(require_authenticated),
) -> dict:
    """Return username, user_id, scopes, and the dev-mode flag."""
    return {
        "username": token_data.username,
        "user_id": token_data.user_id,
        "scopes": token_data.scopes,
        "dev_mode": settings.PTF_DEV_MODE,
    }
