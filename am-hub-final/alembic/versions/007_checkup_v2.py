"""Checkup v2 — модели CheckupV2 и CheckupQuery + Client.diginetica_api_key

Revision ID: 007_checkup_v2
Revises: 006_support_tickets
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '007_checkup_v2'
down_revision = '006_support_tickets'
branch_labels = None
depends_on = None


def upgrade():
    # Client.diginetica_api_key
    op.add_column('clients', sa.Column('diginetica_api_key', sa.String(), nullable=True))

    # checkups_v2
    op.create_table(
        'checkups_v2',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('name', sa.String(), nullable=False),
        sa.Column('frequency', sa.String(), server_default='monthly'),
        sa.Column('due_date', sa.DateTime(), nullable=True),
        sa.Column('partner_comment', sa.Text(), nullable=True),
        sa.Column('any_comment', sa.Text(), nullable=True),
        sa.Column('status', sa.String(), server_default='draft'),
        sa.Column('score', sa.Float(), nullable=True),
        sa.Column('score_max', sa.Float(), server_default='3.0'),
        sa.Column('tracking', JSONB(), server_default='{}'),
        sa.Column('uiux', JSONB(), server_default='{}'),
        sa.Column('recs', JSONB(), server_default='{}'),
        sa.Column('reviews', JSONB(), server_default='{}'),
        sa.Column('products_tab', JSONB(), server_default='{}'),
        sa.Column('debts', JSONB(), server_default='{}'),
        sa.Column('search_comment', sa.Text(), nullable=True),
        sa.Column('top_queries_comment', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(), server_default=sa.func.now()),
        sa.Column('created_by', sa.String(), nullable=True),
    )
    op.create_index('ix_checkups_v2_client_id', 'checkups_v2', ['client_id'])
    op.create_index('ix_checkups_v2_created_at', 'checkups_v2', ['created_at'])

    # checkup_queries
    op.create_table(
        'checkup_queries',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('checkup_id', sa.Integer(),
                  sa.ForeignKey('checkups_v2.id', ondelete='CASCADE'), nullable=True),
        sa.Column('group', sa.String(), server_default='top'),
        sa.Column('query', sa.String(), nullable=False),
        sa.Column('shows_count', sa.Integer(), server_default='0'),
        sa.Column('score', sa.Integer(), nullable=True),
        sa.Column('problem', sa.Text(), nullable=True),
        sa.Column('solution', sa.Text(), nullable=True),
        sa.Column('partner_comment', sa.Text(), nullable=True),
        sa.Column('diginetica_response', JSONB(), server_default='{}'),
        sa.Column('response_time_ms', sa.Integer(), nullable=True),
        sa.Column('results_count', sa.Integer(), server_default='0'),
        sa.Column('has_correction', sa.Boolean(), server_default='false'),
        sa.Column('checked_at', sa.DateTime(), nullable=True),
        sa.Column('created_at', sa.DateTime(), server_default=sa.func.now()),
    )
    op.create_index('ix_checkup_queries_checkup_id', 'checkup_queries', ['checkup_id'])


def downgrade():
    op.drop_index('ix_checkup_queries_checkup_id', table_name='checkup_queries')
    op.drop_table('checkup_queries')
    op.drop_index('ix_checkups_v2_created_at', table_name='checkups_v2')
    op.drop_index('ix_checkups_v2_client_id', table_name='checkups_v2')
    op.drop_table('checkups_v2')
    op.drop_column('clients', 'diginetica_api_key')
