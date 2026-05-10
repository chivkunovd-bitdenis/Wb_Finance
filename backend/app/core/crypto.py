from __future__ import annotations

import os

from cryptography.fernet import Fernet, InvalidToken


class CryptoConfigError(RuntimeError):
    pass


def _get_fernet() -> Fernet:
    key = (os.getenv("APP_ENCRYPTION_KEY") or "").strip()
    if not key:
        raise CryptoConfigError("APP_ENCRYPTION_KEY is required")
    try:
        return Fernet(key.encode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        raise CryptoConfigError("APP_ENCRYPTION_KEY is invalid (must be Fernet base64 key)") from exc


def validate_crypto_config() -> None:
    """
    Fail-fast validation (call on startup when feature is enabled).
    """
    _get_fernet()


def encrypt_text(plain: str) -> str:
    f = _get_fernet()
    token = f.encrypt((plain or "").encode("utf-8"))
    return token.decode("utf-8")


def decrypt_text(token: str) -> str:
    f = _get_fernet()
    try:
        raw = f.decrypt((token or "").encode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("Invalid encrypted payload") from exc
    return raw.decode("utf-8")

