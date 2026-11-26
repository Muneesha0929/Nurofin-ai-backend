"""Schema for the Chief of Staff codes / timeline / priorities work (26 Nov 2026).

Production does not run Alembic reliably (its alembic_version history is broken, so every deploy's
`alembic upgrade head` fails on "Initial schema" and the schema only changes through app startup).
So the same steps live here and run in two places:
  - the Alembic migration a4d7e1b9c3f6 (local / any database with a working history), and
  - `ensure_cos_schema()` at app startup (production).
Every step checks what is already there, so it is safe to run on every start.
"""
import re

import sqlalchemy as sa


# ----------------------------------------------------------------- helpers

def _tables(bind) -> set:
    return set(sa.inspect(bind).get_table_names())


def _cols(bind, table: str) -> set:
    return {c["name"] for c in sa.inspect(bind).get_columns(table)}


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
            print(f"cos schema: department '{name}' (id {did}) shows as ##{old} today but another department already uses ##{old}; "
                  f"it will show as ##{new}. Set a short code by hand.")
            continue
        bind.execute(sa.text("UPDATE cos_department SET short_code = :c WHERE id = :i"), {"c": old, "i": did})
        final[did] = old
        print(f"cos schema: department '{name}' keeps its code ##{old}")


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
    print(f"cos schema: numbered {len(obj_seq)} objective(s) and {len(link_seq)} task link(s)")



# ----------------------------------------------------------------- startup sync

_ADVISORY_LOCK = 742026112601  # any constant: only one worker runs the sync at a time

_COLUMNS = [
    # Muneesha's f8a1c3d5e7b9 (department codes, project link, manual progress)
    ("cos_department", "short_code", "VARCHAR"),
    ("cos_objective", "project_id", "INTEGER REFERENCES project(id)"),
    ("cos_objective", "progress_manual", "BOOLEAN DEFAULT false"),
    # a4d7e1b9c3f6: stored codes
    ("cos_quarter_target", "next_objective_seq", "INTEGER NOT NULL DEFAULT 1"),
    ("cos_objective", "seq", "INTEGER"),
    ("cos_objective", "next_child_seq", "INTEGER NOT NULL DEFAULT 1"),
    ("cos_objective_task", "seq", "INTEGER"),
    # deadline history
    ("task", "original_deadline", "VARCHAR"),
    # priorities
    ("cos_objective", "priority", "VARCHAR"),
    ("cos_objective", "priority_set_at", "TIMESTAMP"),
    ("cos_objective", "priority_set_by_id", 'INTEGER REFERENCES "user"(id)'),
]


def ensure_cos_schema(bind) -> None:
    """Run inside one transaction on a sync connection (engine.begin() + run_sync)."""
    if bind.dialect.name != "postgresql":
        return
    bind.execute(sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": _ADVISORY_LOCK})
    tables = _tables(bind)
    first_time = "cos_objective" in tables and "seq" not in _cols(bind, "cos_objective")
    for table, col, ddl in _COLUMNS:
        if table in tables:
            bind.execute(sa.text(f'ALTER TABLE "{table}" ADD COLUMN IF NOT EXISTS {col} {ddl}'))
    if "cos_objective" in tables:
        bind.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_cos_objective_project_id ON cos_objective (project_id)"))
    if "task" in tables:
        bind.execute(sa.text(
            "UPDATE task SET original_deadline = SUBSTR(deadline, 1, 10) "
            "WHERE original_deadline IS NULL AND deadline IS NOT NULL AND deadline <> ''"
        ))
    if first_time:
        _freeze_department_codes(bind)
    # number anything that has no number yet (all rows the first time; afterwards normally nothing)
    needs = bind.execute(sa.text(
        "SELECT (SELECT COUNT(*) FROM cos_objective WHERE seq IS NULL)"
        + (" + (SELECT COUNT(*) FROM cos_objective_task WHERE seq IS NULL)" if "cos_objective_task" in tables else "")
    )).scalar() if "cos_objective" in tables else 0
    if needs:
        _backfill_codes(bind)


def ensure_notification_priority(conn) -> None:
    """Postgres enum values must be added outside a transaction block on older versions."""
    if conn.dialect.name != "postgresql":
        return
    if conn.execute(sa.text("SELECT 1 FROM pg_type WHERE typname = 'notificationtypeenum'")).first():
        conn.execute(sa.text("ALTER TYPE notificationtypeenum ADD VALUE IF NOT EXISTS 'priority'"))
