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

def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


def _add_column(bind, table: str, column: sa.Column) -> None:
    if table in _tables(bind) and column.name not in _cols(bind, table):
        op.add_column(table, column)


def _drop_column(bind, table: str, name: str) -> None:
    if table in _tables(bind) and name in _cols(bind, table):
        op.drop_column(table, name)


def _executemany(bind, sql: str, rows: list) -> None:
    if rows:
        bind.execute(sa.text(sql), rows)


# ----------------------------------------------------------------- department short codes (copy of the old and new rules)

_DEFAULT_CODES = {
    "business development": "BD", "marketing": "MKT", "compliance": "CMP", "operations": "OPS", "it": "IT",
    "infrastructure": "IT", "finance": "FIN", "customer success": "CS",
}
_OLD_ONLY_CODES = {"product": "PROD", "beltrack": "PROD", "beltrack demo": "PROD"}


def _normalize(raw):
    if not raw:
        return None
    return re.sub(r"[^A-Za-z0-9]", "", raw.strip().lstrip("#")).upper()[:10] or None


def _fallback(name: str, dept_id: int) -> str:
    words = [w for w in re.split(r"[^A-Za-z0-9]+", name or "") if w and w.lower() not in ("and", "of", "the", "for", "in")]
    if len(words) >= 2:
        initials = "".join(w[0].upper() for w in words[:4])
        if len(initials) >= 2:
            return initials
    if words:
        w = words[0].upper()
        if len(w) <= 4:
            return w
        consonants = w[0] + "".join(ch for ch in w[1:] if ch not in "AEIOU")
        return (consonants if len(consonants) >= 3 else w)[:3]
    return f"D{dept_id}"


def _effective(name: str, explicit, dept_id: int, old_rules: bool) -> str:
    low = (name or "").strip().lower()
    code = _normalize(explicit)
    if code:
        if old_rules and code == "PRD" and ("product" in low or "beltrack" in low):
            return "PROD"
        return code
    table = {**_OLD_ONLY_CODES, **_DEFAULT_CODES} if old_rules else _DEFAULT_CODES
    for key, val in table.items():
        if low == key or low.startswith(key + " ") or low.startswith(key + ","):
            return val
    return _fallback(name, dept_id)


def _freeze_department_codes(bind) -> None:
    if "cos_department" not in _tables(bind) or "short_code" not in _cols(bind, "cos_department"):
        return
    rows = bind.execute(sa.text(
        "SELECT id, name, short_code FROM cos_department WHERE COALESCE(is_deleted, false) = false ORDER BY id"
    )).fetchall()
    final = {r[0]: _effective(r[1], r[2], r[0], old_rules=False) for r in rows}
    for did, name, explicit in rows:
        old, new = _effective(name, explicit, did, True), final[did]
        if old == new:
            continue
        if any(other != did and code == old for other, code in final.items()):
            print(f"cos migration: department '{name}' (id {did}) shows as ##{old} today but another department already uses ##{old}; "
                  f"it will show as ##{new}. Set a short code by hand.")
            continue
        bind.execute(sa.text("UPDATE cos_department SET short_code = :c WHERE id = :i"), {"c": old, "i": did})
        final[did] = old
        print(f"cos migration: department '{name}' keeps its code ##{old}")


# ----------------------------------------------------------------- backfill of stored numbers

