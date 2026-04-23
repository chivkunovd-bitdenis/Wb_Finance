from sqlalchemy import Column, Date, DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class FinanceMissingSyncState(Base):
    """
    Дедуп/прогресс для точечной догрузки финансов по диапазону (missing-tail, обычно вчера).

    Unique (user_id, date_from, date_to) позволяет безопасно не плодить одинаковые задачи
    при refresh'ах и параллельных входах.
    """

    __tablename__ = "finance_missing_sync_state"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)

    status = Column(String(32), nullable=False, server_default="idle")  # idle, running, complete, error
    retry_count = Column(Integer, nullable=False, server_default="0")
    last_http_code = Column(Integer, nullable=True)
    next_run_at = Column(DateTime(timezone=True), nullable=True)
    error_message = Column(String(2000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "date_from", "date_to", name="uq_finance_missing_user_range"),
    )

