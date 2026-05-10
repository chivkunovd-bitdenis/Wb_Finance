import pytest

from app.core import crypto


def test_crypto_roundtrip_requires_key(monkeypatch):
    monkeypatch.delenv("APP_ENCRYPTION_KEY", raising=False)
    with pytest.raises(crypto.CryptoConfigError):
        crypto.encrypt_text("x")


def test_crypto_roundtrip_ok(monkeypatch):
    from cryptography.fernet import Fernet

    key = Fernet.generate_key().decode("utf-8")
    monkeypatch.setenv("APP_ENCRYPTION_KEY", key)
    s = "секрет123"
    token = crypto.encrypt_text(s)
    assert token != s
    plain = crypto.decrypt_text(token)
    assert plain == s

