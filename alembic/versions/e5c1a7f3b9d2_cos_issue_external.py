"""Issues raised from Build-Track: side table cos_issue_external

Revision ID: e5c1a7f3b9d2
Revises: d9a3e5b7c1f4
Create Date: 2026-11-12 10:00:00.000000

Additive: one new table. The existing `issue` table is not touched.
Safe to run when the table is already there: accepted if it has exactly these columns, refused (raise) if not,
because production has had hand-made tables that did not match the models.
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'e5c1a7f3b9d2'
down_revision: Union[str, None] = 'd9a3e5b7c1f4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLE = "cos_issue_external"


def _columns():
    return [
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("issue_id", sa.Integer(), sa.ForeignKey("issue.id"), nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("client_report_id", sa.String(80)),
        sa.Column("reporter", sa.JSON()),
        sa.Column("screen", sa.JSON()),
        sa.Column("breadcrumbs", sa.JSON()),
        sa.Column("device", sa.JSON()),
        sa.Column("project", sa.JSON()),
        sa.Column("occurred_at", sa.DateTime()),
        sa.Column("received_at", sa.DateTime()),
        sa.Column("screenshot_key", sa.String(300)),
        sa.Column("screenshot_mime", sa.String(40)),
        sa.Column("callback_url", sa.Text()),
        sa.Column("last_callback_status", sa.String(300)),
        sa.Column("last_callback_at", sa.DateTime()),
        sa.Column("created_at", sa.DateTime()),
        sa.Column("updated_at", sa.DateTime()),
        sa.Column("is_deleted", sa.Boolean()),
    ]


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    wanted = [c.name for c in _columns()]
    if TABLE in insp.get_table_names():
        have = {c["name"] for c in insp.get_columns(TABLE)}
        missing = [n for n in wanted if n not in have]
        extra = sorted(have - set(wanted))
        if missing or extra:
            raise RuntimeError(
                f"{TABLE} already exists but does not match (missing columns {missing}, unexpected columns {extra}); "
                "fix or rename it by hand before upgrading."
            )
        return
    op.create_table(
        TABLE,
        *_columns(),
        sa.UniqueConstraint("issue_id", name="uq_cos_issue_external_issue_id"),
        sa.UniqueConstraint("external_id", name="uq_cos_issue_external_external_id"),
    )
    op.create_index("ix_cos_issue_external_id", TABLE, ["id"])


def downgrade() -> None:
    op.drop_table(TABLE)
