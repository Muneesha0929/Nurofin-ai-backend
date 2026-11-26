"""Stored codes, deadline timeline and CEO priorities

Revision ID: a4d7e1b9c3f6
Revises: f8a1c3d5e7b9
Create Date: 2026-11-26 10:00:00.000000

1. Codes are stored, never recomputed from position:
     cos_objective.seq, cos_objective.next_child_seq,
     cos_objective_task.seq, cos_quarter_target.next_objective_seq
   Existing rows are numbered by what people see today, so every code stays the same:
     - top-level objectives of a department + quarter: (sort_order, id) -> 1, 2, 3
     - under an objective: its sub-objectives first (sort_order, id), then its linked tasks
       in Task Center order (task id), the way the page numbered them
     - items that are not on screen (deleted objectives, unlinked or deleted tasks, tasks shown nested
       under their already-linked parent) get the numbers AFTER the visible ones, so nothing is reused
   Counters are set to max + 1. Quarter-target rows that do not exist yet are created to hold the counter.
2. task.original_deadline (= deadline for existing tasks) and the cos_timeline_entry table.
3. cos_objective.priority / priority_set_at / priority_set_by_id and the cos_priority_seen table,
   plus the notification type 'priority'.
4. Department short codes that came from the old "Product / Beltrack -> PROD" rule are written down as
   real short codes, so removing that rule does not change a prefix people already see.

Every step checks what is already there, so a half-applied run can be repeated.
"""
import re
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = 'a4d7e1b9c3f6'
down_revision: Union[str, None] = 'f8a1c3d5e7b9'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


# ----------------------------------------------------------------- helpers
# The numbering / short-code logic is shared with app startup (production has no working alembic history).
from app.db.cos_schema import _tables, _cols, _executemany, _freeze_department_codes, _backfill_codes  # noqa: E402


def _add_column(bind, table: str, column: sa.Column) -> None:
    if table in _tables(bind) and column.name not in _cols(bind, table):
        op.add_column(table, column)


def _drop_column(bind, table: str, name: str) -> None:
    if table in _tables(bind) and name in _cols(bind, table):
        op.drop_column(table, name)


# ----------------------------------------------------------------- upgrade / downgrade

