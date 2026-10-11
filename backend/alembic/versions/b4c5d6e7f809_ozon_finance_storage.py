"""isolated Ozon finance ledger, credentials, and coverage

Revision ID: b4c5d6e7f809
Revises: a3f7c9e1d2b4
Create Date: 2026-10-11

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "b4c5d6e7f809"
down_revision: Union[str, None] = "a3f7c9e1d2b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "ozon_connections",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("client_id_enc", sa.Text(), nullable=False),
        sa.Column("api_key_enc", sa.Text(), nullable=False),
        sa.Column("accrual_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("status", sa.String(length=24), server_default="configured", nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "status in ('configured','syncing','ready','error','needs_reauth')",
            name="ck_ozon_connections_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id"),
    )
    op.create_index(op.f("ix_ozon_connections_user_id"), "ozon_connections", ["user_id"], unique=False)

    op.create_table(
        "ozon_accrual_snapshots",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("accrual_id", sa.String(length=160), nullable=False),
        sa.Column("accrual_date", sa.Date(), nullable=False),
        sa.Column("category", sa.String(length=40), nullable=True),
        sa.Column("currency", sa.String(length=12), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "accrual_id", name="uq_ozon_accrual_user_source_id"),
    )
    op.create_index(op.f("ix_ozon_accrual_snapshots_user_id"), "ozon_accrual_snapshots", ["user_id"], unique=False)
    op.create_index("ix_ozon_accrual_user_date", "ozon_accrual_snapshots", ["user_id", "accrual_date"], unique=False)

    op.create_table(
        "ozon_sync_days",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="missing", nullable=False),
        sa.Column("last_attempt_status", sa.String(length=16), nullable=True),
        sa.Column("source_count", sa.Integer(), nullable=True),
        sa.Column("daily_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("products_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("accrual_types", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("warnings", postgresql.JSONB(astext_type=sa.Text()), server_default="[]", nullable=False),
        sa.Column("last_error", sa.String(length=1000), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("status in ('missing','complete','empty')", name="ck_ozon_sync_day_status"),
        sa.CheckConstraint(
            "last_attempt_status is null or last_attempt_status in ('queued','running','succeeded','failed')",
            name="ck_ozon_sync_day_attempt_status",
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "day", name="uq_ozon_sync_day_owner_day"),
    )
    op.create_index(op.f("ix_ozon_sync_days_user_id"), "ozon_sync_days", ["user_id"], unique=False)
    op.create_index("ix_ozon_sync_day_owner_day", "ozon_sync_days", ["user_id", "day"], unique=False)

    op.create_table(
        "ozon_products",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("offer_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=1000), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sku", name="uq_ozon_product_user_sku"),
    )
    op.create_index(op.f("ix_ozon_products_user_id"), "ozon_products", ["user_id"], unique=False)

    op.create_table(
        "ozon_product_costs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("cost_price", sa.Numeric(precision=14, scale=2), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("cost_price >= 0", name="ck_ozon_product_cost_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sku", name="uq_ozon_product_cost_user_sku"),
    )
    op.create_index(op.f("ix_ozon_product_costs_user_id"), "ozon_product_costs", ["user_id"], unique=False)

    op.create_table(
        "ozon_sync_runs",
        sa.Column("id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=16), server_default="queued", nullable=False),
        sa.Column("progress", sa.Integer(), server_default="0", nullable=False),
        sa.Column("error_message", sa.String(length=1000), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint("date_from <= date_to", name="ck_ozon_sync_run_valid_range"),
        sa.CheckConstraint("status in ('queued','running','complete','partial','failed')", name="ck_ozon_sync_run_status"),
        sa.CheckConstraint("progress >= 0", name="ck_ozon_sync_run_progress_nonnegative"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_ozon_sync_runs_user_id"), "ozon_sync_runs", ["user_id"], unique=False)
    op.create_index(
        "uq_ozon_sync_run_one_active_per_owner",
        "ozon_sync_runs",
        ["user_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('queued', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("uq_ozon_sync_run_one_active_per_owner", table_name="ozon_sync_runs")
    op.drop_index(op.f("ix_ozon_sync_runs_user_id"), table_name="ozon_sync_runs")
    op.drop_table("ozon_sync_runs")
    op.drop_index(op.f("ix_ozon_product_costs_user_id"), table_name="ozon_product_costs")
    op.drop_table("ozon_product_costs")
    op.drop_index(op.f("ix_ozon_products_user_id"), table_name="ozon_products")
    op.drop_table("ozon_products")
    op.drop_index("ix_ozon_sync_day_owner_day", table_name="ozon_sync_days")
    op.drop_index(op.f("ix_ozon_sync_days_user_id"), table_name="ozon_sync_days")
    op.drop_table("ozon_sync_days")
    op.drop_index("ix_ozon_accrual_user_date", table_name="ozon_accrual_snapshots")
    op.drop_index(op.f("ix_ozon_accrual_snapshots_user_id"), table_name="ozon_accrual_snapshots")
    op.drop_table("ozon_accrual_snapshots")
    op.drop_index(op.f("ix_ozon_connections_user_id"), table_name="ozon_connections")
    op.drop_table("ozon_connections")
