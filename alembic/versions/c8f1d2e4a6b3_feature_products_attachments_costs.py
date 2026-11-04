"""Product Features: products, attachments, and cost and profit lines

Revision ID: c8f1d2e4a6b3
Revises: b7e3c9a1d5f2
Create Date: 2026-11-04 12:00:00.000000

Additive: four new tables. The products list starts with GMDI, Build-Track and Chief of Staff.
An existing table with one of these names is checked column by column instead of being skipped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'c8f1d2e4a6b3'
down_revision: Union[str, None] = 'b7e3c9a1d5f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ["cos_product", "cos_feature_product", "cos_feature_attachment", "cos_feature_cost"]


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())
    for name in TABLES:
        table = Base.metadata.tables[name]
        if name in existing:
            have = {c["name"] for c in insp.get_columns(name)}
            missing = [c.name for c in table.columns if c.name not in have]
            if missing:
                raise RuntimeError(f"Table {name} already exists but lacks columns {missing}; fix it by hand before upgrading.")
            continue
        table.create(bind)
    if not bind.execute(sa.text("SELECT count(*) FROM cos_product")).scalar():
        for i, n in enumerate(["GMDI", "Build-Track", "Chief of Staff"]):
            bind.execute(sa.text("INSERT INTO cos_product (name, sort_order, created_at, updated_at, is_deleted) "
                                 "VALUES (:n, :i, now(), now(), false)"), {"n": n, "i": i})


def downgrade() -> None:
    for name in reversed(TABLES):
        op.drop_table(name)
