"""Add revenue, upsell, health snapshots, NPS tables

Revision ID: 002
Revises: 001
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '002'
down_revision = '001'
branch_labels = None
depends_on = None


def upgrade():
    op.create_table('revenue_entries',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('period', sa.String, nullable=False),
        sa.Column('mrr', sa.Float, server_default='0'),
        sa.Column('arr', sa.Float),
        sa.Column('currency', sa.String, server_default='RUB'),
        sa.Column('note', sa.Text),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_by', sa.String),
    )
    op.create_index('ix_revenue_client_period', 'revenue_entries', ['client_id', 'period'])

    op.create_table('upsell_events',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('event_type', sa.String, nullable=False),
        sa.Column('status', sa.String, server_default='identified'),
        sa.Column('amount_before', sa.Float),
        sa.Column('amount_after', sa.Float),
        sa.Column('delta', sa.Float),
        sa.Column('description', sa.Text),
        sa.Column('owner_email', sa.String),
        sa.Column('due_date', sa.DateTime),
        sa.Column('closed_at', sa.DateTime),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('created_by', sa.String),
    )

    op.create_table('health_snapshots',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('score', sa.Float, nullable=False),
        sa.Column('components', JSONB),
        sa.Column('calculated_at', sa.DateTime, server_default=sa.func.now(), index=True),
    )
    op.create_index('ix_health_snapshots_client_date', 'health_snapshots', ['client_id', 'calculated_at'])

    op.create_table('nps_entries',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('score', sa.Integer, nullable=False),
        sa.Column('type', sa.String, server_default='nps'),
        sa.Column('comment', sa.Text),
        sa.Column('source', sa.String, server_default='manual'),
        sa.Column('recorded_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('recorded_by', sa.String),
    )
    op.create_index('ix_nps_client_date', 'nps_entries', ['client_id', 'recorded_at'])

    # Add MRR column to clients for quick access
    op.add_column('clients', sa.Column('mrr', sa.Float, server_default='0'))
    op.add_column('clients', sa.Column('nps_last', sa.Integer))
    op.add_column('clients', sa.Column('nps_date', sa.DateTime))


def downgrade():
    op.drop_column('clients', 'nps_date')
    op.drop_column('clients', 'nps_last')
    op.drop_column('clients', 'mrr')
    op.drop_index('ix_nps_client_date')
    op.drop_table('nps_entries')
    op.drop_index('ix_health_snapshots_client_date')
    op.drop_table('health_snapshots')
    op.drop_table('upsell_events')
    op.drop_index('ix_revenue_client_period')
    op.drop_table('revenue_entries')
