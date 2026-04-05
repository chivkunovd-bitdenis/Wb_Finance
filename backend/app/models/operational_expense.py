from sqlalchemy import Column, Date, Numeric, ForeignKey, String, DateTime, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class OperationalExpense(Base):
    __tablename__ = "operational_expenses"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False, index=True)
    amount = Column(Numeric(14, 2), nullable=False, default=0)
    comment = Column(String(1000), nullable=True)

    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

