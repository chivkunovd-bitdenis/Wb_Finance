from sqlalchemy import Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class StoreAccessAuditEvent(Base):
    __tablename__ = "store_access_audit_events"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)

    store_owner_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)
    viewer_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)
    actor_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)

    action = Column(String(16), nullable=False)  # grant | revoke
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)

