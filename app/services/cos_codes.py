"""Codes for Chief of Staff items: PROD-1, PROD-1.2, PROD-1.2.3, PROD-1.2.3.1

One place that makes codes, so the board, the department page, the Task Center, the side panel and
"resolve" can never disagree.

- An objective or sub-objective code comes from its STORED number (`cos_objective.seq`).
- A linked task's number is `cos_objective_task.seq`. Sub-objectives and linked tasks of one objective
  share one counter (`cos_objective.next_child_seq`), so under an objective the numbers never clash.
- Subtasks of a linked task are not stored: PARENT_CODE.n, n counting in creation (task id) order.
- A number, once handed out, is never handed out again: deletes leave gaps, unlinking frees nothing.
"""
import re
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department_target import (
    DepartmentObjective,
    DepartmentQuarterTarget,
    ObjectiveTask,
    TargetDepartment,
)
from app.models.task import Task
from app.services.cos_access import task_chains

# ----------------------------------------------------------------- department prefix

# Names that get a friendly default when the Director has not typed a short code.
_DEFAULT_DEPT_CODES = {
    "business development": "BD",
    "marketing": "MKT",
    "compliance": "CMP",
    "operations": "OPS",
    "it": "IT",
    "infrastructure": "IT",
    "finance": "FIN",
    "customer success": "CS",
}


