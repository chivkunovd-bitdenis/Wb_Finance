from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class Subscription(Base):
    __tablename__ = "subscriptions"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    status = Column(String(32), nullable=False, default="trial", index=True)
    trial_started_at = Column(DateTime(timezone=True), nullable=True)
    trial_ends_at = Column(DateTime(timezone=True), nullable=True)
    current_period_start = Column(DateTime(timezone=True), nullable=True)
    current_period_end = Column(DateTime(timezone=True), nullable=True)
    auto_renew = Column(Boolean, nullable=False, default=True)
    provider = Column(String(32), nullable=False, default="yookassa")
    provider_customer_id = Column(String(255), nullable=True)
    provider_subscription_id = Column(String(255), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
