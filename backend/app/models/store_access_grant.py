from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class StoreAccessGrant(Base):
    __tablename__ = "store_access_grants"
    __table_args__ = (
        UniqueConstraint("store_owner_user_id", "viewer_user_id", name="uq_store_access_grant_owner_viewer"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)

    # Store/account being accessed
    store_owner_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)
    # User who can view/sync under that store
    viewer_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)

    status = Column(String(16), nullable=False, server_default="active")  # active | revoked
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

