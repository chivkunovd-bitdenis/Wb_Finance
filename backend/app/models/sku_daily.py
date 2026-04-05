"""Витрина: P&L и воронка по артикулам по дням (user × date × nm_id). Для быстрых выборок time-series по SKU."""
from sqlalchemy import Column, Date, BigInteger, Numeric, Integer, ForeignKey, UniqueConstraint, Index
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class SkuDaily(Base):
    __tablename__ = "sku_daily"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    nm_id = Column(BigInteger, nullable=False)
    revenue = Column(Numeric(14, 2), nullable=True)
    commission = Column(Numeric(14, 2), nullable=True)
    logistics = Column(Numeric(14, 2), nullable=True)
    penalties = Column(Numeric(14, 2), nullable=True)
    storage = Column(Numeric(14, 2), nullable=True)
    ads_spend = Column(Numeric(14, 2), nullable=True)
    cogs = Column(Numeric(14, 2), nullable=True)
    tax = Column(Numeric(14, 2), nullable=True)
    margin = Column(Numeric(14, 2), nullable=True)
    open_count = Column(Integer, nullable=True)
    cart_count = Column(Integer, nullable=True)
    order_count = Column(Integer, nullable=True)
    order_sum = Column(Numeric(14, 2), nullable=True)

    __table_args__ = (
        Index("ix_sku_daily_user_date", "user_id", "date", unique=False),
        UniqueConstraint("user_id", "date", "nm_id", name="uq_sku_daily_user_date_nm"),
    )
