from sqlalchemy import Column, String, Date, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class FunnelRollingSyncState(Base):
    """
    Состояние rolling-синка воронки за последние 7 дней.

    Нужен для single-flight per user и для последовательного "repair хвоста" по дням.
    """

    __tablename__ = "funnel_rolling_sync_state"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status = Column(String(32), nullable=False, server_default="idle")  # idle|running|error
    last_completed_date = Column(Date, nullable=True)
    error_message = Column(String(2000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", name="uq_funnel_rolling_user"),)

