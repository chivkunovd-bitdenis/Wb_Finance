from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class BillingRequisites(Base):
    """Реквизиты покупателя для счёта на оплату (одна запись на пользователя)."""

    __tablename__ = "billing_requisites"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True)
    name = Column(String(255), nullable=False)
    inn = Column(String(12), nullable=False)
    kpp = Column(String(9), nullable=True)
    address = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
