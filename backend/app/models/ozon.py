from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID

from app.models.base import Base, uuid_gen


class OzonConnection(Base):
    """Encrypted Ozon seller credentials and last synchronization status per owner."""

    __tablename__ = "ozon_connections"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    client_id_enc = Column(Text, nullable=False)
    api_key_enc = Column(Text, nullable=False)
    accrual_types = Column(JSONB, nullable=True)
    status = Column(String(24), nullable=False, server_default="configured")
    last_error = Column(String(1000), nullable=True)
    last_success_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "status in ('configured','syncing','ready','error','needs_reauth')",
            name="ck_ozon_connections_status",
        ),
    )


class OzonAccrualSnapshot(Base):
    """One raw source accrual, replaced only when its complete source day succeeds."""

    __tablename__ = "ozon_accrual_snapshots"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    accrual_id = Column(String(160), nullable=False)
    accrual_date = Column(Date, nullable=False)
    category = Column(String(40), nullable=True)
    currency = Column(String(12), nullable=True)
    raw_payload = Column(JSONB, nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "accrual_id", name="uq_ozon_accrual_user_source_id"),
        Index("ix_ozon_accrual_user_date", "user_id", "accrual_date"),
    )


class OzonSyncDay(Base):
    """Coverage and normalized, complete-day view for one owner and calendar day."""

    __tablename__ = "ozon_sync_days"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    day = Column(Date, nullable=False)
    # status describes the retained snapshot; a failed refresh does not erase prior coverage.
    status = Column(String(16), nullable=False, server_default="missing")
    last_attempt_status = Column(String(16), nullable=True)
    source_count = Column(Integer, nullable=True)
    daily_data = Column(JSONB, nullable=True)
    products_data = Column(JSONB, nullable=True)
    accrual_types = Column(JSONB, nullable=True)
    warnings = Column(JSONB, nullable=False, server_default="[]")
    last_error = Column(String(1000), nullable=True)
    completed_at = Column(DateTime(timezone=True), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "day", name="uq_ozon_sync_day_owner_day"),
        CheckConstraint("status in ('missing','complete','empty')", name="ck_ozon_sync_day_status"),
        CheckConstraint(
            "last_attempt_status is null or last_attempt_status in ('queued','running','succeeded','failed')",
            name="ck_ozon_sync_day_attempt_status",
        ),
        Index("ix_ozon_sync_day_owner_day", "user_id", "day"),
    )


class OzonProduct(Base):
    """Ozon catalogue identity; SKU remains the product key and offer_id is metadata."""

    __tablename__ = "ozon_products"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(64), nullable=False)
    offer_id = Column(String(255), nullable=True)
    name = Column(String(1000), nullable=True)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (UniqueConstraint("user_id", "sku", name="uq_ozon_product_user_sku"),)


class OzonProductCost(Base):
    """Explicit owner-entered Ozon cost; absence means unknown, zero is an explicit value."""

    __tablename__ = "ozon_product_costs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    sku = Column(String(64), nullable=False)
    cost_price = Column(Numeric(14, 2), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "sku", name="uq_ozon_product_cost_user_sku"),
        CheckConstraint("cost_price >= 0", name="ck_ozon_product_cost_nonnegative"),
    )


class OzonFinanceSettings(Base):
    """Owner attestation about completeness of Ozon operating expenses."""

    __tablename__ = "ozon_finance_settings"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    # True records the owner's attestation that all Ozon operating expenses are entered.
    operation_expenses_confirmed = Column(
        Boolean, nullable=False, server_default="false"
    )
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

class OzonOperationalExpense(Base):
    """Owner-entered daily Ozon-only operating expense, isolated from WB."""

    __tablename__ = "ozon_operational_expenses"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(
        UUID(as_uuid=False),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    date = Column(Date, nullable=False)
    amount = Column(Numeric(14, 2), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        UniqueConstraint("user_id", "date", name="uq_ozon_operational_expense_user_date"),
        CheckConstraint("amount >= 0", name="ck_ozon_operational_expense_nonnegative"),
        Index("ix_ozon_operational_expense_user_date", "user_id", "date"),
    )


class OzonSyncRun(Base):
    """Bounded manual synchronization run; only the run identifier enters Celery."""

    __tablename__ = "ozon_sync_runs"

    id = Column(UUID(as_uuid=False), primary_key=True, default=uuid_gen)
    user_id = Column(UUID(as_uuid=False), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    date_from = Column(Date, nullable=False)
    date_to = Column(Date, nullable=False)
    status = Column(String(16), nullable=False, server_default="queued")
    # manual: owner button; backfill: automatic year-to-date chain; daily: beat refresh of recent days.
    trigger = Column(String(16), nullable=False, server_default="manual")
    progress = Column(Integer, nullable=False, server_default="0")
    error_message = Column(String(1000), nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)

    __table_args__ = (
        CheckConstraint("date_from <= date_to", name="ck_ozon_sync_run_valid_range"),
        CheckConstraint("status in ('queued','running','complete','partial','failed')", name="ck_ozon_sync_run_status"),
        CheckConstraint("trigger in ('manual','backfill','daily')", name="ck_ozon_sync_run_trigger"),
        CheckConstraint("progress >= 0", name="ck_ozon_sync_run_progress_nonnegative"),
        Index(
            "uq_ozon_sync_run_one_active_per_owner",
            "user_id",
            unique=True,
            postgresql_where=(status.in_(("queued", "running"))),
        ),
    )
