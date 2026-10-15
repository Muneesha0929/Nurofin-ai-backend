"""Add cos_monthly_target (dated targets on the Overview)

Revision ID: d1f6b2a8c4e7
Revises: c9e5a1d7b3f4
Create Date: 2026-10-15 16:00:00.000000

Additive only: one new table.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'd1f6b2a8c4e7'
down_revision: Union[str, None] = 'c9e5a1d7b3f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)
    Base.metadata.tables['cos_monthly_target'].create(op.get_bind(), checkfirst=True)


def downgrade() -> None:
    op.drop_table('cos_monthly_target')
