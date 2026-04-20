"""Auto task rules — actions, trigger_count, last_triggered_at

Revision ID: 005_auto_task_rule_actions
Revises: 004_client_hub_overview
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '005_auto_task_rule_actions'
down_revision = '004_client_hub_overview'
branch_labels = None
depends_on = None


def upgrade():
    # ── Расширение AutoTaskRule ──────────────────────────────────────────────
    op.add_column('auto_task_rules', sa.Column('actions', JSONB, server_default=sa.text("'[]'::jsonb"), nullable=True))
    op.add_column('auto_task_rules', sa.Column('trigger_count', sa.Integer, server_default='0', nullable=True))
    op.add_column('auto_task_rules', sa.Column('last_triggered_at', sa.DateTime, nullable=True))


def downgrade():
    op.drop_column('auto_task_rules', 'last_triggered_at')
    op.drop_column('auto_task_rules', 'trigger_count')
    op.drop_column('auto_task_rules', 'actions')
