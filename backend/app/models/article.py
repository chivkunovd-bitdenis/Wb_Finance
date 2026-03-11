from sqlalchemy import Column, String, DateTime, BigInteger, Numeric, ForeignKey, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class Article(Base):
    __tablename__ = "articles"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    nm_id = Column(BigInteger, nullable=False)
    vendor_code = Column(String(255), nullable=True)
    name = Column(String(1000), nullable=True)
    subject_name = Column(String(500), nullable=True)
    cost_price = Column(Numeric(14, 2), nullable=True, default=0)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "nm_id", name="uq_articles_user_nm"),)
