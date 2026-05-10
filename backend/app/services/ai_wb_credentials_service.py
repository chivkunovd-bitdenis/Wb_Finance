from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.core.crypto import decrypt_text, encrypt_text
from app.models.ai_wb_cabinet_credential import AiWbCabinetCredential


@dataclass(frozen=True)
class InvalidPayloadError(Exception):
    message: str


def upsert_credentials(*, db: Session, user_id: str, wb_login: str, wb_password: str) -> AiWbCabinetCredential:
    login = (wb_login or "").strip()
    password = (wb_password or "").strip()
    if not login:
        raise InvalidPayloadError("wb_login is required")
    if not password:
        raise InvalidPayloadError("wb_password is required")

    enc_login = encrypt_text(login)
    enc_password = encrypt_text(password)

    row = db.query(AiWbCabinetCredential).filter(AiWbCabinetCredential.user_id == user_id).first()
    if row is None:
        row = AiWbCabinetCredential(
            user_id=user_id,
            wb_login_enc=enc_login,
            wb_password_enc=enc_password,
            status="active",
            last_error=None,
            last_verified_at=None,
        )
    else:
        row.wb_login_enc = enc_login
        row.wb_password_enc = enc_password
        row.status = "active"
        row.last_error = None
        row.last_verified_at = None

    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def credentials_status(*, db: Session, user_id: str) -> dict:
    row = db.query(AiWbCabinetCredential).filter(AiWbCabinetCredential.user_id == user_id).first()
    if row is None:
        return {"status": "missing", "last_verified_at": None, "last_error": None}
    return {
        "status": row.status,
        "last_verified_at": row.last_verified_at,
        "last_error": row.last_error,
    }


def decrypt_credentials(*, db: Session, user_id: str) -> tuple[str, str]:
    row = db.query(AiWbCabinetCredential).filter(AiWbCabinetCredential.user_id == user_id).first()
    if row is None:
        raise InvalidPayloadError("WB credentials are not set")
    login = decrypt_text(row.wb_login_enc)
    password = decrypt_text(row.wb_password_enc)
    return login, password


def mark_verified(*, db: Session, user_id: str) -> None:
    row = db.query(AiWbCabinetCredential).filter(AiWbCabinetCredential.user_id == user_id).first()
    if row is None:
        return
    row.last_verified_at = datetime.now(UTC)
    row.status = "active"
    row.last_error = None
    db.add(row)
    db.commit()


def mark_error(*, db: Session, user_id: str, status: str, message: str) -> None:
    row = db.query(AiWbCabinetCredential).filter(AiWbCabinetCredential.user_id == user_id).first()
    if row is None:
        return
    row.status = status
    row.last_error = (message or "")[:800]
    db.add(row)
    db.commit()

