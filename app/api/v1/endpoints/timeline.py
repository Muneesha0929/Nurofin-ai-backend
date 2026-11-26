"""Timeline of one task or objective: who moved the deadline and why, updates, handovers.

GET  /timeline/{task|objective}/{id}   newest first, merged from several tables
POST /timeline/{task|objective}/{id}   add an update, or ask someone to look after the item (handover)
"""
from typing import Any, Literal, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import deps
from app.api.v1.endpoints.department_targets import can_see, get_department
from app.core.responses import APIResponse, success_response
from app.models.department_target import DepartmentObjective
from app.models.notification import Notification, NotificationTypeEnum
from app.models.task import Task
from app.models.user import User
from app.services import cos_codes
from app.services.cos_access import task_access
from app.services.cos_timeline import ENTITIES, add_entry, deadline_stats, entry_payload, load_users, timeline_for

router = APIRouter()


class TimelinePost(BaseModel):
    kind: Literal["update", "handover"]
    text: Optional[str] = Field(None, max_length=4000)
    person_id: Optional[int] = None


async def _visible_item(db: AsyncSession, user: User, entity: str, entity_id: int):
    """The task or objective, or 404 when it does not exist or this person may not see it."""
    if entity not in ENTITIES:
        raise HTTPException(status_code=404, detail="Timelines exist for tasks and objectives only.")
    if entity == "task":
        task = (await db.execute(select(Task).where(Task.id == entity_id, Task.is_deleted == False))).scalars().first()
        if not task or not (await task_access(db, user, [task]))[task.id]["see"]:
            raise HTTPException(status_code=404, detail="Task not found.")
        return task
    o = (await db.execute(select(DepartmentObjective).where(DepartmentObjective.id == entity_id, DepartmentObjective.is_deleted == False))).scalars().first()
    if not o:
        raise HTTPException(status_code=404, detail="Objective not found.")
    dept = await get_department(db, o.department_id)
    if not can_see(user, dept):
        raise HTTPException(status_code=404, detail="Objective not found.")
    return o


@router.get("/{entity}/{entity_id}", response_model=APIResponse)
async def get_timeline(entity: str, entity_id: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    item = await _visible_item(db, current_user, entity, entity_id)
    stats = (await deadline_stats(db, entity, [entity_id])).get(entity_id, {})
    current = (item.deadline or "")[:10] or None
    original = (getattr(item, "original_deadline", None) or "")[:10] or stats.get("original") or current
    data = await timeline_for(db, entity, entity_id)
    return success_response(data={
        "original_deadline": original,
        "current_deadline": current,
        "changes": stats.get("changes", 0),
        "entries": data["entries"],
    }, message="Timeline fetched")


@router.post("/{entity}/{entity_id}", response_model=APIResponse)
async def post_timeline(entity: str, entity_id: int, body: TimelinePost, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    item = await _visible_item(db, current_user, entity, entity_id)
    text = (body.text or "").strip()
    person = None
    if body.kind == "update":
        if not text:
            raise HTTPException(status_code=400, detail="Write the update first.")
    else:
        if not body.person_id:
            raise HTTPException(status_code=400, detail="Pick the person you want to look after this.")
        if body.person_id == current_user.id:
            raise HTTPException(status_code=400, detail="Pick someone other than yourself.")
        person = (await db.execute(select(User).where(User.id == body.person_id, User.is_deleted == False))).scalars().first()
        if not person:
            raise HTTPException(status_code=400, detail="That person was not found.")

    entry = add_entry(db, entity, entity_id, body.kind, current_user.id, text=text or None, person_id=person.id if person else None)
    if person:
        if entity == "task":
            info = await cos_codes.task_code_info(db, item.id)
            code, link, title = (info["full_code"] if info else None), cos_codes.task_link(item.id), item.title
        else:
            code, link, title = await cos_codes.objective_full_code(db, item), cos_codes.objective_link(item.department_id, item.id), item.title
        who = current_user.full_name or current_user.email
        db.add(Notification(
            title=f"{who} asked you to look after {code or title}",
            message=(f"{title}: {text}" if text else title)[:240],
            type=NotificationTypeEnum.project_update,
            user_id=person.id,
            link=link,
        ))
    await db.commit()
    users = await load_users(db, [entry.created_by_id, entry.person_id])
    return success_response(data=entry_payload(entry, users), message="Added to the timeline")
