"""Runway: billed-monthly products with client minimums, and when clients pay

Revision ID: b7e3c9a1d5f2
Revises: a2b9d4e6f8c1
Create Date: 2026-11-03 15:00:00.000000

Additive: one column on cos_fin_product (pay_delay, default 0) and one new table
(cos_fin_product_client). The existing GMDI product becomes "billed monthly, paid the month
after", which is how GMDI bills: each month is invoiced on the 1st of the next.
Like f4c8a2e6b9d1, an existing table is checked column by column instead of being skipped.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'b7e3c9a1d5f2'
down_revision: Union[str, None] = 'a2b9d4e6f8c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401

    bind = op.get_bind()
    insp = sa.inspect(bind)
    if "pay_delay" not in {c["name"] for c in insp.get_columns("cos_fin_product")}:
        op.add_column("cos_fin_product", sa.Column("pay_delay", sa.Integer(), nullable=False, server_default="0"))
    table = Base.metadata.tables["cos_fin_product_client"]
    if "cos_fin_product_client" in insp.get_table_names():
        have = {c["name"] for c in insp.get_columns("cos_fin_product_client")}
        missing = [c.name for c in table.columns if c.name not in have]
        if missing:
            raise RuntimeError(f"cos_fin_product_client already exists but lacks columns {missing}; fix it by hand before upgrading.")
    else:
        table.create(bind)
    op.execute("UPDATE cos_fin_product SET tracking = 'billed', pay_delay = 1 "
               "WHERE lower(name) = 'gmdi' AND tracking = 'figures'")


def downgrade() -> None:
    op.execute("UPDATE cos_fin_product SET tracking = 'figures' WHERE tracking = 'billed'")
    op.drop_table("cos_fin_product_client")
    op.drop_column("cos_fin_product", "pay_delay")
