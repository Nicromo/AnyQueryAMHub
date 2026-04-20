"""Client hub overview — payment fields, contacts, products, merch rules, feeds

Revision ID: 004_client_hub_overview
Revises: 003
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '004_client_hub_overview'
down_revision = '003'
branch_labels = None
depends_on = None


def upgrade():
    # ── Платёжные поля в clients ─────────────────────────────────────────────
    op.add_column('clients', sa.Column('payment_status', sa.String, server_default='active'))
    op.add_column('clients', sa.Column('payment_due_date', sa.DateTime))
    op.add_column('clients', sa.Column('payment_amount', sa.Float))

    # ── client_contacts ──────────────────────────────────────────────────────
    op.create_table('client_contacts',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('role', sa.String),
        sa.Column('position', sa.String),
        sa.Column('email', sa.String),
        sa.Column('phone', sa.String),
        sa.Column('telegram', sa.String),
        sa.Column('is_primary', sa.Boolean, server_default='false'),
        sa.Column('notes', sa.Text),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_client_contacts_client_id', 'client_contacts', ['client_id'])

    # ── client_products ──────────────────────────────────────────────────────
    op.create_table('client_products',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('code', sa.String, nullable=False),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('status', sa.String, server_default='active'),
        sa.Column('activated_at', sa.DateTime),
        sa.Column('extra', JSONB),
    )
    op.create_index('ix_client_products_client_id', 'client_products', ['client_id'])

    # ── client_merch_rules ───────────────────────────────────────────────────
    op.create_table('client_merch_rules',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('merchrules_id', sa.String),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('rule_type', sa.String),
        sa.Column('status', sa.String, server_default='active'),
        sa.Column('priority', sa.Integer, server_default='0'),
        sa.Column('config', JSONB),
        sa.Column('last_synced', sa.DateTime),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )
    op.create_index('ix_client_merch_rules_client_id', 'client_merch_rules', ['client_id'])

    # ── client_feeds ─────────────────────────────────────────────────────────
    op.create_table('client_feeds',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('feed_type', sa.String, nullable=False),
        sa.Column('name', sa.String),
        sa.Column('url', sa.String),
        sa.Column('status', sa.String, server_default='ok'),
        sa.Column('last_updated', sa.DateTime),
        sa.Column('sku_count', sa.Integer, server_default='0'),
        sa.Column('errors_count', sa.Integer, server_default='0'),
        sa.Column('last_error', sa.Text),
        sa.Column('schedule', sa.String),
    )
    op.create_index('ix_client_feeds_client_id', 'client_feeds', ['client_id'])


def downgrade():
    op.drop_index('ix_client_feeds_client_id')
    op.drop_table('client_feeds')

    op.drop_index('ix_client_merch_rules_client_id')
    op.drop_table('client_merch_rules')

    op.drop_index('ix_client_products_client_id')
    op.drop_table('client_products')

    op.drop_index('ix_client_contacts_client_id')
    op.drop_table('client_contacts')

    op.drop_column('clients', 'payment_amount')
    op.drop_column('clients', 'payment_due_date')
    op.drop_column('clients', 'payment_status')
