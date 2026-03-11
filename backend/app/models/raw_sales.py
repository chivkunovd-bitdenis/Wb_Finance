from sqlalchemy import Column, String, Date, BigInteger, Numeric, Integer, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class RawSale(Base):
    __tablename__ = "raw_sales"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    nm_id = Column(BigInteger, nullable=False)
    doc_type = Column(String(100), nullable=True)
    retail_price = Column(Numeric(14, 2), nullable=True)
    ppvz_for_pay = Column(Numeric(14, 2), nullable=True)
    delivery_rub = Column(Numeric(14, 2), nullable=True)
    penalty = Column(Numeric(14, 2), nullable=True)
    additional_payment = Column(Numeric(14, 2), nullable=True)
    storage_fee = Column(Numeric(14, 2), nullable=True)
    quantity = Column(Integer, nullable=True, default=1)

    __table_args__ = (Index("ix_raw_sales_user_date", "user_id", "date"),)
