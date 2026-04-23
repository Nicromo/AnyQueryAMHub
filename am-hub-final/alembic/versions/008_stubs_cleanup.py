"""Stubs cleanup: RoadmapItem.author_id, FollowupTemplate.usage_count/last_used_at

Revision ID: 008_stubs_cleanup
Revises: 007_checkup_v2
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa


revision = '008_stubs_cleanup'
down_revision = '007_checkup_v2'
branch_labels = None
depends_on = None


def upgrade():
    # RoadmapItem.author_id — для ownership-проверки на delete/update.
    op.add_column(
        'roadmap_items',
        sa.Column('author_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
    )
    op.create_index(
        'ix_roadmap_items_author_id', 'roadmap_items', ['author_id']
    )

    # FollowupTemplate.usage_count / last_used_at — честный счётчик использований.
    op.add_column(
        'followup_templates',
        sa.Column('usage_count', sa.Integer(), server_default='0', nullable=False),
    )
    op.add_column(
        'followup_templates',
        sa.Column('last_used_at', sa.DateTime(), nullable=True),
    )


def downgrade():
    op.drop_column('followup_templates', 'last_used_at')
    op.drop_column('followup_templates', 'usage_count')
    op.drop_index('ix_roadmap_items_author_id', table_name='roadmap_items')
    op.drop_column('roadmap_items', 'author_id')
