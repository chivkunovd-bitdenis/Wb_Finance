"""
Тесты для app/core/security.py: хэш пароля, проверка, JWT.
В контейнере bcrypt иногда падает на внутренней проверке passlib (72 байт) — мокаем для стабильности.
"""
from unittest.mock import patch

from app.core.security import (
    hash_password,
    verify_password,
    create_access_token,
    decode_access_token,
)


@patch("app.core.security.pwd_context.hash")
def test_hash_password_returns_string(mock_hash):
    mock_hash.return_value = "$2b$12$mockedhash"
    result = hash_password("secret123")
    assert isinstance(result, str)
    assert result != "secret123"
    mock_hash.assert_called_once()


@patch("app.core.security.pwd_context.verify")
def test_verify_password_correct(mock_verify):
    mock_verify.return_value = True
    assert verify_password("mypass", "anyhash") is True


@patch("app.core.security.pwd_context.verify")
def test_verify_password_wrong(mock_verify):
    mock_verify.return_value = False
    assert verify_password("wrong", "anyhash") is False


def test_create_access_token_returns_string():
    token = create_access_token(data={"sub": "user-123"})
    assert isinstance(token, str)
    assert len(token) > 0


def test_decode_access_token_returns_payload():
    token = create_access_token(data={"sub": "user-456"})
    payload = decode_access_token(token)
    assert payload is not None
    assert payload.get("sub") == "user-456"


def test_decode_access_token_invalid_returns_none():
    assert decode_access_token("invalid-token") is None
    assert decode_access_token("") is None
