"""Add department targets (Director quarter messages, department objectives)

Revision ID: a7c3e91d2f10
Revises: 671ace7a50e2
Create Date: 2026-10-14 18:30:00.000000

Additive only: five new tables plus one join table. No existing table is touched.
Each create is skipped if the table already exists (init_db's create_all may have made it).
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'a7c3e91d2f10'
down_revision: Union[str, None] = '671ace7a50e2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _base_columns():
    return [
        sa.Column('created_at', sa.DateTime(), nullable=True),
        sa.Column('updated_at', sa.DateTime(), nullable=True),
        sa.Column('is_deleted', sa.Boolean(), nullable=True),
    ]


def upgrade() -> None:
    existing = set(sa.inspect(op.get_bind()).get_table_names())

    if 'target_department' not in existing:
        op.create_table(
            'target_department',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('name', sa.String(), nullable=False),
            sa.Column('hod_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            *_base_columns(),
        )
        op.create_index('ix_target_department_id', 'target_department', ['id'])

    if 'target_department_member' not in existing:
        op.create_table(
            'target_department_member',
            sa.Column('department_id', sa.Integer(), sa.ForeignKey('target_department.id'), primary_key=True),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), primary_key=True),
        )

    if 'company_quarter_goal' not in existing:
        op.create_table(
            'company_quarter_goal',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('fiscal_year', sa.Integer(), nullable=False),
            sa.Column('quarter_number', sa.Integer(), nullable=False),
            sa.Column('headline', sa.Text(), nullable=True),
            sa.Column('updated_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            *_base_columns(),
            sa.UniqueConstraint('fiscal_year', 'quarter_number', name='uix_company_quarter_goal'),
        )
        op.create_index('ix_company_quarter_goal_id', 'company_quarter_goal', ['id'])

    if 'department_quarter_target' not in existing:
        op.create_table(
            'department_quarter_target',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('department_id', sa.Integer(), sa.ForeignKey('target_department.id'), nullable=False),
            sa.Column('fiscal_year', sa.Integer(), nullable=False),
            sa.Column('quarter_number', sa.Integer(), nullable=False),
            sa.Column('message', sa.Text(), nullable=True),
            sa.Column('message_updated_at', sa.DateTime(), nullable=True),
            sa.Column('message_updated_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('hod_seen_at', sa.DateTime(), nullable=True),
            sa.Column('outcome', sa.String(), nullable=True),
            sa.Column('outcome_note', sa.Text(), nullable=True),
            sa.Column('outcome_set_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('outcome_set_at', sa.DateTime(), nullable=True),
            *_base_columns(),
            sa.UniqueConstraint('department_id', 'fiscal_year', 'quarter_number', name='uix_department_quarter_target'),
        )
        op.create_index('ix_department_quarter_target_id', 'department_quarter_target', ['id'])
        op.create_index('ix_department_quarter_target_department_id', 'department_quarter_target', ['department_id'])

    if 'department_objective' not in existing:
        op.create_table(
            'department_objective',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('department_id', sa.Integer(), sa.ForeignKey('target_department.id'), nullable=False),
            sa.Column('fiscal_year', sa.Integer(), nullable=False),
            sa.Column('quarter_number', sa.Integer(), nullable=False),
            sa.Column('parent_id', sa.Integer(), sa.ForeignKey('department_objective.id'), nullable=True),
            sa.Column('title', sa.String(), nullable=False),
            sa.Column('description', sa.Text(), nullable=True),
            sa.Column('owner_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            sa.Column('deadline', sa.String(), nullable=True),
            sa.Column('status', sa.String(), nullable=True),
            sa.Column('progress', sa.Float(), nullable=True),
            sa.Column('sort_order', sa.Integer(), nullable=True),
            sa.Column('created_by_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=True),
            *_base_columns(),
        )
        op.create_index('ix_department_objective_id', 'department_objective', ['id'])
        op.create_index('ix_department_objective_department_id', 'department_objective', ['department_id'])
        op.create_index('ix_department_objective_parent_id', 'department_objective', ['parent_id'])

    if 'department_objective_update' not in existing:
        op.create_table(
            'department_objective_update',
            sa.Column('id', sa.Integer(), primary_key=True),
            sa.Column('objective_id', sa.Integer(), sa.ForeignKey('department_objective.id'), nullable=False),
            sa.Column('user_id', sa.Integer(), sa.ForeignKey('user.id'), nullable=False),
            sa.Column('text', sa.Text(), nullable=False),
            *_base_columns(),
        )
        op.create_index('ix_department_objective_update_id', 'department_objective_update', ['id'])
        op.create_index('ix_department_objective_update_objective_id', 'department_objective_update', ['objective_id'])


def downgrade() -> None:
    op.drop_table('department_objective_update')
    op.drop_table('department_objective')
    op.drop_table('department_quarter_target')
    op.drop_table('company_quarter_goal')
    op.drop_table('target_department_member')
    op.drop_table('target_department')
