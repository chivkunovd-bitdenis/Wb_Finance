from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class FinanceBackfillState(Base):
    """
    Прогресс фоновой догрузки финансовых данных (sales+ads → pnl/sku) ретроспективно по году.

    last_completed_date:
    - в режиме ретроспективной загрузки это "курсор" — последний полностью обработанный день (двигается назад).
    - при старте (None) считается, что курсор = through_date (вчера/конец года).
    """

    __tablename__ = "finance_backfill_state"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    calendar_year = Column(Integer, nullable=False)
    last_completed_date = Column(Date, nullable=True)
    status = Column(String(32), nullable=False, server_default="idle")  # idle, running, complete, error
    error_message = Column(String(2000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "calendar_year", name="uq_finance_backfill_user_year"),)
