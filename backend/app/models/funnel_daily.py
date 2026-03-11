from sqlalchemy import Column, Date, BigInteger, String, Integer, Numeric, DateTime, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class FunnelDaily(Base):
    __tablename__ = "funnel_daily"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    nm_id = Column(BigInteger, nullable=False)
    vendor_code = Column(String(255), nullable=True)
    open_count = Column(Integer, nullable=True, default=0)
    cart_count = Column(Integer, nullable=True, default=0)
    order_count = Column(Integer, nullable=True, default=0)
    order_sum = Column(Numeric(14, 2), nullable=True)
    buyout_percent = Column(Numeric(8, 2), nullable=True)
    cr_to_cart = Column(Numeric(8, 4), nullable=True)
    cr_to_order = Column(Numeric(8, 4), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "date", "nm_id", name="uq_funnel_daily_user_date_nm"),)
