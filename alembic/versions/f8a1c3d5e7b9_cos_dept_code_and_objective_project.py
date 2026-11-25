"""Add short_code to cos_department and project_id + progress_manual to cos_objective

Revision ID: f8a1c3d5e7b9
Revises: e5c1a7f3b9d2
Create Date: 2026-11-25 12:00:00.000000
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'f8a1c3d5e7b9'
down_revision: Union[str, None] = 'e5c1a7f3b9d2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "cos_department" in tables:
        dept_cols = {c["name"] for c in insp.get_columns("cos_department")}
        if "short_code" not in dept_cols:
            op.add_column("cos_department", sa.Column("short_code", sa.String(), nullable=True))

    if "cos_objective" in tables:
        obj_cols = {c["name"] for c in insp.get_columns("cos_objective")}
        if "project_id" not in obj_cols:
            op.add_column(
                "cos_objective",
                sa.Column("project_id", sa.Integer(), sa.ForeignKey("project.id"), nullable=True),
            )
            op.create_index("ix_cos_objective_project_id", "cos_objective", ["project_id"])
        if "progress_manual" not in obj_cols:
            op.add_column(
                "cos_objective",
                sa.Column("progress_manual", sa.Boolean(), server_default=sa.text("false"), nullable=True),
            )


def downgrade() -> None:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    tables = set(insp.get_table_names())

    if "cos_objective" in tables:
        obj_cols = {c["name"] for c in insp.get_columns("cos_objective")}
        if "progress_manual" in obj_cols:
            op.drop_column("cos_objective", "progress_manual")
        if "project_id" in obj_cols:
            op.drop_index("ix_cos_objective_project_id", table_name="cos_objective")
            op.drop_column("cos_objective", "project_id")

    if "cos_department" in tables:
        dept_cols = {c["name"] for c in insp.get_columns("cos_department")}
        if "short_code" in dept_cols:
            op.drop_column("cos_department", "short_code")
