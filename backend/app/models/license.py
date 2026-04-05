from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class License(Base):
    __tablename__ = "licenses"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    status = Column(String(32), nullable=False, default="inactive", index=True)
    valid_until = Column(DateTime(timezone=True), nullable=True)
    source = Column(String(32), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
