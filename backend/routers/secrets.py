"""
PyTaskForge – VaultGuard Secrets Router
=========================================
POST   /api/secrets          →  create or update a secret (value never returned)
GET    /api/secrets          →  list secret names only
DELETE /api/secrets/{name}   →  delete a secret

Security contract:
  - Secret VALUES are NEVER returned by any endpoint.
  - Only secret NAMES are exposed via the API.
  - All values are stored encrypted (AES-256 Fernet) in the database.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, field_validator
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.security import TokenData, require_authenticated
from backend.core.vault import VaultNotConfiguredError, encrypt_secret
from backend.models.database import Secret, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


# ── Request / response schemas ────────────────────────────────────────────────

class SecretCreate(BaseModel):
    """Request body for creating or updating a secret."""
    name: str
    value: str

    @field_validator("name")
    @classmethod
    def _validate_name(cls, v: str) -> str:
        """Enforce alphanumeric + underscore names for safe template resolution."""
        import re
        if not re.match(r"^\w+$", v):
            raise ValueError(
                "Secret name must contain only letters, digits, and underscores."
            )
        return v.upper()


class SecretNameResponse(BaseModel):
    """Response listing a secret by name only — no value field."""
    id: int
    name: str
    owner_id: Optional[int]

    model_config = {"from_attributes": True}


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post(
    "",
    status_code=status.HTTP_201_CREATED,
    summary="Create or update a secret (value is write-only)",
)
async def upsert_secret(
    body: SecretCreate,
    db: AsyncSession = Depends(get_db),
    token: TokenData = Depends(require_authenticated),
) -> dict:
    """Store an encrypted secret.

    If a secret with the same name already exists for this user,
    its value is overwritten. The plaintext value is NEVER returned.
    """
    try:
        encrypted = encrypt_secret(body.value)
    except VaultNotConfiguredError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
        ) from exc

    # Upsert: update if exists, create otherwise.
    result = await db.execute(
        select(Secret).where(Secret.name == body.name)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_value = encrypted
        await db.commit()
        logger.info("Secret updated: name=%s owner_id=%s", body.name, token.user_id)
        return {"detail": "Secret updated.", "name": body.name}

    secret = Secret(
        name=body.name,
        encrypted_value=encrypted,
        owner_id=token.user_id,
    )
    db.add(secret)
    await db.commit()
    logger.info("Secret created: name=%s owner_id=%s", body.name, token.user_id)
    return {"detail": "Secret created.", "name": body.name}


@router.get(
    "",
    response_model=List[SecretNameResponse],
    summary="List secret names (values are never exposed)",
)
async def list_secrets(
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> List[Secret]:
    """Return a list of all stored secret names.

    The ``encrypted_value`` field is intentionally excluded from the
    response model — values can never be retrieved via the API.
    """
    result = await db.execute(select(Secret).order_by(Secret.name))
    return result.scalars().all()


@router.delete(
    "/{secret_name}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a secret by name",
)
async def delete_secret(
    secret_name: str,
    db: AsyncSession = Depends(get_db),
    _: TokenData = Depends(require_authenticated),
) -> None:
    """Permanently delete a secret. This action is irreversible."""
    result = await db.execute(
        select(Secret).where(Secret.name == secret_name.upper())
    )
    secret = result.scalar_one_or_none()
    if not secret:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Secret '{secret_name}' not found.",
        )
    await db.delete(secret)
    await db.commit()
    logger.info("Secret deleted: name=%s", secret_name)