def upgrade() -> None:
    bind = op.get_bind()
    is_pg = bind.dialect.name == "postgresql"

    # the bell notification type; Postgres enums grow outside a transaction (older versions refuse inside one)
    if is_pg and bind.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'notificationtypeenum'")).first():
        with op.get_context().autocommit_block():
            op.execute("ALTER TYPE notificationtypeenum ADD VALUE IF NOT EXISTS 'priority'")

    # 1. stored codes
    _add_column(bind, "cos_quarter_target", sa.Column("next_objective_seq", sa.Integer(), nullable=False, server_default=sa.text("1")))
    _add_column(bind, "cos_objective", sa.Column("seq", sa.Integer(), nullable=True))
    _add_column(bind, "cos_objective", sa.Column("next_child_seq", sa.Integer(), nullable=False, server_default=sa.text("1")))
    _add_column(bind, "cos_objective_task", sa.Column("seq", sa.Integer(), nullable=True))
    _backfill_codes(bind)
    if "cos_objective" in _tables(bind):
        op.alter_column("cos_objective", "seq", existing_type=sa.Integer(), nullable=False)
    if "cos_objective_task" in _tables(bind):
        op.alter_column("cos_objective_task", "seq", existing_type=sa.Integer(), nullable=False)

    # 2. deadline history
    _add_column(bind, "task", sa.Column("original_deadline", sa.String(), nullable=True))
    if "task" in _tables(bind):
        bind.execute(sa.text(
            "UPDATE task SET original_deadline = SUBSTR(deadline, 1, 10) "
            "WHERE original_deadline IS NULL AND deadline IS NOT NULL AND deadline <> ''"
        ))
    if "cos_timeline_entry" not in _tables(bind):
        op.create_table(
            "cos_timeline_entry",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("entity", sa.String(), nullable=False),
            sa.Column("entity_id", sa.Integer(), nullable=False),
            sa.Column("kind", sa.String(), nullable=False),
            sa.Column("old_date", sa.String(), nullable=True),
            sa.Column("new_date", sa.String(), nullable=True),
            sa.Column("text", sa.Text(), nullable=True),
            sa.Column("person_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_by_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
        )
        op.create_index("ix_cos_timeline_entry_id", "cos_timeline_entry", ["id"])
        op.create_index("ix_cos_timeline_entry_entity", "cos_timeline_entry", ["entity", "entity_id"])
    else:
        missing = {"entity", "entity_id", "kind", "old_date", "new_date", "text", "person_id", "created_by_id"} - _cols(bind, "cos_timeline_entry")
        if missing:
            raise RuntimeError(f"cos_timeline_entry already exists but lacks columns {sorted(missing)}; fix it by hand before upgrading.")

    # 3. priorities
    _add_column(bind, "cos_objective", sa.Column("priority", sa.String(), nullable=True))
    _add_column(bind, "cos_objective", sa.Column("priority_set_at", sa.DateTime(), nullable=True))
    _add_column(bind, "cos_objective", sa.Column("priority_set_by_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=True))
    if "cos_priority_seen" not in _tables(bind):
        op.create_table(
            "cos_priority_seen",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("user_id", sa.Integer(), sa.ForeignKey("user.id"), nullable=False),
            sa.Column("objective_id", sa.Integer(), sa.ForeignKey("cos_objective.id"), nullable=False),
            sa.Column("priority", sa.String(), nullable=False),
            sa.Column("seen_on", sa.String(), nullable=False),
            sa.Column("created_at", sa.DateTime(), nullable=True),
            sa.Column("updated_at", sa.DateTime(), nullable=True),
            sa.Column("is_deleted", sa.Boolean(), nullable=True),
            sa.UniqueConstraint("user_id", "objective_id", name="uix_cos_priority_seen"),
        )
        op.create_index("ix_cos_priority_seen_id", "cos_priority_seen", ["id"])
        op.create_index("ix_cos_priority_seen_user_id", "cos_priority_seen", ["user_id"])
    else:
        missing = {"user_id", "objective_id", "priority", "seen_on"} - _cols(bind, "cos_priority_seen")
        if missing:
            raise RuntimeError(f"cos_priority_seen already exists but lacks columns {sorted(missing)}; fix it by hand before upgrading.")

    # 4. keep the department prefixes people see today
    _freeze_department_codes(bind)


def downgrade() -> None:
    bind = op.get_bind()
    tables = _tables(bind)

    if "cos_priority_seen" in tables:
        op.drop_table("cos_priority_seen")
    if "cos_timeline_entry" in tables:
        op.drop_table("cos_timeline_entry")

    for col in ("priority_set_by_id", "priority_set_at", "priority", "next_child_seq", "seq"):
        _drop_column(bind, "cos_objective", col)
    _drop_column(bind, "cos_objective_task", "seq")
    _drop_column(bind, "cos_quarter_target", "next_objective_seq")
    _drop_column(bind, "task", "original_deadline")

    # Take 'priority' out of the notification type: Postgres cannot drop one value, so rebuild the type without it.
    if bind.dialect.name == "postgresql" and bind.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'notificationtypeenum'")).first():
        labels = [r[0] for r in bind.execute(sa.text(
            "SELECT e.enumlabel FROM pg_enum e JOIN pg_type t ON t.oid = e.enumtypid "
            "WHERE t.typname = 'notificationtypeenum' ORDER BY e.enumsortorder"
        )).fetchall()]
        if "priority" in labels:
            keep = [l for l in labels if l != "priority"]
            op.execute("DELETE FROM notification WHERE type::text = 'priority'")
            op.execute("ALTER TYPE notificationtypeenum RENAME TO notificationtypeenum_old")
            op.execute("CREATE TYPE notificationtypeenum AS ENUM (" + ", ".join("'" + l.replace("'", "''") + "'" for l in keep) + ")")
            op.execute("ALTER TABLE notification ALTER COLUMN type TYPE notificationtypeenum USING type::text::notificationtypeenum")
            op.execute("DROP TYPE notificationtypeenum_old")