def normalize_short_code(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    cleaned = re.sub(r"[^A-Za-z0-9]", "", raw.strip().lstrip("#")).upper()
    return cleaned[:10] or None


def dept_short_code(d: TargetDepartment) -> str:
    """The prefix of every code in this department: the explicit short code, else a default from the name."""
    explicit = normalize_short_code(getattr(d, "short_code", None))
    if explicit:
        return explicit
    low = (d.name or "").strip().lower()
    for key, code in _DEFAULT_DEPT_CODES.items():
        if low == key or low.startswith(key + " ") or low.startswith(key + ","):
            return code
    words = [w for w in re.split(r"[^A-Za-z0-9]+", d.name or "") if w and w.lower() not in ("and", "of", "the", "for", "in")]
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
    return f"D{d.id}"


def full(dept_code: Optional[str], path: Optional[str]) -> str:
    """'PROD' + '1.2' -> 'PROD-1.2'. Without a prefix the bare path is returned."""
    if dept_code and path:
        return f"{dept_code}-{path}"
    return path or dept_code or ""


# ----------------------------------------------------------------- codes from stored numbers

def objective_code(o: DepartmentObjective, by_id: Dict[int, DepartmentObjective]) -> str:
    """'1' for a top-level objective, '1.2' for a sub-objective (its parent must be in by_id)."""
    own = str(o.seq)
    if o.parent_id and o.parent_id in by_id:
        return f"{objective_code(by_id[o.parent_id], by_id)}.{own}"
    return own


def assign_task_codes(task_list: List[Dict[str, Any]], prefix: str, dept_code: Optional[str] = None, linked: bool = True) -> List[Dict[str, Any]]:
    """Put `code` / `full_code` on tasks. Linked tasks (the first level under an objective) use their stored number;
    subtasks are numbered 1, 2, 3 in the order they are listed (creation order)."""
    out = []
    for idx, t in enumerate(task_list, start=1):
        n = t.get("seq") if (linked and t.get("seq")) else idx
        code = f"{prefix}.{n}"
        out.append({**t, "code": code, "full_code": full(dept_code, code),
                    "subtasks": assign_task_codes(t.get("subtasks") or [], code, dept_code, linked=False)})
    return out


# ----------------------------------------------------------------- handing out numbers

async def next_child_seq(db: AsyncSession, objective_id: int) -> int:
    """The next free number under this objective (for a sub-objective or a linked task).
    One UPDATE ... RETURNING: it takes the row lock and bumps the counter in the caller's transaction."""
    new = (await db.execute(
        update(DepartmentObjective)
        .where(DepartmentObjective.id == objective_id)
        .values(next_child_seq=func.coalesce(DepartmentObjective.next_child_seq, 1) + 1)
        .returning(DepartmentObjective.next_child_seq)
        .execution_options(synchronize_session=False)
    )).scalar_one()
    return int(new) - 1


async def next_objective_seq(db: AsyncSession, department_id: int, fiscal_year: int, quarter: int) -> int:
    """The next free number for a top-level objective of this department and quarter.
    The quarter-target row holds the counter; it is created here if the Director has not written a target yet."""
    stmt = (
        update(DepartmentQuarterTarget)
        .where(
            DepartmentQuarterTarget.department_id == department_id,
            DepartmentQuarterTarget.fiscal_year == fiscal_year,
            DepartmentQuarterTarget.quarter_number == quarter,
        )
        .values(next_objective_seq=func.coalesce(DepartmentQuarterTarget.next_objective_seq, 1) + 1)
        .returning(DepartmentQuarterTarget.next_objective_seq)
        .execution_options(synchronize_session=False)
    )
    new = (await db.execute(stmt)).scalar()
    if new is None:
        try:
            async with db.begin_nested():
                db.add(DepartmentQuarterTarget(department_id=department_id, fiscal_year=fiscal_year, quarter_number=quarter, next_objective_seq=1))
        except IntegrityError:
            pass  # another request created it a moment ago
        new = (await db.execute(stmt)).scalar_one()
    return int(new) - 1


# ----------------------------------------------------------------- codes of single items

async def objective_path(db: AsyncSession, o: DepartmentObjective) -> str:
    if o.parent_id:
        parent_seq = (await db.execute(select(DepartmentObjective.seq).where(DepartmentObjective.id == o.parent_id))).scalar()
        return f"{parent_seq}.{o.seq}"
    return str(o.seq)


async def objective_full_code(db: AsyncSession, o: DepartmentObjective, dept: Optional[TargetDepartment] = None) -> str:
    dept = dept or (await db.execute(select(TargetDepartment).where(TargetDepartment.id == o.department_id))).scalars().first()
    return full(dept_short_code(dept) if dept else None, await objective_path(db, o))


async def task_code_info(db: AsyncSession, task_id: int) -> Optional[Dict[str, Any]]:
    """{'objective': DepartmentObjective, 'department': TargetDepartment, 'path': '1.2.3', 'full_code': 'PROD-1.2.3'}
    for a task linked (itself or through an ancestor task) to an objective, else None."""
    chain = (await task_chains(db, [task_id])).get(task_id) or [task_id]
    links = {
        tid: (seq, oid)
        for tid, seq, oid in (await db.execute(
            select(ObjectiveTask.task_id, ObjectiveTask.seq, ObjectiveTask.objective_id)
            .where(ObjectiveTask.task_id.in_(chain), ObjectiveTask.is_deleted == False)
        )).all()
    }
    anchor_idx = next((i for i, tid in enumerate(chain) if tid in links), None)
    if anchor_idx is None:
        return None
    seq, oid = links[chain[anchor_idx]]
    o = (await db.execute(select(DepartmentObjective).where(DepartmentObjective.id == oid, DepartmentObjective.is_deleted == False))).scalars().first()
    if not o:
        return None
    dept = (await db.execute(select(TargetDepartment).where(TargetDepartment.id == o.department_id, TargetDepartment.is_deleted == False))).scalars().first()
    if not dept:
        return None
    path = f"{await objective_path(db, o)}.{seq}"
    # walk down from the linked ancestor to the task: its position among its siblings at every step
    for i in range(anchor_idx - 1, -1, -1):
        parent_id, child_id = chain[i + 1], chain[i]
        sibs = [r for (r,) in (await db.execute(
            select(Task.id).where(Task.parent_id == parent_id, Task.is_deleted == False).order_by(Task.id)
        )).all()]
        path += f".{sibs.index(child_id) + 1 if child_id in sibs else len(sibs) + 1}"
    return {"objective": o, "department": dept, "path": path, "full_code": full(dept_short_code(dept), path)}


# ----------------------------------------------------------------- reading a typed code

_CODE_RE = re.compile(r"^#*\s*([A-Z0-9]+)\s*(?:-\s*(\d+(?:\s*\.\s*\d+)*))?$")


def parse_code(raw: str) -> Optional[Tuple[str, List[int]]]:
    """'##prod-1.2.3', ' PROD - 1.2 ' -> ('PROD', [1, 2, 3]). 'PROD' alone -> ('PROD', []). Bad input -> None."""
    s = (raw or "").strip().upper()
    m = _CODE_RE.match(s)
    if not m:
        return None
    path = [int(x) for x in re.split(r"\s*\.\s*", m.group(2))] if m.group(2) else []
    if any(n < 1 or n > 100000 for n in path):
        return None
    return m.group(1), path


# ----------------------------------------------------------------- links the frontend opens

def objective_link(department_id: int, objective_id: int) -> str:
    return f"/departments?id={department_id}&objective={objective_id}"


def task_link(task_id: int) -> str:
    return f"/workcenter?task={task_id}"
