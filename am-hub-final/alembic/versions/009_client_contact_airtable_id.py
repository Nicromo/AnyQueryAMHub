"""ClientContact.airtable_record_id — для идемпотентного upsert linked-контактов.

Revision ID: 009_client_contact_airtable_id
Revises: 008_stubs_cleanup
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa


revision = '009_client_contact_airtable_id'
down_revision = '008_stubs_cleanup'
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        'client_contacts',
        sa.Column('airtable_record_id', sa.String(), nullable=True),
    )
    op.create_index(
        'ix_client_contacts_airtable_record_id',
        'client_contacts',
        ['airtable_record_id'],
    )


def downgrade():
    op.drop_index('ix_client_contacts_airtable_record_id', table_name='client_contacts')
    op.drop_column('client_contacts', 'airtable_record_id')
