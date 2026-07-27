"""add knowledge chunk table for organizational knowledge hub

Revision ID: d2e3f4a5b6c7
Revises: c7d8e9f0a1b2
Create Date: 2026-07-27 20:30:00.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'd2e3f4a5b6c7'
down_revision = 'c7d8e9f0a1b2'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        'knowledge_chunk',
        sa.Column('id', sa.Integer(), primary_key=True, index=True),
        sa.Column('source_type', sa.String(), nullable=False, index=True),
        sa.Column('source_id', sa.Integer(), nullable=False, index=True),
        sa.Column('source_title', sa.String(), nullable=False),
        sa.Column('title', sa.String(), nullable=False, index=True),
        sa.Column('content', sa.Text(), nullable=False),
        sa.Column('chunk_type', sa.String(), nullable=False, index=True),
        sa.Column('project_id', sa.Integer(), sa.ForeignKey('project.id'), nullable=True, index=True),
        sa.Column('meeting_id', sa.Integer(), sa.ForeignKey('meeting.id'), nullable=True, index=True),
        sa.Column('task_id', sa.Integer(), sa.ForeignKey('task.id'), nullable=True, index=True),
        sa.Column('conversation_id', sa.Integer(), sa.ForeignKey('conversation.id'), nullable=True, index=True),
        sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True, index=True),
        sa.Column('chunk_metadata', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), default=False),
    )


def downgrade() -> None:
    op.drop_table('knowledge_chunk')
