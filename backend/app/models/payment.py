from sqlalchemy import Column, DateTime, ForeignKey, Numeric, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class Payment(Base):
    __tablename__ = "payments"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    subscription_id = Column(UUID(as_uuid=False), ForeignKey("subscriptions.id", ondelete="SET NULL"), nullable=True, index=True)
    provider = Column(String(32), nullable=False, default="yookassa")
    provider_payment_id = Column(String(255), nullable=False, index=True)
    idempotency_key = Column(String(255), nullable=False, unique=True)
    amount = Column(Numeric(14, 2), nullable=False)
    currency = Column(String(8), nullable=False, default="RUB")
    status = Column(String(32), nullable=False, index=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    raw_payload = Column(JSONB, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
