"""Add cos_objective_task (Task Center tasks linked to objectives)

Revision ID: e3a7c5d9f1b2
Revises: d1f6b2a8c4e7
Create Date: 2026-10-16 11:00:00.000000

Additive only: one new link table. The task table is not altered.
"""
from typing import Sequence, Union

from alembic import op


revision: str = 'e3a7c5d9f1b2'
down_revision: Union[str, None] = 'd1f6b2a8c4e7'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)
    Base.metadata.tables['cos_objective_task'].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table('cos_objective_task')
