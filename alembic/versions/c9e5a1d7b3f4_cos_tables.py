"""Move department targets to their own cos_* tables, add company widgets

Revision ID: c9e5a1d7b3f4
Revises: b8d4f02c6e11
Create Date: 2026-10-15 10:00:00.000000

Production already had hand-made tables named department_objective (and possibly others)
with different column types, so inserts failed even after b8d4f02c6e11 added the missing
columns. Our feature now uses its own tables (cos_ = Chief of Staff):

  target_department          -> cos_department
  target_department_member   -> cos_department_member
  company_quarter_goal       -> cos_company_goal
  department_quarter_target  -> cos_quarter_target
  department_objective       -> cos_objective
  department_objective_update-> cos_objective_update
  (new)                      -> cos_company_widget

Rows that clearly belong to this feature are copied across; the old tables are left exactly
as they are (nothing is dropped or altered). Each copy runs in its own savepoint, so a table
that cannot be copied is skipped instead of failing the deploy.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = 'c9e5a1d7b3f4'
down_revision: Union[str, None] = 'b8d4f02c6e11'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

NEW_TABLES = ['cos_department', 'cos_department_member', 'cos_company_goal', 'cos_quarter_target',
              'cos_objective', 'cos_objective_update', 'cos_company_widget']

# (old table, new table, extra WHERE so only this feature's rows come across)
COPIES = [
    ('target_department', 'cos_department', 'name IS NOT NULL'),
    ('target_department_member', 'cos_department_member',
     'department_id IN (SELECT id FROM cos_department) AND user_id IN (SELECT id FROM "user")'),
    ('company_quarter_goal', 'cos_company_goal', 'fiscal_year IS NOT NULL AND quarter_number IS NOT NULL'),
    ('department_quarter_target', 'cos_quarter_target',
     'department_id IN (SELECT id FROM cos_department) AND fiscal_year IS NOT NULL AND quarter_number IS NOT NULL'),
    # objectives: top level first, then sub-objectives whose parent made it across
    ('department_objective', 'cos_objective',
     'parent_id IS NULL AND department_id IN (SELECT id FROM cos_department) '
     'AND fiscal_year IS NOT NULL AND quarter_number IS NOT NULL AND title IS NOT NULL'),
    ('department_objective', 'cos_objective',
     'parent_id IN (SELECT id FROM cos_objective) AND department_id IN (SELECT id FROM cos_department) '
     'AND fiscal_year IS NOT NULL AND quarter_number IS NOT NULL AND title IS NOT NULL'),
    ('department_objective_update', 'cos_objective_update',
     'objective_id IN (SELECT id FROM cos_objective) AND user_id IS NOT NULL AND text IS NOT NULL'),
]


def upgrade() -> None:
    from app.db.base_class import Base
    import app.models  # noqa: F401  (registers the models on Base.metadata)

    bind = op.get_bind()
    dialect = bind.dialect
    insp = sa.inspect(bind)
    existing = set(insp.get_table_names())

    for name in NEW_TABLES:
        Base.metadata.tables[name].create(bind, checkfirst=True)

    for old, new, where in COPIES:
        if old not in existing:
            continue
        new_table = Base.metadata.tables[new]
        old_cols = {c['name'] for c in sa.inspect(bind).get_columns(old)}
        cols = [c for c in new_table.columns if c.name in old_cols]
        if not cols:
            continue
        target = ', '.join(f'"{c.name}"' for c in cols)
        source = ', '.join(f'CAST("{c.name}" AS {c.type.compile(dialect=dialect)})' for c in cols)
        sql = f'INSERT INTO {new} ({target}) SELECT {source} FROM {old} WHERE {where}'
        if 'id' in {c.name for c in cols} and new not in ('cos_department_member',):
            sql += f' AND id NOT IN (SELECT id FROM {new})'
        try:
            with bind.begin_nested():
                n = bind.execute(sa.text(sql)).rowcount
            print(f'cos migration: copied {n} row(s) {old} -> {new}')
        except Exception as e:  # keep the deploy going; the new table simply starts empty
            print(f'cos migration: skipped {old} -> {new}: {type(e).__name__}: {str(e).splitlines()[0][:200]}')

    if dialect.name == 'postgresql':
        for name in NEW_TABLES:
            if 'id' in Base.metadata.tables[name].c and name != 'cos_department_member':
                bind.execute(sa.text(
                    f"SELECT setval(pg_get_serial_sequence('{name}', 'id'), COALESCE((SELECT MAX(id) FROM {name}), 0) + 1, false)"
                ))


def downgrade() -> None:
    for name in reversed(NEW_TABLES):
        op.drop_table(name)
