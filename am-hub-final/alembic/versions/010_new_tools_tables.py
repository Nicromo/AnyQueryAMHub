"""New tools tables: hypotheses, tg_broadcast, client_context, jira_issues, gdrive_files, auto_followups.

Revision ID: 010_new_tools_tables
Revises: 009_client_contact_airtable_id
Create Date: 2026-04-23
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '010_new_tools_tables'
down_revision = '009_client_contact_airtable_id'
branch_labels = None
depends_on = None


def upgrade():
    # ── hypotheses ──────────────────────────────────────────────────────────
    op.create_table(
        'hypotheses',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='SET NULL'), nullable=True),
        sa.Column('title', sa.String(length=500), nullable=False),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('hypothesis_type', sa.String(length=50), nullable=True, server_default='ab'),
        sa.Column('status', sa.String(length=50), nullable=True, server_default='draft'),
        sa.Column('priority', sa.String(length=20), nullable=True, server_default='medium'),
        sa.Column('metrics', sa.Text(), nullable=True),
        sa.Column('expected_impact', sa.Text(), nullable=True),
        sa.Column('result', sa.Text(), nullable=True),
        sa.Column('start_date', sa.Date(), nullable=True),
        sa.Column('end_date', sa.Date(), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_hypotheses_client_id', 'hypotheses', ['client_id'])
    op.create_index('ix_hypotheses_status', 'hypotheses', ['status'])

    # ── tg_broadcasts ────────────────────────────────────────────────────────
    op.create_table(
        'tg_broadcasts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('message_text', sa.Text(), nullable=False),
        sa.Column('target_type', sa.String(length=50), nullable=True, server_default='all'),
        sa.Column('target_filter', sa.String(length=500), nullable=True),
        sa.Column('schedule_type', sa.String(length=50), nullable=True, server_default='manual'),
        sa.Column('cron_expr', sa.String(length=100), nullable=True),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── tg_broadcast_logs ────────────────────────────────────────────────────
    op.create_table(
        'tg_broadcast_logs',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('broadcast_id', sa.Integer(), sa.ForeignKey('tg_broadcasts.id', ondelete='CASCADE'), nullable=False),
        sa.Column('sent_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.Column('recipients_count', sa.Integer(), nullable=True, server_default='0'),
        sa.Column('status', sa.String(length=20), nullable=True, server_default='ok'),
        sa.Column('error', sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_tg_broadcast_logs_broadcast_id', 'tg_broadcast_logs', ['broadcast_id'])

    # ── client_contexts ──────────────────────────────────────────────────────
    op.create_table(
        'client_contexts',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False, unique=True),
        sa.Column('key_facts', sa.JSON(), nullable=True),
        sa.Column('pain_points', sa.JSON(), nullable=True),
        sa.Column('wins', sa.JSON(), nullable=True),
        sa.Column('risks', sa.JSON(), nullable=True),
        sa.Column('next_steps', sa.JSON(), nullable=True),
        sa.Column('notes', sa.Text(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.Column('edited_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('last_manual_edit', sa.DateTime(), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_client_contexts_client_id', 'client_contexts', ['client_id'])

    # ── jira_issues ──────────────────────────────────────────────────────────
    op.create_table(
        'jira_issues',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='SET NULL'), nullable=True),
        sa.Column('issue_key', sa.String(length=100), nullable=False),
        sa.Column('jira_id', sa.String(length=100), nullable=True),
        sa.Column('title', sa.String(length=1000), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('status', sa.String(length=100), nullable=True),
        sa.Column('priority', sa.String(length=50), nullable=True),
        sa.Column('issue_type', sa.String(length=50), nullable=True),
        sa.Column('assignee', sa.String(length=200), nullable=True),
        sa.Column('reporter', sa.String(length=200), nullable=True),
        sa.Column('project_key', sa.String(length=50), nullable=True),
        sa.Column('due_date', sa.Date(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('synced_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_jira_issues_client_id', 'jira_issues', ['client_id'])
    op.create_index('ix_jira_issues_issue_key', 'jira_issues', ['issue_key'], unique=True)

    # ── gdrive_files ─────────────────────────────────────────────────────────
    op.create_table(
        'gdrive_files',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='CASCADE'), nullable=False),
        sa.Column('gdrive_id', sa.String(length=200), nullable=False),
        sa.Column('file_name', sa.String(length=1000), nullable=True),
        sa.Column('mime_type', sa.String(length=200), nullable=True),
        sa.Column('web_url', sa.String(length=2000), nullable=True),
        sa.Column('linked_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.Column('linked_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_gdrive_files_client_id', 'gdrive_files', ['client_id'])

    # ── auto_followups ───────────────────────────────────────────────────────
    op.create_table(
        'auto_followups',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=500), nullable=False),
        sa.Column('trigger_type', sa.String(length=50), nullable=True, server_default='after_meeting'),
        sa.Column('delay_hours', sa.Integer(), nullable=True, server_default='24'),
        sa.Column('message_template', sa.Text(), nullable=False),
        sa.Column('is_active', sa.Boolean(), nullable=True, server_default='true'),
        sa.Column('created_by', sa.Integer(), sa.ForeignKey('users.id', ondelete='SET NULL'), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )

    # ── auto_followup_executions ─────────────────────────────────────────────
    op.create_table(
        'auto_followup_executions',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('followup_id', sa.Integer(), sa.ForeignKey('auto_followups.id', ondelete='CASCADE'), nullable=False),
        sa.Column('client_id', sa.Integer(), sa.ForeignKey('clients.id', ondelete='SET NULL'), nullable=True),
        sa.Column('status', sa.String(length=20), nullable=True, server_default='sent'),
        sa.Column('message_sent', sa.Text(), nullable=True),
        sa.Column('error', sa.Text(), nullable=True),
        sa.Column('executed_at', sa.DateTime(), nullable=True, server_default=sa.text('now()')),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index('ix_auto_followup_executions_followup_id', 'auto_followup_executions', ['followup_id'])


def downgrade():
    op.drop_table('auto_followup_executions')
    op.drop_table('auto_followups')
    op.drop_table('gdrive_files')
    op.drop_table('jira_issues')
    op.drop_table('client_contexts')
    op.drop_table('tg_broadcast_logs')
    op.drop_table('tg_broadcasts')
    op.drop_table('hypotheses')
