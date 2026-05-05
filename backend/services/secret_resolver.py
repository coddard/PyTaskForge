"""
PyTaskForge – Secret Resolver Service
=======================================
Resolves ``{{ secrets.NAME }}`` placeholders in job environment variable
values, replacing them with their decrypted secret values at execution time.

Security contract:
  - Decrypted values are NEVER logged.
  - Decrypted values are injected directly into the subprocess environment.
  - No decrypted value ever touches the database or the API layer.
"""
from __future__ import annotations

import logging
import re
from typing import Dict, Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.core.vault import decrypt_secret

logger = logging.getLogger(__name__)

# Pattern that matches {{ secrets.VARIABLE_NAME }}
_SECRET_PATTERN: re.Pattern = re.compile(r"\{\{\s*secrets\.(\w+)\s*\}\}")


class SecretNotFoundError(KeyError):
    """Raised when a {{ secrets.NAME }} reference cannot be resolved."""

    def __init__(self, secret_name: str) -> None:
        super().__init__(
            f"Secret '{secret_name}' not found. "
            "Create it at POST /api/secrets before referencing it in a job."
        )
        self.secret_name = secret_name


async def resolve_secrets(
    env_vars: Dict[str, str],
    db: AsyncSession,
    owner_id: Optional[int] = None,
) -> Dict[str, str]:
    """Replace ``{{ secrets.NAME }}`` placeholders with decrypted values.

    Scans every value in *env_vars*. For each placeholder found, the
    matching :class:`~backend.models.database.Secret` row is fetched from
    the database and its encrypted value is decrypted via the vault.

    Args:
        env_vars:  Dictionary of environment variable names → values.
        db:        Active async database session.
        owner_id:  The job owner's user ID (used to scope secret lookup).

    Returns:
        A new dictionary with placeholders replaced by their decrypted values.
        Non-placeholder values are passed through unchanged.

    Raises:
        SecretNotFoundError: A placeholder references a secret that does not exist.
    """
    from backend.models.database import Secret

    # Fast path: if there are no placeholders, skip the DB query entirely.
    raw = str(env_vars)
    if "secrets." not in raw:
        return dict(env_vars)

    # Collect all referenced secret names in one pass.
    referenced_names: set[str] = set()
    for value in env_vars.values():
        for match in _SECRET_PATTERN.finditer(str(value)):
            referenced_names.add(match.group(1))

    if not referenced_names:
        return dict(env_vars)

    # Fetch all referenced secrets in a single query.
    query = select(Secret).where(Secret.name.in_(referenced_names))
    result = await db.execute(query)
    secret_rows = {row.name: row for row in result.scalars().all()}

    # Verify all references resolve to an existing secret.
    missing = referenced_names - set(secret_rows.keys())
    if missing:
        raise SecretNotFoundError(next(iter(missing)))

    # Decrypt secrets into a temporary in-memory dict.
    # These values must never be logged.
    decrypted: Dict[str, str] = {}
    for name, row in secret_rows.items():
        decrypted[name] = decrypt_secret(row.encrypted_value)

    # Perform substitution on env_var values.
    resolved: Dict[str, str] = {}
    for key, value in env_vars.items():
        def _replace(m: re.Match) -> str:
            return decrypted[m.group(1)]

        resolved[key] = _SECRET_PATTERN.sub(_replace, str(value))

    logger.debug(
        "Resolved %d secret reference(s) for %d env var(s).",
        len(referenced_names),
        len(env_vars),
    )
    return resolved

