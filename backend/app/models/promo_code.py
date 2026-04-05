from sqlalchemy import Boolean, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class PromoCode(Base):
    __tablename__ = "promo_codes"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    code = Column(String(32), unique=True, nullable=False, index=True)
    is_used = Column(Boolean, nullable=False, default=False)
    used_by_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    used_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
