from sqlalchemy import Column, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID

from app.models.base import Base, uuid_gen


class StoreInvite(Base):
    """
    Приглашение сотрудника в кабинет селлера.

    В таблице хранится только sha256 от токена (token_hash): сам токен показывается
    селлеру один раз, при создании приглашения.
    """

    __tablename__ = "store_invites"
    __table_args__ = (
        UniqueConstraint("token_hash", name="uq_store_invites_token_hash"),
    )

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)

    # Кабинет (владелец магазина), в который приглашают
    store_owner_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=False, index=True)
    # Email приглашённого, в нижнем регистре
    email = Column(String(255), nullable=False, index=True)
    # sha256 hex от токена; сам токен не хранится
    token_hash = Column(String(64), nullable=False)

    status = Column(String(16), nullable=False, server_default="pending")  # pending | accepted | revoked
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    accepted_at = Column(DateTime(timezone=True), nullable=True)
    accepted_user_id = Column(UUID(as_uuid=False), ForeignKey("users.id"), nullable=True)
