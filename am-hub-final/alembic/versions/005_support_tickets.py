"""Support tickets — SupportTicket + TicketComment (Tbank Time / Mattermost)

Revision ID: 005_support_tickets
Revises: 004_auto_task_rule_actions
Create Date: 2026-04-20
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '005_support_tickets'
down_revision = '004_auto_task_rule_actions'
branch_labels = None
depends_on = None


def upgrade():
    # ── support_tickets ──────────────────────────────────────────────────────
    op.create_table('support_tickets',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), nullable=True),
        sa.Column('source', sa.String, server_default='tbank_time'),
        sa.Column('external_id', sa.String, nullable=True),
        sa.Column('external_url', sa.String, nullable=True),
        sa.Column('channel_id', sa.String, nullable=True),
        sa.Column('title', sa.String, nullable=True),
        sa.Column('body', sa.Text, nullable=True),
        sa.Column('status', sa.String, server_default='open'),
        sa.Column('priority', sa.String, server_default='normal'),
        sa.Column('author', sa.String, nullable=True),
        sa.Column('author_name', sa.String, nullable=True),
        sa.Column('external_client_id', sa.String, nullable=True),
        sa.Column('comments_count', sa.Integer, server_default='0'),
        sa.Column('last_comment_at', sa.DateTime, nullable=True),
        sa.Column('last_comment_snippet', sa.Text, nullable=True),
        sa.Column('opened_at', sa.DateTime, nullable=True),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('resolved_at', sa.DateTime, nullable=True),
        sa.Column('raw', JSONB, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index('ix_support_tickets_client_id', 'support_tickets', ['client_id'])
    op.create_index('ix_support_tickets_external_id', 'support_tickets', ['external_id'], unique=True)
    op.create_index('ix_support_tickets_external_client_id', 'support_tickets', ['external_client_id'])
    op.create_index('ix_support_tickets_opened_at', 'support_tickets', ['opened_at'])

    # ── ticket_comments ──────────────────────────────────────────────────────
    op.create_table('ticket_comments',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('ticket_id', sa.Integer,
                  sa.ForeignKey('support_tickets.id', ondelete='CASCADE'), nullable=True),
        sa.Column('external_id', sa.String, nullable=True),
        sa.Column('author', sa.String, nullable=True),
        sa.Column('author_name', sa.String, nullable=True),
        sa.Column('body', sa.Text, nullable=True),
        sa.Column('posted_at', sa.DateTime, nullable=True),
        sa.Column('raw', JSONB, server_default=sa.text("'{}'::jsonb")),
    )
    op.create_index('ix_ticket_comments_ticket_id', 'ticket_comments', ['ticket_id'])
    op.create_index('ix_ticket_comments_external_id', 'ticket_comments', ['external_id'], unique=True)


def downgrade():
    op.drop_index('ix_ticket_comments_external_id')
    op.drop_index('ix_ticket_comments_ticket_id')
    op.drop_table('ticket_comments')

    op.drop_index('ix_support_tickets_opened_at')
    op.drop_index('ix_support_tickets_external_client_id')
    op.drop_index('ix_support_tickets_external_id')
    op.drop_index('ix_support_tickets_client_id')
    op.drop_table('support_tickets')
