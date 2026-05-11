"""competitor metrics import batches (accumulate imports)

Revision ID: f8a1c2d3e4b5
Revises: 976e4903b0a0
Create Date: 2026-05-11

"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "f8a1c2d3e4b5"
down_revision: Union[str, None] = "976e4903b0a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "ai_competitor_metrics",
        sa.Column("import_batch_id", postgresql.UUID(as_uuid=False), nullable=True),
    )
    op.add_column(
        "ai_competitor_comparison_reports",
        sa.Column("latest_import_batch_id", postgresql.UUID(as_uuid=False), nullable=True),
    )

    conn = op.get_bind()
    dist = conn.execute(sa.text("SELECT DISTINCT report_id FROM ai_competitor_metrics")).fetchall()
    for (rid,) in dist:
        bid = str(uuid.uuid4())
        conn.execute(
            sa.text("UPDATE ai_competitor_metrics SET import_batch_id = :bid WHERE report_id = :rid"),
            {"bid": bid, "rid": rid},
        )
        conn.execute(
            sa.text(
                "UPDATE ai_competitor_comparison_reports SET latest_import_batch_id = :bid WHERE id = :rid"
            ),
            {"bid": bid, "rid": rid},
        )

    op.alter_column("ai_competitor_metrics", "import_batch_id", nullable=False)
    op.drop_constraint("uq_ai_comp_metric_report_nm_code", "ai_competitor_metrics", type_="unique")
    op.create_unique_constraint(
        "uq_ai_comp_metric_report_nm_code_batch",
        "ai_competitor_metrics",
        ["report_id", "nm_id", "metric_code", "import_batch_id"],
    )
    op.create_index("ix_ai_competitor_metrics_import_batch_id", "ai_competitor_metrics", ["import_batch_id"])


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(
        sa.text(
            """
            DELETE FROM ai_competitor_metrics m
            USING ai_competitor_comparison_reports r
            WHERE m.report_id = r.id
              AND m.import_batch_id IS DISTINCT FROM r.latest_import_batch_id
            """
        )
    )

    op.drop_index("ix_ai_competitor_metrics_import_batch_id", table_name="ai_competitor_metrics")
    op.drop_constraint("uq_ai_comp_metric_report_nm_code_batch", "ai_competitor_metrics", type_="unique")
    op.create_unique_constraint(
        "uq_ai_comp_metric_report_nm_code",
        "ai_competitor_metrics",
        ["report_id", "nm_id", "metric_code"],
    )

    op.drop_column("ai_competitor_comparison_reports", "latest_import_batch_id")
    op.drop_column("ai_competitor_metrics", "import_batch_id")
