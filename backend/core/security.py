"""
PyTaskForge – Security & Authentication Layer
==============================================
Architecture:
  ┌──────────────────────────────────────────┐
  │           AuthBackend  (ABC)             │  ← Pluggable interface
  ├──────────────────────────────────────────┤
  │  JWTAuthBackend  │  (LDAPAuthBackend …)  │  ← Concrete implementations
  └──────────────────────────────────────────┘

PTF_DEV_MODE=true  →  all auth checks are skipped;
every response carries an X-Dev-Mode-Warning header.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from passlib.context import CryptContext
from pydantic import BaseModel

from backend.core.config import settings

logger = logging.getLogger(__name__)

# ── Password hashing ──────────────────────────────────────────────────────────
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/token", auto_error=False)


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class TokenData(BaseModel):
    """Decoded JWT payload carried through the request pipeline."""

    username: Optional[str] = None
    user_id: Optional[int] = None
    scopes: List[str] = []


class TokenResponse(BaseModel):
    """Payload returned by the login endpoint."""

    access_token: str
    token_type: str = "bearer"
    expires_in: int  # seconds


# ── Crypto helpers ────────────────────────────────────────────────────────────

def hash_password(plain: str) -> str:
    """Hash a plain-text password with bcrypt."""
    return _pwd_context.hash(plain)


def verify_password(plain: str, hashed: str) -> bool:
    """Return True when *plain* matches the stored *hashed* password."""
    return _pwd_context.verify(plain, hashed)


def create_access_token(
    data: Dict[str, Any],
    expires_delta: Optional[timedelta] = None,
) -> str:
    """Sign and encode a JWT access token.

    Args:
        data: Claims to embed in the token payload.
        expires_delta: Custom lifetime; falls back to the global setting.

    Returns:
        Encoded JWT string.
    """
    payload = data.copy()
    now = datetime.now(tz=timezone.utc)
    expire = now + (expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES))
    payload.update({"exp": expire, "iat": now})
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=settings.ALGORITHM)


def decode_token(token: str) -> TokenData:
    """Verify and decode a JWT token into a :class:`TokenData` instance.

    Raises:
        HTTPException 401: Token is invalid, expired, or malformed.
    """
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials. Token is invalid or expired.",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        username: Optional[str] = payload.get("sub")
        if username is None:
            raise credentials_exception
        return TokenData(
            username=username,
            user_id=payload.get("uid"),
            scopes=payload.get("scopes", []),
        )
    except JWTError as exc:
        logger.warning("JWT decode error: %s", exc)
        raise credentials_exception from exc


# ── Pluggable Auth Backend interface ─────────────────────────────────────────

class AuthBackend(ABC):
    """Abstract interface for authentication providers.

    Implementing a new backend (LDAP, OAuth2, SAML …) requires only
    subclassing this class and overriding the two abstract methods.
    """

    @abstractmethod
    async def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Verify credentials and return a user-info dict on success, None on failure."""
        ...

    @abstractmethod
    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Return a user-info dict for *username*, or None if not found."""
        ...


# ── JWT + Database concrete implementation ───────────────────────────────────

class JWTAuthBackend(AuthBackend):
    """Database-backed JWT authentication.

    User lookup is injected via *user_lookup_fn* to keep this class
    decoupled from the ORM session (Dependency Inversion Principle).

    Args:
        user_lookup_fn: ``async callable(username: str) -> User | None``
    """

    def __init__(self, user_lookup_fn: Any) -> None:
        self._lookup = user_lookup_fn

    async def authenticate(self, username: str, password: str) -> Optional[Dict[str, Any]]:
        """Return user dict when credentials are valid, None otherwise."""
        user = await self._lookup(username)
        if user is None:
            logger.info("authenticate: user '%s' not found", username)
            return None
        if not verify_password(password, user.hashed_password):
            logger.warning("authenticate: invalid password for user '%s'", username)
            return None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
        }

    async def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """Return user dict for *username*, or None if not found."""
        user = await self._lookup(username)
        if user is None:
            return None
        return {
            "id": user.id,
            "username": user.username,
            "email": user.email,
            "is_active": user.is_active,
        }


# ── FastAPI dependency helpers ────────────────────────────────────────────────

async def get_current_user_optional(
    token: Optional[str] = Depends(oauth2_scheme),
) -> Optional[TokenData]:
    """Resolve the current user without raising on missing token.

    In dev mode a synthetic *dev-user* token-data is always returned.
    """
    if settings.PTF_DEV_MODE:
        return TokenData(username="dev-user", user_id=0, scopes=["admin"])
    if token is None:
        return None
    return decode_token(token)


async def require_authenticated(
    token_data: Optional[TokenData] = Depends(get_current_user_optional),
) -> TokenData:
    """Enforce authentication on protected endpoints.

    Raises:
        HTTPException 401: User is not authenticated.
    """
    if settings.PTF_DEV_MODE:
        logger.warning(
            "PTF_DEV_MODE is active - authentication is DISABLED. "
            "Do NOT use this setting in production."
        )
        return TokenData(username="dev-user", user_id=0, scopes=["admin"])

    if token_data is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required.",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return token_data


def require_scope(scope: str):
    """Dependency factory that enforces a specific token scope.

    Usage::

        @router.delete("/jobs/{job_id}", dependencies=[Depends(require_scope("admin"))])
    """
    async def _check_scope(
        token_data: TokenData = Depends(require_authenticated),
    ) -> TokenData:
        if settings.PTF_DEV_MODE:
            return token_data  # scope checks are skipped in dev mode
        if scope not in token_data.scopes:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"The '{scope}' scope is required for this operation.",
            )
        return token_data

    return _check_scope
