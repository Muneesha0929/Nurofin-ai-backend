"""Timeline of a task or an objective: deadline changes (with the reason), updates and handovers,
merged with status changes, transfers and objective updates that already live in their own tables."""
from typing import Any, Dict, Iterable, List, Optional

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.department_target import DepartmentObjective, DepartmentObjectiveUpdate, TimelineEntry
from app.models.task import Task
from app.models.task_history import TaskHistory
from app.models.task_transfer import TaskTransfer
from app.models.user import User
from app.services.cos_access import iso_utc, valid_date

ENTITIES = ("task", "objective")


def clean_reason(reason: Optional[str]) -> str:
    text = (reason or "").strip()
    if len(text) < 3:
        raise HTTPException(status_code=400, detail="Please give a reason for the new date (at least 3 characters).")
    return text


def clean_date(value: Optional[str], what: str = "new date") -> str:
    v = valid_date(value)
    if not v or len(str(value).strip()) < 10:
        raise HTTPException(status_code=400, detail=f"Please pick a valid {what} (YYYY-MM-DD).")
    return v


def add_entry(db: AsyncSession, entity: str, entity_id: int, kind: str, user_id: Optional[int], *,
              old_date: Optional[str] = None, new_date: Optional[str] = None, text: Optional[str] = None,
              person_id: Optional[int] = None) -> TimelineEntry:
    e = TimelineEntry(entity=entity, entity_id=entity_id, kind=kind, old_date=old_date, new_date=new_date,
                      text=text, person_id=person_id, created_by_id=user_id)
    db.add(e)
    return e


async def deadline_stats(db: AsyncSession, entity: str, ids: Iterable[int]) -> Dict[int, Dict[str, Any]]:
    """{id: {"original": first deadline or None, "changes": number of times an existing deadline was moved}}.
    Only items that have deadline entries appear. One query."""
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    rows = (await db.execute(
        select(TimelineEntry.entity_id, TimelineEntry.old_date, TimelineEntry.new_date)
        .where(TimelineEntry.entity == entity, TimelineEntry.kind == "date_change", TimelineEntry.entity_id.in_(ids), TimelineEntry.is_deleted == False)
        .order_by(TimelineEntry.id)
    )).all()
    out: Dict[int, Dict[str, Any]] = {}
    for eid, old, new in rows:
        s = out.setdefault(eid, {"original": old or new, "changes": 0})
        if old:
            s["changes"] += 1
    return out


def _person(u: Optional[User]) -> Optional[Dict[str, Any]]:
    return {"id": u.id, "name": u.full_name or u.email} if u else None


def entry_payload(e: TimelineEntry, users: Dict[int, User]) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "id": f"e{e.id}", "kind": e.kind, "at": iso_utc(e.created_at), "by": _person(users.get(e.created_by_id)),
    }
    if e.kind == "date_change":
        out["old_date"], out["new_date"] = e.old_date, e.new_date
    if e.text:
        out["text"] = e.text
    if e.person_id:
        out["person"] = _person(users.get(e.person_id))
    return out


async def load_users(db: AsyncSession, ids: Iterable[Optional[int]]) -> Dict[int, User]:
    ids = list({i for i in ids if i})
    if not ids:
        return {}
    return {u.id: u for u in (await db.execute(select(User).where(User.id.in_(ids)))).scalars().all()}


async def timeline_for(db: AsyncSession, entity: str, entity_id: int) -> Dict[str, Any]:
    """Everything that happened to this task / objective, newest first."""
    rows: List[tuple] = []  # (datetime, payload-without-by)
    entries = (await db.execute(
        select(TimelineEntry).where(TimelineEntry.entity == entity, TimelineEntry.entity_id == entity_id, TimelineEntry.is_deleted == False)
    )).scalars().all()
    extra_user_ids: List[Optional[int]] = [e.created_by_id for e in entries] + [e.person_id for e in entries]
    raw: List[Dict[str, Any]] = []
    stamps: Dict[str, Any] = {}

    def put(payload: Dict[str, Any], created_at, by_id: Optional[int]):
        payload["_by"] = by_id
        raw.append(payload)
        stamps[payload["id"]] = created_at

    if entity == "task":
        hist = (await db.execute(
            select(TaskHistory).where(
                TaskHistory.task_id == entity_id,
                TaskHistory.action.in_(("status_changed", "actual_completion_date_changed")),
                TaskHistory.is_deleted == False,
            )
        )).scalars().all()
        for h in hist:
            extra_user_ids.append(h.performed_by_id)
            if h.action == "status_changed":
                put({"id": f"h{h.id}", "kind": "status", "at": iso_utc(h.created_at), "old_value": h.old_value, "new_value": h.new_value,
                     "text": h.description}, h.created_at, h.performed_by_id)
            else:
                put({"id": f"h{h.id}", "kind": "completion_date", "at": iso_utc(h.created_at), "old_value": h.old_value, "new_value": h.new_value,
                     "text": h.description}, h.created_at, h.performed_by_id)
        transfers = (await db.execute(
            select(TaskTransfer).where(TaskTransfer.task_id == entity_id, TaskTransfer.is_deleted == False)
        )).scalars().all()
        for t in transfers:
            extra_user_ids += [t.from_user_id, t.to_user_id, t.transferred_by_id]
            put({"id": f"x{t.id}", "kind": "transfer", "at": iso_utc(t.created_at), "text": t.reason, "_from": t.from_user_id, "_to": t.to_user_id,
                 "new_date": t.new_deadline}, t.created_at, t.transferred_by_id or t.from_user_id)
    else:
        ups = (await db.execute(
            select(DepartmentObjectiveUpdate).where(DepartmentObjectiveUpdate.objective_id == entity_id, DepartmentObjectiveUpdate.is_deleted == False)
        )).scalars().all()
        for u in ups:
            extra_user_ids.append(u.user_id)
            put({"id": f"u{u.id}", "kind": "update", "at": iso_utc(u.created_at), "text": u.text}, u.created_at, u.user_id)

    users = await load_users(db, extra_user_ids)
    out: List[Dict[str, Any]] = []
    for e in entries:
        p = entry_payload(e, users)
        out.append(p)
        stamps[p["id"]] = e.created_at
    for p in raw:
        by = users.get(p.pop("_by"))
        p["by"] = _person(by)
        if "_to" in p:
            p["person"] = _person(users.get(p.pop("_to")))
            p["old_value"] = (users.get(p["_from"]).full_name if users.get(p["_from"]) else None)
            p["new_value"] = (p["person"] or {}).get("name")
            p.pop("_from")
        if p.get("new_date") is None:
            p.pop("new_date", None)
        out.append(p)
    out.sort(key=lambda p: (stamps.get(p["id"]) is not None, stamps.get(p["id"])), reverse=True)
    return {"entries": out}
