"""competitor metrics import batches (accumulate imports)

Revision ID: f8a1c2d3e4b5
Revises: 976e4903b0a0
Create Date: 2026-05-11

Idempotent on PostgreSQL: local/test DB may already have ``import_batch_id`` from
additive DDL while ``alembic_version`` was still behind this revision.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f8a1c2d3e4b5"
down_revision: Union[str, None] = "976e4903b0a0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        sa.text(
            "ALTER TABLE ai_competitor_metrics ADD COLUMN IF NOT EXISTS import_batch_id UUID"
        )
    )
    op.execute(
        sa.text(
            "ALTER TABLE ai_competitor_comparison_reports ADD COLUMN IF NOT EXISTS "
            "latest_import_batch_id UUID"
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE r RECORD;
            DECLARE bid uuid;
            BEGIN
              FOR r IN
                SELECT DISTINCT report_id FROM ai_competitor_metrics WHERE import_batch_id IS NULL
              LOOP
                bid := gen_random_uuid();
                UPDATE ai_competitor_metrics
                  SET import_batch_id = bid
                  WHERE report_id = r.report_id AND import_batch_id IS NULL;
                UPDATE ai_competitor_comparison_reports
                  SET latest_import_batch_id = bid
                  WHERE id = r.report_id;
              END LOOP;
            END $$;
            """
        )
    )
    op.execute(
        sa.text(
            """
            UPDATE ai_competitor_comparison_reports r
            SET latest_import_batch_id = s.import_batch_id
            FROM (
              SELECT DISTINCT ON (report_id) report_id, import_batch_id
              FROM ai_competitor_metrics
              ORDER BY report_id, created_at DESC NULLS LAST, id DESC
            ) s
            WHERE r.id = s.report_id AND r.latest_import_batch_id IS NULL
            """
        )
    )
    op.execute(sa.text("ALTER TABLE ai_competitor_metrics ALTER COLUMN import_batch_id SET NOT NULL"))

    op.execute(sa.text("ALTER TABLE ai_competitor_metrics DROP CONSTRAINT IF EXISTS uq_ai_comp_metric_report_nm_code"))
    op.execute(sa.text("DROP INDEX IF EXISTS ux_ai_comp_metric_report_nm_code"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS uq_ai_comp_metric_report_nm_code_batch "
            "ON ai_competitor_metrics (report_id, nm_id, metric_code, import_batch_id)"
        )
    )
    op.execute(
        sa.text(
            "CREATE INDEX IF NOT EXISTS ix_ai_competitor_metrics_import_batch_id "
            "ON ai_competitor_metrics (import_batch_id)"
        )
    )


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

    op.execute(sa.text("DROP INDEX IF EXISTS ix_ai_competitor_metrics_import_batch_id"))
    op.execute(sa.text("DROP INDEX IF EXISTS uq_ai_comp_metric_report_nm_code_batch"))
    op.execute(
        sa.text(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_ai_comp_metric_report_nm_code "
            "ON ai_competitor_metrics (report_id, nm_id, metric_code)"
        )
    )

    op.execute(sa.text("ALTER TABLE ai_competitor_comparison_reports DROP COLUMN IF EXISTS latest_import_batch_id"))
    op.execute(sa.text("ALTER TABLE ai_competitor_metrics DROP COLUMN IF EXISTS import_batch_id"))
