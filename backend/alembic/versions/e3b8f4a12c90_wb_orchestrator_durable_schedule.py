"""Persist orchestrator deadlines instead of Celery ETA timers."""
import sqlalchemy as sa
from alembic import op

revision = "e3b8f4a12c90"
down_revision = "d8e9f0a1b2c3"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("wb_orchestrator_state", sa.Column("next_tick_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("wb_orchestrator_state", sa.Column("dispatch_token", sa.String(36), nullable=True))
    op.add_column("wb_orchestrator_state", sa.Column("dispatch_lease_until", sa.DateTime(timezone=True), nullable=True))
    op.create_index("ix_wb_orchestrator_state_next_tick_at", "wb_orchestrator_state", ["next_tick_at"])
    # Dispatcher computes the actual per-operation deadline before sending anything.
    op.execute("UPDATE wb_orchestrator_state SET next_tick_at = now() WHERE intents ? 'high' OR intents ? 'low'")


def downgrade():
    op.drop_index("ix_wb_orchestrator_state_next_tick_at", table_name="wb_orchestrator_state")
    for name in ("dispatch_lease_until", "dispatch_token", "next_tick_at"):
        op.drop_column("wb_orchestrator_state", name)
