from sqlalchemy import Column, String, Date, DateTime, Integer, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class FunnelBackfillState(Base):
    """Прогресс фоновой догрузки воронки (sales-funnel/products) с начала календарного года."""

    __tablename__ = "funnel_backfill_state"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_year = Column(Integer, nullable=False)
    last_completed_date = Column(Date, nullable=True)
    status = Column(String(32), nullable=False, server_default="idle")
    error_message = Column(String(2000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "calendar_year", name="uq_funnel_backfill_user_year"),)
