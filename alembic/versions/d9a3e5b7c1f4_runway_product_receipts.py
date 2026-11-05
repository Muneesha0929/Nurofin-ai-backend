"""Runway: money actually received per product and month

Revision ID: d9a3e5b7c1f4
Revises: c8f1d2e4a6b3
Create Date: 2026-11-04 14:00:00.000000

Additive: one new table, cos_fin_product_receipt. Nothing existing changes; with no rows the
forecast is exactly as before (received = expected).
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'd9a3e5b7c1f4'
down_revision: Union[str, None] = 'c8f1d2e4a6b3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    insp = sa.inspect(bind)
    table = Base.metadata.tables["cos_fin_product_receipt"]
    if "cos_fin_product_receipt" in insp.get_table_names():
        have = {c["name"] for c in insp.get_columns("cos_fin_product_receipt")}
        missing = [c.name for c in table.columns if c.name not in have]
        if missing:
            raise RuntimeError(f"cos_fin_product_receipt already exists but lacks columns {missing}; fix it by hand before upgrading.")
        return
    table.create(bind)


def downgrade() -> None:
    op.drop_table("cos_fin_product_receipt")
