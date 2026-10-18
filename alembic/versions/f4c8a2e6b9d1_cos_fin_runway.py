"""Add the cos_fin_* tables for Runway (CEO-only cash forecast)

Revision ID: f4c8a2e6b9d1
Revises: e3a7c5d9f1b2
Create Date: 2026-10-18 12:00:00.000000

Additive only: nine new tables, nothing existing is altered.
If a table with one of these names already exists, its columns are checked against the model
and the migration stops with a clear message instead of silently skipping it
(a bare "create if missing" is what broke department_objective on 14 Oct).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f4c8a2e6b9d1'
down_revision: Union[str, None] = 'e3a7c5d9f1b2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)
    from app.models.runway import RUNWAY_TABLES

    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for name in RUNWAY_TABLES:
        table = Base.metadata.tables[name]
        if name in existing:
            have = {c["name"] for c in insp.get_columns(name)}
            missing = [c.name for c in table.columns if c.name not in have]
            if missing:
                raise RuntimeError(f"Table {name} already exists but lacks columns {missing}; fix it by hand before upgrading.")
            continue
        table.create(bind)


def downgrade() -> None:
    from app.models.runway import RUNWAY_TABLES
    for name in reversed(RUNWAY_TABLES):
        op.drop_table(name)
