"""Initial schema — all tables and columns

Revision ID: 001
Revises: 
Create Date: 2026-04-16
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = '001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    # users
    op.create_table('users',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('email', sa.String, unique=True, index=True, nullable=False),
        sa.Column('first_name', sa.String),
        sa.Column('last_name', sa.String),
        sa.Column('role', sa.String, server_default='manager'),
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('hashed_password', sa.String),
        sa.Column('telegram_id', sa.String, unique=True),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('settings', JSONB),
    )

    # accounts
    op.create_table('accounts',
        sa.Column('id', sa.Integer, primary_key=True),
        sa.Column('name', sa.String, unique=True, index=True, nullable=False),
        sa.Column('domain', sa.String),
        sa.Column('airtable_base_id', sa.String),
        sa.Column('merchrules_login', sa.String),
        sa.Column('is_active', sa.Boolean, server_default='true'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('account_data', JSONB),
    )

    # clients
    op.create_table('clients',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('account_id', sa.Integer, sa.ForeignKey('accounts.id')),
        sa.Column('name', sa.String, index=True),
        sa.Column('domain', sa.String),
        sa.Column('segment', sa.String),
        sa.Column('manager_email', sa.String),
        sa.Column('airtable_record_id', sa.String, unique=True),
        sa.Column('merchrules_account_id', sa.String, unique=True),
        sa.Column('site_ids', JSONB),
        sa.Column('health_score', sa.Float, server_default='0.0'),
        sa.Column('revenue_trend', sa.String),
        sa.Column('activity_level', sa.String),
        sa.Column('last_meeting_date', sa.DateTime),
        sa.Column('last_checkup', sa.DateTime),
        sa.Column('needs_checkup', sa.Boolean, server_default='false'),
        sa.Column('open_tickets', sa.Integer, server_default='0'),
        sa.Column('last_ticket_date', sa.DateTime),
        sa.Column('last_sync_at', sa.DateTime),
        sa.Column('integration_metadata', JSONB),
        sa.Column('last_qbr_date', sa.DateTime),
        sa.Column('next_qbr_date', sa.DateTime),
        sa.Column('account_plan', JSONB),
    )

    # meetings
    op.create_table('meetings',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('date', sa.DateTime),
        sa.Column('type', sa.String),
        sa.Column('source', sa.String, server_default='internal'),
        sa.Column('title', sa.String),
        sa.Column('summary', sa.Text),
        sa.Column('transcript', sa.Text),
        sa.Column('recording_url', sa.String),
        sa.Column('transcript_url', sa.String),
        sa.Column('mood', sa.String),
        sa.Column('sentiment_score', sa.Float),
        sa.Column('attendees', JSONB),
        sa.Column('external_id', sa.String),
        sa.Column('followup_status', sa.String, server_default='pending'),
        sa.Column('followup_text', sa.Text),
        sa.Column('followup_sent_at', sa.DateTime),
        sa.Column('followup_skipped', sa.Boolean, server_default='false'),
        sa.Column('is_qbr', sa.Boolean, server_default='false'),
    )

    # tasks
    op.create_table('tasks',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('merchrules_task_id', sa.String),
        sa.Column('title', sa.String, nullable=False),
        sa.Column('description', sa.Text),
        sa.Column('status', sa.String, server_default='plan'),
        sa.Column('priority', sa.String, server_default='medium'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('due_date', sa.DateTime),
        sa.Column('source', sa.String, server_default='manual'),
        sa.Column('created_from_meeting_id', sa.Integer, sa.ForeignKey('meetings.id')),
        sa.Column('team', sa.String),
        sa.Column('task_type', sa.String),
        sa.Column('confirmed_at', sa.DateTime),
        sa.Column('confirmed_by', sa.String),
        sa.Column('pushed_to_roadmap', sa.Boolean, server_default='false'),
        sa.Column('roadmap_pushed_at', sa.DateTime),
    )

    # checkups
    op.create_table('checkups',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('type', sa.String),
        sa.Column('status', sa.String),
        sa.Column('scheduled_date', sa.DateTime),
        sa.Column('completed_date', sa.DateTime),
        sa.Column('priority', sa.Integer, server_default='0'),
        sa.Column('merchrules_id', sa.String),
    )

    # qbrs
    op.create_table('qbrs',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('quarter', sa.String, nullable=False),
        sa.Column('year', sa.Integer, nullable=False),
        sa.Column('date', sa.DateTime),
        sa.Column('status', sa.String, server_default='draft'),
        sa.Column('metrics', JSONB),
        sa.Column('summary', sa.Text),
        sa.Column('achievements', JSONB),
        sa.Column('issues', JSONB),
        sa.Column('next_quarter_goals', JSONB),
        sa.Column('presentation_url', sa.String),
        sa.Column('executive_summary', sa.Text),
        sa.Column('future_work', JSONB),
        sa.Column('key_insights', JSONB),
        sa.Column('meeting_id', sa.Integer, sa.ForeignKey('meetings.id')),
    )

    # account_plans
    op.create_table('account_plans',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), unique=True),
        sa.Column('quarterly_goals', JSONB),
        sa.Column('action_items', JSONB),
        sa.Column('notes', sa.Text),
        sa.Column('strategy', sa.Text),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_by', sa.String),
    )

    # user_client_assignment
    op.create_table('user_client_assignment',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id'), index=True),
        sa.Column('assigned_at', sa.DateTime, server_default=sa.func.now()),
    )

    # audit_logs
    op.create_table('audit_logs',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('action', sa.String),
        sa.Column('resource_type', sa.String),
        sa.Column('resource_id', sa.Integer),
        sa.Column('old_values', JSONB),
        sa.Column('new_values', JSONB),
        sa.Column('ip_address', sa.String),
        sa.Column('user_agent', sa.String),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), index=True),
    )

    # notifications
    op.create_table('notifications',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id'), index=True),
        sa.Column('title', sa.String),
        sa.Column('message', sa.Text),
        sa.Column('type', sa.String),
        sa.Column('is_read', sa.Boolean, server_default='false'),
        sa.Column('related_resource_type', sa.String),
        sa.Column('related_resource_id', sa.Integer),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now(), index=True),
        sa.Column('read_at', sa.DateTime),
    )

    # sync_logs
    op.create_table('sync_logs',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('integration', sa.String, index=True),
        sa.Column('resource_type', sa.String),
        sa.Column('action', sa.String),
        sa.Column('status', sa.String),
        sa.Column('message', sa.Text),
        sa.Column('records_processed', sa.Integer, server_default='0'),
        sa.Column('errors_count', sa.Integer, server_default='0'),
        sa.Column('started_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('completed_at', sa.DateTime),
        sa.Column('sync_data', JSONB),
    )

    # client_notes
    op.create_table('client_notes',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('is_pinned', sa.Boolean, server_default='false'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime, server_default=sa.func.now()),
    )

    # task_comments
    op.create_table('task_comments',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('task_id', sa.Integer, sa.ForeignKey('tasks.id')),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    # followup_templates
    op.create_table('followup_templates',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('name', sa.String, nullable=False),
        sa.Column('content', sa.Text, nullable=False),
        sa.Column('category', sa.String, server_default='general'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )

    # voice_notes
    op.create_table('voice_notes',
        sa.Column('id', sa.Integer, primary_key=True, index=True),
        sa.Column('meeting_id', sa.Integer, sa.ForeignKey('meetings.id')),
        sa.Column('client_id', sa.Integer, sa.ForeignKey('clients.id')),
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id')),
        sa.Column('audio_url', sa.String),
        sa.Column('transcription', sa.Text),
        sa.Column('duration_seconds', sa.Integer, server_default='0'),
        sa.Column('created_at', sa.DateTime, server_default=sa.func.now()),
    )


def downgrade():
    op.drop_table('voice_notes')
    op.drop_table('followup_templates')
    op.drop_table('task_comments')
    op.drop_table('client_notes')
    op.drop_table('sync_logs')
    op.drop_table('notifications')
    op.drop_table('audit_logs')
    op.drop_table('user_client_assignment')
    op.drop_table('account_plans')
    op.drop_table('qbrs')
    op.drop_table('checkups')
    op.drop_table('tasks')
    op.drop_table('meetings')
    op.drop_table('clients')
    op.drop_table('accounts')
    op.drop_table('users')
