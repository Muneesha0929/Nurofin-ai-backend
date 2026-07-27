"""add phase 2.5 meeting intelligence fields

Revision ID: c7d8e9f0a1b2
Revises: b1c2d3e4f5a6
Create Date: 2026-07-27 15:48:48.000000

"""
from alembic import op
import sqlalchemy as sa


revision = 'c7d8e9f0a1b2'
down_revision = 'b1c2d3e4f5a6'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('meeting', sa.Column('document_file_path', sa.String(), nullable=True))
    op.add_column('meeting', sa.Column('document_filename', sa.String(), nullable=True))
    op.add_column('meeting', sa.Column('mom_questions', sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column('meeting', 'mom_questions')
    op.drop_column('meeting', 'document_filename')
    op.drop_column('meeting', 'document_file_path')
