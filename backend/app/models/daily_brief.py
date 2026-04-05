"""Кэш ежедневной AI-сводки: 1 запись на (user_id, date_for)."""
from sqlalchemy import Column, Date, DateTime, String, Text, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy import func

from app.models.base import Base, uuid_gen


class DailyBrief(Base):
    __tablename__ = "daily_brief"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    # За какой день сводка (обычно вчера на момент генерации).
    date_for = Column(Date, nullable=False)
    # pending → generating → ready | error
    status = Column(String(16), nullable=False, default="pending")
    # Готовый markdown-текст сводки.
    text = Column(Text, nullable=True)
    error_message = Column(String(1000), nullable=True)
    generated_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("user_id", "date_for", name="uq_daily_brief_user_date"),
    )
