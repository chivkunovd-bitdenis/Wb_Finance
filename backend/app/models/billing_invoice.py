from sqlalchemy import JSON, CheckConstraint, Column, Date, DateTime, ForeignKey, Integer, Numeric, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class BillingInvoice(Base):
    """Счёт на оплату подписки: выставляется пользователю, оплачивается по банковским реквизитам."""

    __tablename__ = "billing_invoices"
    __table_args__ = (
        CheckConstraint("status IN ('issued', 'paid', 'cancelled')", name="ck_billing_invoices_status"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    number = Column(Integer, nullable=False, unique=True)
    number_display = Column(String(32), nullable=False)
    issued_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    period_start = Column(Date, nullable=False)
    period_end = Column(Date, nullable=False)
    marketplaces = Column(JSON, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    status = Column(String(16), nullable=False, default="issued", index=True)
    paid_at = Column(DateTime(timezone=True), nullable=True)
    buyer = Column(JSON, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
