from sqlalchemy import Column, Date, Numeric, ForeignKey, UniqueConstraint
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class PnlDaily(Base):
    __tablename__ = "pnl_daily"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    revenue = Column(Numeric(14, 2), nullable=True)
    commission = Column(Numeric(14, 2), nullable=True)
    logistics = Column(Numeric(14, 2), nullable=True)
    penalties = Column(Numeric(14, 2), nullable=True)
    storage = Column(Numeric(14, 2), nullable=True)
    ads_spend = Column(Numeric(14, 2), nullable=True)
    cogs = Column(Numeric(14, 2), nullable=True)
    tax = Column(Numeric(14, 2), nullable=True)
    margin = Column(Numeric(14, 2), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "date", name="uq_pnl_daily_user_date"),)
