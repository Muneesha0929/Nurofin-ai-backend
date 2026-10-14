"""Repair department-target tables

Revision ID: b8d4f02c6e11
Revises: a7c3e91d2f10
Create Date: 2026-10-14 20:00:00.000000

a7c3e91d2f10 skipped any table that already existed. If a table with the same name was
already in the database (made outside migrations), it kept its old columns and the new
endpoints failed. This makes sure every table and column the models need exists.

Additive only: creates missing tables, adds missing columns. Never drops or alters.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'b8d4f02c6e11'
down_revision: Union[str, None] = 'a7c3e91d2f10'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = [
    'target_department',
    'target_department_member',
    'company_quarter_goal',
    'department_quarter_target',
    'department_objective',
    'department_objective_update',
]


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)

    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    for name in TABLES:
        table = Base.metadata.tables[name]
        if name not in existing:
            table.create(bind)
            continue
        have = {c['name'] for c in insp.get_columns(name)}
        for col in table.columns:
            if col.name in have:
                continue
            # added as nullable so existing rows are fine; the app always sets these values
            op.add_column(name, sa.Column(col.name, col.type, nullable=True))


def downgrade() -> None:
    # Nothing to undo safely: this only filled gaps.
    pass