def _backfill_codes(bind) -> None:
    tables = _tables(bind)
    if "cos_objective" not in tables:
        return
    objs = bind.execute(sa.text(
        "SELECT id, parent_id, department_id, fiscal_year, quarter_number, COALESCE(sort_order, 0), COALESCE(is_deleted, false), seq "
        "FROM cos_objective"
    )).fetchall()
    by_id = {o[0]: o for o in objs}
    children: dict = {}
    top_groups: dict = {}
    for o in objs:
        if o[1] is None:
            top_groups.setdefault((o[2], o[3], o[4]), []).append(o)
        else:
            children.setdefault(o[1], []).append(o)

    def order(rows):
        # visible first (so today's numbers hold), then deleted ones; each by (sort_order, id)
        return sorted(rows, key=lambda r: (bool(r[6]), r[5], r[0]))

    obj_seq: dict = {o[0]: o[7] for o in objs if o[7] is not None}  # already numbered by an earlier run
    top_next: dict = {}
    for key, rows in top_groups.items():
        n = max([obj_seq[r[0]] for r in rows if r[0] in obj_seq] or [0])
        for r in order(rows):
            if r[0] not in obj_seq:
                n += 1
                obj_seq[r[0]] = n
        top_next[key] = n + 1

    link_rows = []
    link_seq: dict = {}
    if "cos_objective_task" in tables:
        link_rows = bind.execute(sa.text(
            "SELECT l.id, l.objective_id, l.task_id, COALESCE(l.is_deleted, false), COALESCE(t.is_deleted, false), t.parent_id, l.seq "
            "FROM cos_objective_task l LEFT JOIN task t ON t.id = l.task_id"
        )).fetchall()
        link_seq = {r[0]: r[6] for r in link_rows if r[6] is not None}
    old_link_seq = dict(link_seq)
    links_by_obj: dict = {}
    for r in link_rows:
        links_by_obj.setdefault(r[1], []).append(r)

    child_next: dict = {}
    for oid in by_id:
        kids = children.get(oid, [])
        used = [obj_seq[k[0]] for k in kids if k[0] in obj_seq] + [link_seq[r[0]] for r in links_by_obj.get(oid, []) if r[0] in link_seq]
        n = max(used or [0])
        live_kids = [k for k in order(kids) if not k[6]]
        dead_kids = [k for k in order(kids) if k[6]]
        mine = links_by_obj.get(oid, [])
        shown = [r for r in mine if not r[3] and not r[4]]
        shown_task_ids = {r[2] for r in shown}
        tasks_main = sorted([r for r in shown if r[5] not in shown_task_ids], key=lambda r: (r[2], r[0]))   # what the page lists under the objective
        tasks_nested = sorted([r for r in shown if r[5] in shown_task_ids], key=lambda r: (r[2], r[0]))     # shown as subtasks of their linked parent
        tasks_hidden = sorted([r for r in mine if r[3] or r[4]], key=lambda r: r[0])
        for k in live_kids:
            if k[0] not in obj_seq:
                n += 1
                obj_seq[k[0]] = n
        for r in tasks_main + tasks_nested + tasks_hidden:
            if r[0] not in link_seq:
                n += 1
                link_seq[r[0]] = n
        for k in dead_kids:
            if k[0] not in obj_seq:
                n += 1
                obj_seq[k[0]] = n
        child_next[oid] = n + 1

    _executemany(bind, "UPDATE cos_objective SET seq = :s, next_child_seq = :n WHERE id = :i",
                 [{"s": obj_seq[oid], "n": child_next[oid], "i": oid} for oid in by_id if by_id[oid][7] is None])
    _executemany(bind, "UPDATE cos_objective SET next_child_seq = :n WHERE id = :i",
                 [{"n": child_next[oid], "i": oid} for oid in by_id if by_id[oid][7] is not None])
    _executemany(bind, "UPDATE cos_objective_task SET seq = :s WHERE id = :i",
                 [{"s": s, "i": lid} for lid, s in link_seq.items() if lid not in old_link_seq])

    if "cos_quarter_target" in tables:
        have = {(r[1], r[2], r[3]): (r[0], r[4]) for r in bind.execute(sa.text(
            "SELECT id, department_id, fiscal_year, quarter_number, COALESCE(next_objective_seq, 1) FROM cos_quarter_target"
        )).fetchall()}
        for key, nxt in top_next.items():
            if key in have:
                if nxt > have[key][1]:
                    bind.execute(sa.text("UPDATE cos_quarter_target SET next_objective_seq = :n WHERE id = :i"), {"n": nxt, "i": have[key][0]})
            else:
                bind.execute(sa.text(
                    "INSERT INTO cos_quarter_target (department_id, fiscal_year, quarter_number, next_objective_seq, created_at, updated_at, is_deleted) "
                    "VALUES (:d, :f, :q, :n, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP, false)"
                ), {"d": key[0], "f": key[1], "q": key[2], "n": nxt})
    print(f"cos migration: numbered {len(obj_seq)} objective(s) and {len(link_seq)} task link(s)")


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
