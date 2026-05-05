"""
Tests for VaultGuard: Encrypted Secrets Manager (Phase 1)
"""
from __future__ import annotations

import os
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("PTF_DEV_MODE", "true")

_TEST_KEY = None


def _get_test_key() -> str:
    global _TEST_KEY
    if _TEST_KEY is None:
        from cryptography.fernet import Fernet
        _TEST_KEY = Fernet.generate_key().decode()
    return _TEST_KEY


class TestVaultEncryption:
    """Unit tests for vault.py encrypt/decrypt round-trip."""

    def test_encrypt_produces_non_plaintext(self):
        """Encrypted output must not contain the plaintext value."""
        with patch("backend.core.config.settings") as mock_settings:
            mock_settings.VAULT_ENCRYPTION_KEY = _get_test_key()
            from backend.core import vault as vault_module
            vault_module._get_fernet.cache_clear()
            from backend.core.vault import encrypt_secret
            ciphertext = encrypt_secret("my_secret_value")
            assert "my_secret_value" not in ciphertext
        vault_module._get_fernet.cache_clear()

    def test_encrypt_decrypt_roundtrip(self):
        """decrypt(encrypt(x)) must equal x."""
        with patch("backend.core.config.settings") as mock_settings:
            mock_settings.VAULT_ENCRYPTION_KEY = _get_test_key()
            from backend.core import vault as vault_module
            vault_module._get_fernet.cache_clear()
            from backend.core.vault import decrypt_secret, encrypt_secret
            original = "super_secret_password_123"
            assert decrypt_secret(encrypt_secret(original)) == original
        vault_module._get_fernet.cache_clear()

    def test_missing_vault_key_raises(self):
        """VaultNotConfiguredError must be raised when key is unset."""
        with patch("backend.core.config.settings") as mock_settings:
            mock_settings.VAULT_ENCRYPTION_KEY = None
            from backend.core import vault as vault_module
            vault_module._get_fernet.cache_clear()
            from backend.core.vault import VaultNotConfiguredError, encrypt_secret
            with pytest.raises(VaultNotConfiguredError):
                encrypt_secret("test")
        vault_module._get_fernet.cache_clear()


class TestSecretsAPI:
    """Integration tests for /api/secrets endpoints."""

    @pytest.mark.asyncio
    async def test_create_secret_returns_201(self, async_client):
        """POST /api/secrets must return 201 and NOT echo back the value."""
        with patch("backend.routers.secrets.encrypt_secret", return_value="ENCRYPTED_VALUE"):
            response = await async_client.post(
                "/api/secrets",
                json={"name": "TEST_KEY", "value": "super_secret"},
            )
        assert response.status_code == 201
        body = response.json()
        assert "super_secret" not in str(body)
        assert body["name"] == "TEST_KEY"

    @pytest.mark.asyncio
    async def test_list_secrets_has_no_value_field(self, async_client):
        """GET /api/secrets must never include a 'value' or 'encrypted_value' field."""
        with patch("backend.routers.secrets.encrypt_secret", return_value="ENCRYPTED_VALUE"):
            await async_client.post(
                "/api/secrets",
                json={"name": "LIST_TEST_KEY", "value": "must_not_appear"},
            )
        response = await async_client.get("/api/secrets")
        assert response.status_code == 200
        for secret in response.json():
            assert "value" not in secret
            assert "encrypted_value" not in secret

    @pytest.mark.asyncio
    async def test_delete_secret_returns_204(self, async_client):
        """DELETE /api/secrets/{name} must return 204."""
        with patch("backend.routers.secrets.encrypt_secret", return_value="ENCRYPTED_VALUE"):
            await async_client.post(
                "/api/secrets",
                json={"name": "DELETE_ME", "value": "temp"},
            )
        response = await async_client.delete("/api/secrets/DELETE_ME")
        assert response.status_code == 204

    @pytest.mark.asyncio
    async def test_delete_nonexistent_secret_returns_404(self, async_client):
        """Deleting a non-existent secret must return 404."""
        response = await async_client.delete("/api/secrets/DOES_NOT_EXIST_XYZ")
        assert response.status_code == 404


class TestSecretResolver:
    """Unit tests for secret_resolver.resolve_secrets()."""

    @pytest.mark.asyncio
    async def test_placeholder_is_replaced(self):
        """{{ secrets.KEY }} placeholder must be replaced with the decrypted value."""
        with patch("backend.services.secret_resolver.decrypt_secret", return_value="real_value"):
            from backend.services.secret_resolver import resolve_secrets

            mock_secret = MagicMock()
            mock_secret.name = "MY_KEY"
            mock_secret.encrypted_value = "ENCRYPTED"

            mock_db = AsyncMock()
            mock_result = MagicMock()
            mock_result.scalars.return_value.all.return_value = [mock_secret]
            mock_db.execute = AsyncMock(return_value=mock_result)

            resolved = await resolve_secrets(
                {"API_KEY": "{{ secrets.MY_KEY }}"}, mock_db, owner_id=1
            )
            assert resolved["API_KEY"] == "real_value"

    @pytest.mark.asyncio
    async def test_missing_secret_raises_error(self):
        """A placeholder referencing a non-existent secret raises SecretNotFoundError."""
        from backend.services.secret_resolver import SecretNotFoundError, resolve_secrets

        mock_db = AsyncMock()
        mock_result = MagicMock()
        mock_result.scalars.return_value.all.return_value = []
        mock_db.execute = AsyncMock(return_value=mock_result)

        with pytest.raises(SecretNotFoundError):
            await resolve_secrets(
                {"KEY": "{{ secrets.MISSING_SECRET }}"}, mock_db, owner_id=1
            )

    @pytest.mark.asyncio
    async def test_no_placeholder_skips_db(self):
        """When no {{ secrets.* }} placeholders exist, the DB must not be queried."""
        from backend.services.secret_resolver import resolve_secrets

        mock_db = AsyncMock()
        result = await resolve_secrets({"PLAIN_KEY": "plain_value"}, mock_db)
        assert result == {"PLAIN_KEY": "plain_value"}
        mock_db.execute.assert_not_called()

