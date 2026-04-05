from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class ReminderLog(Base):
    __tablename__ = "reminder_logs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    reminder_type = Column(String(32), nullable=False)
    channel = Column(String(32), nullable=False)
    status = Column(String(32), nullable=False, default="scheduled")
    sent_at = Column(DateTime(timezone=True), nullable=True)
    due_at = Column(DateTime(timezone=True), nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
