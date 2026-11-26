"""Who is who in Chief of Staff: roles, 'today', and who may edit or see a Task Center task.

A task can be linked to an objective (cos_objective_task). Subtasks inherit the link of their
nearest linked ancestor, the same way the Task Center shows it.
"""
import os
from datetime import date, datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Set

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department_target import DepartmentObjective, ObjectiveTask, TargetDepartment, target_department_members
from app.models.task import Task
from app.models.user import User

IST = timezone(timedelta(hours=5, minutes=30))
DIRECTOR_ROLES = ("ceo", "super_admin", "admin", "director", "chief_of_staff")
CEO_ROLES = ("ceo", "super_admin")


def user_role(user: User) -> str:
    role = getattr(user, "role", "") or ""
    return str(role.value if hasattr(role, "value") else role).lower()


def is_director(user: User) -> bool:
    return user_role(user) in DIRECTOR_ROLES or user.id == 1


def is_ceo(user: User) -> bool:
    """The CEO only (role ceo or super_admin, or user 1). Narrower than is_director."""
    return user_role(user) in CEO_ROLES or user.id == 1


def cos_today() -> date:
    """Today in India. COS_TODAY (YYYY-MM-DD) pretends it is another day: local testing only, read on every call."""
    fixed = (os.environ.get("COS_TODAY") or "").strip()
    if not fixed:
        try:
            from app.core.config import settings
            fixed = (getattr(settings, "COS_TODAY", "") or "").strip()
        except Exception:
            fixed = ""
    if fixed:
        try:
            return date.fromisoformat(fixed)
        except ValueError:
            pass
    return datetime.now(IST).date()


def iso_utc(dt: Optional[datetime]) -> Optional[str]:
    """Same convention as the rest of the app: naive UTC (a few legacy rows were saved in IST) -> ISO with Z."""
    if not dt:
        return None
    if dt > datetime.utcnow() + timedelta(minutes=15):
        dt = dt - timedelta(hours=5, minutes=30)
    s = dt.isoformat()
    if not s.endswith("Z") and "+" not in s:
        s += "Z"
    return s


def valid_date(value: Optional[str]) -> Optional[str]:
    """YYYY-MM-DD or None."""
    if not value:
        return None
    v = str(value).strip()[:10]
    try:
        date.fromisoformat(v)
    except ValueError:
        return None
    return v


async def task_chains(db: AsyncSession, task_ids: Iterable[int]) -> Dict[int, List[int]]:
    """For each task: [itself, its parent, grandparent ...] (up to 6 levels). Two or three queries in total."""
    ids = list({i for i in task_ids if i})
    chains: Dict[int, List[int]] = {i: [i] for i in ids}
    parent_of: Dict[int, Optional[int]] = {}
    frontier = list(ids)
    for _ in range(6):
        frontier = [i for i in frontier if i not in parent_of]
        if not frontier:
            break
        rows = (await db.execute(select(Task.id, Task.parent_id).where(Task.id.in_(frontier)))).all()
        next_frontier = []
        for tid, pid in rows:
            parent_of[tid] = pid
            if pid:
                next_frontier.append(pid)
        frontier = next_frontier
    for tid, chain in chains.items():
        cur = parent_of.get(tid)
        while cur and cur not in chain and len(chain) < 8:
            chain.append(cur)
            cur = parent_of.get(cur)
    return chains


async def linked_department_ids(db: AsyncSession, task_ids: Iterable[int]) -> Dict[int, Set[int]]:
    """Departments each task belongs to through an objective link on itself or an ancestor task."""
    chains = await task_chains(db, task_ids)
    all_ids = {i for c in chains.values() for i in c}
    if not all_ids:
        return {}
    rows = (await db.execute(
        select(ObjectiveTask.task_id, DepartmentObjective.department_id)
        .join(DepartmentObjective, DepartmentObjective.id == ObjectiveTask.objective_id)
        .join(TargetDepartment, TargetDepartment.id == DepartmentObjective.department_id)
        .where(
            ObjectiveTask.task_id.in_(all_ids),
            ObjectiveTask.is_deleted == False,
            DepartmentObjective.is_deleted == False,
            TargetDepartment.is_deleted == False,
        )
    )).all()
    dept_by_task: Dict[int, int] = {tid: did for tid, did in rows}
    out: Dict[int, Set[int]] = {}
    for tid, chain in chains.items():
        for cid in chain:
            if cid in dept_by_task:
                out.setdefault(tid, set()).add(dept_by_task[cid])
                break  # the nearest linked ancestor decides
    return out


async def task_access(db: AsyncSession, user: User, tasks: List[Task]) -> Dict[int, Dict[str, bool]]:
    """{task_id: {"edit": bool, "see": bool}}.
    edit = assignee, creator, HOD of a linked department, or the Director.
    see  = edit, plus reviewer and members of a linked department."""
    out: Dict[int, Dict[str, bool]] = {}
    if not tasks:
        return out
    director = is_director(user)
    dept_map = {} if director else await linked_department_ids(db, [t.id for t in tasks])
    dept_ids = {d for ds in dept_map.values() for d in ds}
    hods: Dict[int, Optional[int]] = {}
    members: Dict[int, Set[int]] = {}
    if dept_ids:
        for did, hod_id in (await db.execute(select(TargetDepartment.id, TargetDepartment.hod_id).where(TargetDepartment.id.in_(dept_ids)))).all():
            hods[did] = hod_id
        for did, uid in (await db.execute(
            select(target_department_members.c.department_id, target_department_members.c.user_id)
            .where(target_department_members.c.department_id.in_(dept_ids), target_department_members.c.user_id == user.id)
        )).all():
            members.setdefault(did, set()).add(uid)
    for t in tasks:
        own = user.id in (t.assigned_to_id, t.assigned_by_id)
        ds = dept_map.get(t.id, set())
        is_hod = any(hods.get(d) == user.id for d in ds)
        edit = director or own or is_hod
        see = edit or t.reviewer_id == user.id or any(user.id in members.get(d, set()) for d in ds)
        out[t.id] = {"edit": edit, "see": see}
    return out


async def require_task_edit(db: AsyncSession, user: User, tasks: List[Task], what: str = "change this", list_ids: bool = False) -> None:
    """403 unless the caller may edit every one of these tasks. Names the task ids that block it."""
    access = await task_access(db, user, tasks)
    blocked = [t.id for t in tasks if not access[t.id]["edit"]]
    if blocked:
        ids = ", ".join(str(i) for i in blocked)
        if len(tasks) == 1 and not list_ids:
            raise HTTPException(status_code=403, detail=f"Only the assignee, the person who created the task, the department HOD or the Director can {what}.")
        raise HTTPException(status_code=403, detail=f"You can't change task {ids}: only the assignee, the person who created it, the department HOD or the Director can. Nothing was changed.")
