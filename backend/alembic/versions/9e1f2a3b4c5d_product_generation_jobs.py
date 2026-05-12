"""product_generation_jobs table

Revision ID: 9e1f2a3b4c5d
Revises: 2d4f3a1c0b11
Create Date: 2026-05-12

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "9e1f2a3b4c5d"
down_revision: Union[str, None] = "2d4f3a1c0b11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "product_generation_jobs",
        sa.Column("id", postgresql.UUID(as_uuid=False), primary_key=True),
        sa.Column("user_id", postgresql.UUID(as_uuid=False), nullable=False),
        sa.Column("status", sa.String(32), nullable=False, server_default="draft"),
        sa.Column("pipeline_run_id", sa.String(64), nullable=True),
        sa.Column("vendor_code", sa.String(255), nullable=True),
        sa.Column("title", sa.String(1000), nullable=True),
        sa.Column("brand", sa.String(500), nullable=True),
        sa.Column("description_user", sa.Text(), nullable=True),
        sa.Column("seo_description", sa.Text(), nullable=True),
        sa.Column("price_kopeks", sa.Integer(), nullable=True),
        sa.Column("dimensions_length", sa.Numeric(12, 4), nullable=True),
        sa.Column("dimensions_width", sa.Numeric(12, 4), nullable=True),
        sa.Column("dimensions_height", sa.Numeric(12, 4), nullable=True),
        sa.Column("weight_brutto", sa.Numeric(12, 4), nullable=True),
        sa.Column("sizes_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reference_paths_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("selected_main_asset_id", sa.String(64), nullable=True),
        sa.Column("selected_series_asset_ids", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("wb_publish_error", sa.Text(), nullable=True),
        sa.Column("wb_response_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "status in ('draft', 'in_progress', 'error', 'ready_to_publish', 'published')",
            name="ck_product_generation_jobs_status",
        ),
    )
    op.create_index("ix_product_generation_jobs_user_id", "product_generation_jobs", ["user_id"])
    op.create_index("ix_product_generation_jobs_status", "product_generation_jobs", ["status"])
    op.create_index("ix_product_generation_jobs_created_at", "product_generation_jobs", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_product_generation_jobs_created_at", table_name="product_generation_jobs")
    op.drop_index("ix_product_generation_jobs_status", table_name="product_generation_jobs")
    op.drop_index("ix_product_generation_jobs_user_id", table_name="product_generation_jobs")
    op.drop_table("product_generation_jobs")
