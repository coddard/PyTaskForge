"""
PyTaskForge – Application Configuration
========================================
All settings are driven by environment variables or a .env file.
No hard-coded secrets or magic values; every tuneable is declared here.
"""
from __future__ import annotations

import secrets
from pathlib import Path
from typing import List, Optional

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Sentinel used to detect when SECRET_KEY was NOT explicitly provided.
_UNSET_SENTINEL: str = "__UNSET__"


class Settings(BaseSettings):
    """Central configuration object for the entire application.

    Every field can be overridden via environment variable or .env file.
    Attribute names map 1-to-1 with env-var names (case-insensitive).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # ── Application ───────────────────────────────────────────────────────────
    APP_NAME: str = "PyTaskForge"
    APP_VERSION: str = "2.0.0"
    DEBUG: bool = False

    # ── Developer / CI bypass ─────────────────────────────────────────────────
    # PTF_DEV_MODE=true → authentication is fully bypassed.
    # A visible warning header is injected into every HTTP response.
    # NEVER enable in production.
    PTF_DEV_MODE: bool = False

    # ── Security / JWT ────────────────────────────────────────────────────────
    # Defaults to sentinel; the model_validator below enforces explicit setting
    # in production (PTF_DEV_MODE=False).
    SECRET_KEY: str = _UNSET_SENTINEL
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 8  # 8 hours

    # ── Bootstrap admin account (auto-created on first startup) ──────────────
    ADMIN_USERNAME: str = "admin"
    ADMIN_PASSWORD: str = "changeme"  # Must be changed in production!
    ADMIN_EMAIL: str = "admin@pytaskforge.local"

    # ── Database ──────────────────────────────────────────────────────────────
    DATABASE_URL: str = "sqlite+aiosqlite:///./pytaskforge.db"

    # ── File-system paths ─────────────────────────────────────────────────────
    BASE_DIR: Path = Path(__file__).resolve().parents[2]
    JOBS_DIR: Path = BASE_DIR / "jobs"
    VENV_BASE_DIR: Path = BASE_DIR / ".venvs"

    # ── Docker execution defaults ─────────────────────────────────────────────
    DOCKER_DEFAULT_IMAGE: str = "python:3.11-slim"
    DOCKER_NETWORK: str = "none"        # no network = stronger isolation
    DOCKER_MEM_LIMIT: str = "256m"
    DOCKER_CPU_PERIOD: int = 100_000
    DOCKER_CPU_QUOTA: int = 50_000      # 50 % CPU cap

    # ── CORS ──────────────────────────────────────────────────────────────────
    CORS_ORIGINS: List[str] = ["http://localhost:5173", "http://localhost:8000"]

    # ── Concurrency ───────────────────────────────────────────────────────────
    MAX_CONCURRENT_JOBS: int = 10

    # ── Logging ───────────────────────────────────────────────────────────────
    # LOG_FORMAT=json  → structured JSON (production / log aggregators)
    # LOG_FORMAT=text  → human-readable plain text (default / development)
    LOG_FORMAT: str = "text"

    # ── VaultGuard encryption key ─────────────────────────────────────────────
    # Generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
    # Must be set when secrets feature is used.
    VAULT_ENCRYPTION_KEY: Optional[str] = None

    # ── Validators ────────────────────────────────────────────────────────────

    @field_validator("JOBS_DIR", "VENV_BASE_DIR", mode="after")
    @classmethod
    def _ensure_directory_exists(cls, path: Path) -> Path:
        """Create the directory on disk if it does not exist yet."""
        path.mkdir(parents=True, exist_ok=True)
        return path

    @model_validator(mode="after")
    def _validate_production_secrets(self) -> "Settings":
        """Enforce explicit SECRET_KEY in production mode.

        Raises:
            RuntimeError: SECRET_KEY is not set and PTF_DEV_MODE is False.
        """
        if not self.PTF_DEV_MODE and self.SECRET_KEY == _UNSET_SENTINEL:
            raise RuntimeError(
                "FATAL: SECRET_KEY must be explicitly set via environment variable "
                "or .env file when PTF_DEV_MODE is False. "
                "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(64))\""
            )
        # In dev mode, fall back to a random key (tokens won't survive restarts — acceptable).
        if self.SECRET_KEY == _UNSET_SENTINEL:
            object.__setattr__(self, "SECRET_KEY", secrets.token_urlsafe(64))
        return self


settings = Settings()
