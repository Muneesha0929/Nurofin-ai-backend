"""Add the cos_feature* tables for Product Features (why each feature was built)

Revision ID: a2b9d4e6f8c1
Revises: f4c8a2e6b9d1
Create Date: 2026-10-29 12:00:00.000000

Additive only: six new tables, nothing existing is altered.
If a table with one of these names already exists, its columns are checked against the model
and the migration stops with a clear message instead of silently skipping it.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a2b9d4e6f8c1'
down_revision: Union[str, None] = 'f4c8a2e6b9d1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)
    from app.models.product_feature import FEATURE_TABLES

    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for name in FEATURE_TABLES:
        table = Base.metadata.tables[name]
        if name in existing:
            have = {c["name"] for c in insp.get_columns(name)}
            missing = [c.name for c in table.columns if c.name not in have]
            if missing:
                raise RuntimeError(f"Table {name} already exists but lacks columns {missing}; fix it by hand before upgrading.")
            continue
        table.create(bind)


def downgrade() -> None:
    from app.models.product_feature import FEATURE_TABLES
    existing = set(sa.inspect(op.get_bind()).get_table_names())
    for name in reversed(FEATURE_TABLES):
        if name in existing:
            op.drop_table(name)
