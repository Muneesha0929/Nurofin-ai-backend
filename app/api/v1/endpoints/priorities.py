"""CEO priorities on objectives: who has to be told, and when the pop-up shows again.

GET  /priorities/mine   the open, prioritised objectives that concern me (the CEO sees all of them, never a pop-up)
POST /priorities/seen   I have read these: stop popping them up (P1 comes back every new day, P2 / P3 only when the priority changes)

The CEO sets a priority with PUT /department-targets/objectives/{id} {"priority": "P1" | "P2" | "P3" | null}.
"""
from datetime import date
from typing import Any, Dict, List

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.api import deps
from app.api.v1.endpoints.department_targets import (
    PRIORITY_RANK,
    build_tree,
    objectives_for,
    priority_recipients,
    tasks_for,
    user_brief,
)
from app.core.responses import APIResponse, success_response
from app.models.department_target import DepartmentObjective, PrioritySeen, TargetDepartment
from app.models.user import User
from app.services import cos_codes
from app.services.cos_access import cos_today, is_ceo, iso_utc

router = APIRouter()


class SeenIn(BaseModel):
    objective_ids: List[int] = Field(default_factory=list, max_length=500)


@router.get("/mine", response_model=APIResponse)
async def my_priorities(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    today = cos_today()
    ceo = is_ceo(current_user)
    prioritised = list((await db.execute(
        select(DepartmentObjective)
        .options(selectinload(DepartmentObjective.owner))
        .join(TargetDepartment, TargetDepartment.id == DepartmentObjective.department_id)
        .where(DepartmentObjective.is_deleted == False, TargetDepartment.is_deleted == False, DepartmentObjective.priority.in_(list(PRIORITY_RANK)))
    )).scalars().all())
    if not prioritised:
        return success_response(data={"today": today.isoformat(), "items": []}, message="Priorities fetched")

    if ceo:
        mine = prioritised
    else:
        recipients = await priority_recipients(db, prioritised)
        mine = [o for o in prioritised if current_user.id in recipients.get(o.id, set())]

    depts = {d.id: d for d in (await db.execute(
        select(TargetDepartment).options(selectinload(TargetDepartment.members)).where(TargetDepartment.id.in_({o.department_id for o in mine} or {0}))
    )).scalars().all()}

    # progress and status come from the same roll-up as the department page (one tree per department + quarter)
    nodes: Dict[int, Dict[str, Any]] = {}
    for did, fy, q in sorted({(o.department_id, o.fiscal_year, o.quarter_number) for o in mine}):
        objs = await objectives_for(db, [did], fy, q)
        tree = build_tree(objs, date.today().isoformat(), tasks=await tasks_for(db, [x.id for x in objs]), dept_codes={did: cos_codes.dept_short_code(depts[did])})

        def walk(ns):
            for n in ns:
                nodes[n["id"]] = n
                walk(n["children"])
        walk(tree)

    seen = {}
    if not ceo:
        seen = {r.objective_id: r for r in (await db.execute(
            select(PrioritySeen).where(PrioritySeen.user_id == current_user.id, PrioritySeen.objective_id.in_([o.id for o in mine] or [0]))
        )).scalars().all()}

    items = []
    for o in mine:
        n = nodes.get(o.id)
        if not n or n["status"] == "done":
            continue
        d = depts[o.department_id]
        s = seen.get(o.id)
        if ceo:
            popup = False
        elif s is None or s.priority != o.priority:
            popup = True
        else:
            popup = o.priority == "P1" and (s.seen_on or "") < today.isoformat()
        items.append({
            "objective_id": o.id,
            "full_code": n["full_code"],
            "title": o.title,
            "priority": o.priority,
            "department": {"id": d.id, "name": d.name, "short_code": cos_codes.dept_short_code(d)},
            "deadline": o.deadline,
            "progress": n["progress"],
            "status": n["status"],
            "owner": user_brief(o.owner),
            "link": cos_codes.objective_link(d.id, o.id),
            "set_at": iso_utc(o.priority_set_at),
            "show_popup": popup,
        })
    items.sort(key=lambda i: (PRIORITY_RANK[i["priority"]], i["deadline"] is None, i["deadline"] or "", i["objective_id"]))
    return success_response(data={"today": today.isoformat(), "items": items}, message="Priorities fetched")


@router.post("/seen", response_model=APIResponse)
async def mark_seen(body: SeenIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    """Remember that I have seen these priorities today (at their current level)."""
    ids = list(dict.fromkeys(body.objective_ids))
    if not ids:
        return success_response(data={"marked": 0}, message="Nothing to mark")
    objs = (await db.execute(select(DepartmentObjective).where(
        DepartmentObjective.id.in_(ids), DepartmentObjective.is_deleted == False, DepartmentObjective.priority.in_(list(PRIORITY_RANK))
    ))).scalars().all()
    existing = {r.objective_id: r for r in (await db.execute(
        select(PrioritySeen).where(PrioritySeen.user_id == current_user.id, PrioritySeen.objective_id.in_([o.id for o in objs] or [0]))
    )).scalars().all()}
    today = cos_today().isoformat()
    for o in objs:
        row = existing.get(o.id)
        if row:
            row.priority, row.seen_on = o.priority, today
        else:
            db.add(PrioritySeen(user_id=current_user.id, objective_id=o.id, priority=o.priority, seen_on=today))
    try:
        await db.commit()
    except IntegrityError:  # the same person marked it from two tabs at once: the other write already did the job
        await db.rollback()
    return success_response(data={"marked": len(objs)}, message="Marked as seen")
