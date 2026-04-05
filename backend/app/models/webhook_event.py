from sqlalchemy import Column, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class WebhookEvent(Base):
    __tablename__ = "webhook_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    provider = Column(String(32), nullable=False, index=True)
    event_key = Column(String(255), nullable=False, unique=True)
    payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
