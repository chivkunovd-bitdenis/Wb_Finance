from sqlalchemy import Column, Date, BigInteger, Numeric, ForeignKey, Index
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class RawAd(Base):
    __tablename__ = "raw_ads"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date = Column(Date, nullable=False)
    nm_id = Column(BigInteger, nullable=True)
    campaign_id = Column(BigInteger, nullable=True)
    spend = Column(Numeric(14, 2), nullable=True)

    __table_args__ = (Index("ix_raw_ads_user_date", "user_id", "date"),)
