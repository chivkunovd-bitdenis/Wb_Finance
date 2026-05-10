from __future__ import annotations

from sqlalchemy import CheckConstraint, Column, DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class AiWbCabinetCredential(Base):
    __tablename__ = "ai_wb_cabinet_credentials"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
        unique=True,  # one credential per store owner
    )

    wb_login_enc = Column(Text, nullable=False)
    wb_password_enc = Column(Text, nullable=False)

    status = Column(String(16), nullable=False, default="active")  # active|invalid|needs_reauth|disabled
    last_error = Column(Text, nullable=True)
    last_verified_at = Column(DateTime(timezone=True), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status in ('active','invalid','needs_reauth','disabled')",
            name="ck_ai_wb_cabinet_credentials_status",
        ),
    )

