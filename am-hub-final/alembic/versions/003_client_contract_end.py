"""Add Client.contract_end for renewal alerts

Revision ID: 003
Revises: 002
Create Date: 2026-04-19
"""
from alembic import op
import sqlalchemy as sa


revision = '003'
down_revision = '002'
branch_labels = None
depends_on = None


def upgrade():
    # Дата окончания текущего контракта — для алертов 30/14/7 дней.
    op.add_column(
        'clients',
        sa.Column('contract_end', sa.Date(), nullable=True),
    )
    op.create_index(
        'ix_clients_contract_end',
        'clients',
        ['contract_end'],
    )


def downgrade():
    op.drop_index('ix_clients_contract_end', table_name='clients')
    op.drop_column('clients', 'contract_end')
