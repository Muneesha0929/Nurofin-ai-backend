"""Product Features: ideas the team raises, Sir's decision on each, and why.

Who can do what:
  - everyone signed in: read and search all features, suggest one, comment, say "I noticed this too",
    add their own solution and like other people's solutions
  - the person who suggested it, or the Director: edit the wording
  - anyone: link their own Task Center tasks to a feature (the Director can link any task); a link
    can be removed by the Director, the feature's author, the task's owner or whoever made it
  - the Director (ceo / super_admin) only: move the status, always with the reason written down,
    and pick the solution to build (its author is credited and told)

Nothing is ever deleted. Every change is written to cos_feature_event (append-only), with the old
and new values, so the history explains itself to someone who joins later.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, exists, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.notification import Notification, NotificationTypeEnum
from app.models.product_feature import (
    FEATURE_SOURCES, FEATURE_STATUSES, ProductFeature, ProductFeatureEvent, ProductFeatureMeToo,
    ProductFeatureSolution, ProductFeatureSolutionVote, ProductFeatureTask,
)
from app.models.task import Task
from app.models.user import User

router = APIRouter()

DIRECTOR_ROLES = ("ceo", "super_admin")
STATUS_LABEL = {"suggested": "Suggested", "planned": "Planned", "building": "Building", "shipped": "Shipped", "not_doing": "Not doing"}
NEEDS_REASON = ("planned", "not_doing")   # a yes or a no must say why
EDITABLE = ("title", "problem", "idea", "area", "source", "source_note")


# ----------------------------------------------------------------- helpers

def _role(user: User) -> str:
    role = getattr(user, "role", "") or ""
    return str(role.value if hasattr(role, "value") else role).lower()


def is_director(user: User) -> bool:
    return _role(user) in DIRECTOR_ROLES


def can_edit(user: User, f: ProductFeature) -> bool:
    return is_director(user) or f.created_by_id == user.id


def code(fid: int) -> str:
    return f"F-{fid}"


def _iso(d: Optional[datetime]) -> Optional[str]:
    return d.isoformat() + "Z" if d else None


def _status(v) -> str:
    return str(v.value if hasattr(v, "value") else (v or "todo"))


def _clean(s: Optional[str], limit: Optional[int] = None) -> Optional[str]:
    s = (s or "").strip()
    if limit:
        s = s[:limit]
    return s or None


async def people(db: AsyncSession, ids) -> Dict[int, Dict[str, Any]]:
    ids = {i for i in ids if i}
    if not ids:
        return {}
    rows = (await db.execute(select(User).filter(User.id.in_(ids)))).scalars().all()
    return {u.id: {"id": u.id, "name": u.full_name or u.email, "avatar": u.profile_picture} for u in rows}


async def get_feature(db: AsyncSession, fid: int) -> ProductFeature:
    f = (await db.execute(select(ProductFeature).filter(ProductFeature.id == fid, ProductFeature.is_deleted == False))).scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="Feature not found.")
    return f


def log(db: AsyncSession, f: ProductFeature, user: User, kind: str, text: Optional[str] = None, data: Optional[dict] = None) -> None:
    db.add(ProductFeatureEvent(feature_id=f.id, kind=kind, user_id=user.id, text=text, data=data))
    f.updated_at = datetime.utcnow()


def notify(db: AsyncSession, user_id: Optional[int], actor: User, title: str, message: str, fid: int) -> None:
    if user_id and user_id != actor.id:
        db.add(Notification(title=title, message=message, type=NotificationTypeEnum.project_update,
                            user_id=user_id, link=f"/features?id={fid}"))


async def tasks_of(db: AsyncSession, feature_ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not feature_ids:
        return {}
    rows = (await db.execute(
        select(ProductFeatureTask.feature_id, Task.id, Task.title, cast(Task.status, String), Task.deadline, Task.assigned_to_id,
               Task.assigned_by_id, ProductFeatureTask.linked_by_id)
        .join(Task, Task.id == ProductFeatureTask.task_id)
        .filter(ProductFeatureTask.feature_id.in_(feature_ids), ProductFeatureTask.is_deleted == False, Task.is_deleted == False)
        .order_by(Task.id)
    )).all()
    who = await people(db, [r[5] for r in rows])
    out: Dict[int, List[Dict[str, Any]]] = {}
    for fid, tid, title, status, deadline, uid, by_id, linker in rows:
        st = _status(status)
        out.setdefault(fid, []).append({
            "id": tid, "title": title, "status": st, "done": st in ("completed", "done"),
            "deadline": (deadline or "")[:10] or None, "assignee": who.get(uid),
            "_owners": {uid, by_id, linker} - {None},
        })
    return out


def summary(f: ProductFeature, who: Dict[int, Any], tasks: List[Dict[str, Any]], me_too: int, comments: int, mine_too: bool,
            solutions: int = 0, chosen: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "id": f.id, "code": code(f.id), "title": f.title, "problem": f.problem, "idea": f.idea,
        "area": f.area, "source": f.source, "source_note": f.source_note,
        "status": f.status, "status_label": STATUS_LABEL.get(f.status, f.status),
        "decision_note": f.decision_note, "decided_by": who.get(f.decided_by_id), "decided_at": _iso(f.decided_at),
        "shipped_at": _iso(f.shipped_at),
        "created_by": who.get(f.created_by_id), "created_at": _iso(f.created_at), "updated_at": _iso(f.updated_at),
        "tasks_total": len(tasks), "tasks_done": sum(1 for t in tasks if t["done"]),
        "me_too": me_too, "me_too_mine": mine_too, "comments": comments,
        "solutions": solutions, "chosen_solution": chosen,
    }


# ----------------------------------------------------------------- schemas

class FeatureIn(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)
    problem: str = Field(..., min_length=1, max_length=5000)
    idea: Optional[str] = Field(None, max_length=5000)
    area: Optional[str] = Field(None, max_length=80)
    source: str = "team"
    source_note: Optional[str] = Field(None, max_length=200)


class FeatureEdit(BaseModel):
    title: Optional[str] = Field(None, max_length=200)
    problem: Optional[str] = Field(None, max_length=5000)
    idea: Optional[str] = Field(None, max_length=5000)
    area: Optional[str] = Field(None, max_length=80)
    source: Optional[str] = None
    source_note: Optional[str] = Field(None, max_length=200)


class StatusIn(BaseModel):
    status: str
    note: Optional[str] = Field(None, max_length=5000)


class TextIn(BaseModel):
    text: Optional[str] = Field(None, max_length=5000)


class SolutionIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class ChooseIn(BaseModel):
    note: Optional[str] = Field(None, max_length=5000)


class TaskLinkIn(BaseModel):
    task_id: int


# ----------------------------------------------------------------- list + search

@router.get("", response_model=APIResponse)
async def list_features(
    q: Optional[str] = Query(None, max_length=200),
    status: Optional[str] = None,
    area: Optional[str] = None,
    mine: bool = False,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """All features, newest activity first. q searches the title, the problem, the idea, the area,
    who raised it, every decision reason and every comment, so "why did we build X" finds X."""
    base = select(ProductFeature).filter(ProductFeature.is_deleted == False)
    if area:
        base = base.filter(ProductFeature.area == area)
    if mine:
        base = base.filter(ProductFeature.created_by_id == current_user.id)
    term = (q or "").strip()
    if term:
        like = f"%{term}%"
        in_events = exists().where(ProductFeatureEvent.feature_id == ProductFeature.id, ProductFeatureEvent.text.ilike(like))
        in_solutions = exists().where(ProductFeatureSolution.feature_id == ProductFeature.id, ProductFeatureSolution.text.ilike(like))
        conds = [ProductFeature.title.ilike(like), ProductFeature.problem.ilike(like), ProductFeature.idea.ilike(like),
                 ProductFeature.area.ilike(like), ProductFeature.source_note.ilike(like), ProductFeature.decision_note.ilike(like), in_events, in_solutions]
        num = term.upper().removeprefix("F-").removeprefix("F").strip()
        if num.isdigit():
            conds.append(ProductFeature.id == int(num))
        base = base.filter(or_(*conds))

    everything = (await db.execute(base.order_by(ProductFeature.updated_at.desc(), ProductFeature.id.desc()))).scalars().all()
    counts = {s: 0 for s in FEATURE_STATUSES}
    for f in everything:
        counts[f.status] = counts.get(f.status, 0) + 1
    rows = [f for f in everything if not status or f.status == status]

    ids = [f.id for f in rows]
    tally: Dict[int, Dict[str, int]] = {}
    mine_too = set()
    if ids:
        for fid, n in (await db.execute(
            select(ProductFeatureEvent.feature_id, func.count()).filter(ProductFeatureEvent.feature_id.in_(ids), ProductFeatureEvent.kind == "comment")
            .group_by(ProductFeatureEvent.feature_id)
        )).all():
            tally.setdefault(fid, {})["comment"] = n
        for fid, n in (await db.execute(
            select(ProductFeatureMeToo.feature_id, func.count()).filter(ProductFeatureMeToo.feature_id.in_(ids)).group_by(ProductFeatureMeToo.feature_id)
        )).all():
            tally.setdefault(fid, {})["me_too"] = n
        for fid, n in (await db.execute(
            select(ProductFeatureSolution.feature_id, func.count()).filter(ProductFeatureSolution.feature_id.in_(ids), ProductFeatureSolution.is_deleted == False)
            .group_by(ProductFeatureSolution.feature_id)
        )).all():
            tally.setdefault(fid, {})["solutions"] = n
        mine_too = set((await db.execute(
            select(ProductFeatureMeToo.feature_id).filter(ProductFeatureMeToo.feature_id.in_(ids), ProductFeatureMeToo.user_id == current_user.id)
        )).scalars().all())
    chosen_ids = [f.chosen_solution_id for f in rows if f.chosen_solution_id]
    chosen_rows = {c.id: c for c in (await db.execute(
        select(ProductFeatureSolution).filter(ProductFeatureSolution.id.in_(chosen_ids)))).scalars().all()} if chosen_ids else {}
    tasks = await tasks_of(db, ids)
    who = await people(db, [f.created_by_id for f in rows] + [f.decided_by_id for f in rows] + [c.created_by_id for c in chosen_rows.values()])
    chosen_of = lambda f: ({"id": c.id, "text": c.text, "by": who.get(c.created_by_id)} if (c := chosen_rows.get(f.chosen_solution_id)) else None)
    areas = sorted({a for a in (await db.execute(
        select(ProductFeature.area).filter(ProductFeature.is_deleted == False, ProductFeature.area.isnot(None)).distinct()
    )).scalars().all() if a})

    return success_response(data={
        "features": [summary(f, who, tasks.get(f.id, []), tally.get(f.id, {}).get("me_too", 0),
                             tally.get(f.id, {}).get("comment", 0), f.id in mine_too,
                             tally.get(f.id, {}).get("solutions", 0), chosen_of(f)) for f in rows],
        "counts": counts, "total": len(everything), "areas": areas,
        "is_director": is_director(current_user), "me": current_user.id,
    })


@router.get("/task-links", response_model=APIResponse)
async def task_links(
    task_ids: str = Query(..., max_length=4000),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """For the Task Center: which features each task builds. {task_id: [{id, code, title, status}]}"""
    ids = [int(x) for x in task_ids.split(",") if x.strip().isdigit()][:500]
    if not ids:
        return success_response(data={})
    rows = (await db.execute(
        select(ProductFeatureTask.task_id, ProductFeature.id, ProductFeature.title, ProductFeature.status)
        .join(ProductFeature, ProductFeature.id == ProductFeatureTask.feature_id)
        .filter(ProductFeatureTask.task_id.in_(ids), ProductFeatureTask.is_deleted == False, ProductFeature.is_deleted == False)
        .order_by(ProductFeatureTask.id)
    )).all()
    out: Dict[str, List[Dict[str, Any]]] = {}
    for tid, fid, title, status in rows:
        out.setdefault(str(tid), []).append({"id": fid, "code": code(fid), "title": title, "status": status,
                                             "status_label": STATUS_LABEL.get(status, status)})
    return success_response(data=out)


# ----------------------------------------------------------------- one feature

async def detail(db: AsyncSession, f: ProductFeature, user: User) -> Dict[str, Any]:
    events = (await db.execute(
        select(ProductFeatureEvent).filter(ProductFeatureEvent.feature_id == f.id).order_by(ProductFeatureEvent.id)
    )).scalars().all()
    tasks = (await tasks_of(db, [f.id])).get(f.id, [])
    for t in tasks:
        t["can_unlink"] = is_director(user) or f.created_by_id == user.id or user.id in t.pop("_owners")
    me_too = (await db.execute(select(ProductFeatureMeToo.user_id).filter(ProductFeatureMeToo.feature_id == f.id)
                               .order_by(ProductFeatureMeToo.id))).scalars().all()
    sols = (await db.execute(select(ProductFeatureSolution).filter(
        ProductFeatureSolution.feature_id == f.id, ProductFeatureSolution.is_deleted == False).order_by(ProductFeatureSolution.id))).scalars().all()
    votes: Dict[int, List[int]] = {}
    if sols:
        for sid, uid in (await db.execute(select(ProductFeatureSolutionVote.solution_id, ProductFeatureSolutionVote.user_id)
                                          .filter(ProductFeatureSolutionVote.solution_id.in_([x.id for x in sols])))).all():
            votes.setdefault(sid, []).append(uid)
    who = await people(db, [f.created_by_id, f.decided_by_id] + [e.user_id for e in events] + list(me_too)
                       + [x.created_by_id for x in sols] + [u for v in votes.values() for u in v])
    solutions = [{
        "id": x.id, "text": x.text, "by": who.get(x.created_by_id), "created_at": _iso(x.created_at),
        "votes": len(votes.get(x.id, [])), "voted": user.id in votes.get(x.id, []),
        "voters": [who[u] for u in votes.get(x.id, []) if u in who],
        "chosen": x.id == f.chosen_solution_id, "mine": x.created_by_id == user.id,
    } for x in sols]
    # The pick first, then the most liked, then the oldest.
    solutions.sort(key=lambda x: (not x["chosen"], -x["votes"], x["id"]))
    chosen = next(({"id": x["id"], "text": x["text"], "by": x["by"]} for x in solutions if x["chosen"]), None)
    out = summary(f, who, tasks, len(me_too), sum(1 for e in events if e.kind == "comment"),
                  user.id in me_too, len(solutions), chosen)
    out.update({
        "tasks": tasks,
        "solutions_list": solutions,
        "me_too_people": [who[u] for u in me_too if u in who],
        "events": [{"id": e.id, "kind": e.kind, "user": who.get(e.user_id), "text": e.text, "data": e.data,
                    "created_at": _iso(e.created_at)} for e in events],
        "permissions": {"can_edit": can_edit(user, f), "can_decide": is_director(user), "can_link": True},
    })
    return out


@router.post("", response_model=APIResponse)
async def create_feature(
    body: FeatureIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    title, problem = _clean(body.title, 200), _clean(body.problem)
    if not title or not problem:
        raise HTTPException(status_code=422, detail="Give it a name and say what you noticed.")
    f = ProductFeature(
        title=title, problem=problem, idea=_clean(body.idea), area=_clean(body.area, 80),
        source=body.source if body.source in FEATURE_SOURCES else "team", source_note=_clean(body.source_note, 200),
        status="suggested", created_by_id=current_user.id,
    )
    db.add(f)
    await db.flush()
    log(db, f, current_user, "created", data={"title": f.title})
    if f.idea:
        sol = ProductFeatureSolution(feature_id=f.id, text=f.idea, created_by_id=current_user.id)
        db.add(sol)
        await db.flush()
        log(db, f, current_user, "solution_added", text=f.idea, data={"solution_id": sol.id})
    if not is_director(current_user):
        ceos = (await db.execute(select(User.id).filter(func.lower(cast(User.role, String)) == "ceo"))).scalars().all()
        for uid in ceos:
            notify(db, uid, current_user, "New feature idea", f"{code(f.id)} · {f.title}", f.id)
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Feature suggested")


@router.get("/{fid}", response_model=APIResponse)
async def get_one(fid: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    return success_response(data=await detail(db, await get_feature(db, fid), current_user))


@router.put("/{fid}", response_model=APIResponse)
async def edit_feature(
    fid: int, body: FeatureEdit,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    f = await get_feature(db, fid)
    if not can_edit(current_user, f):
        raise HTTPException(status_code=403, detail="Only the person who suggested this, or the Director, can edit it.")
    changes: Dict[str, List[Optional[str]]] = {}
    for field in EDITABLE:
        if field not in body.model_fields_set:
            continue
        new = getattr(body, field)
        if field == "source":
            new = new if new in FEATURE_SOURCES else f.source
        else:
            new = _clean(new, 200 if field in ("title", "source_note") else 80 if field == "area" else None)
        if field in ("title", "problem") and not new:
            raise HTTPException(status_code=422, detail="The name and what was noticed can't be empty.")
        old = getattr(f, field)
        if new != old:
            changes[field] = [old, new]
            setattr(f, field, new)
    if changes:
        log(db, f, current_user, "edited", data={"changes": changes})
        await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Saved" if changes else "Nothing changed")


@router.post("/{fid}/status", response_model=APIResponse)
async def set_status(
    fid: int, body: StatusIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    if not is_director(current_user):
        raise HTTPException(status_code=403, detail="Only the Director decides on features.")
    if body.status not in FEATURE_STATUSES:
        raise HTTPException(status_code=422, detail="Unknown status.")
    f = await get_feature(db, fid)
    note = _clean(body.note)
    if body.status in NEEDS_REASON and not note:
        raise HTTPException(status_code=422, detail=f"Write down why: it's what people will read later. ({STATUS_LABEL[body.status]} needs a reason.)")
    if body.status == f.status and not note:
        return success_response(data=await detail(db, f, current_user), message="Nothing changed")
    old = f.status
    f.status = body.status
    if note:
        f.decision_note = note
    f.decided_by_id, f.decided_at = current_user.id, datetime.utcnow()
    if body.status != "shipped":
        f.shipped_at = None
    elif old != "shipped":
        f.shipped_at = datetime.utcnow()
    log(db, f, current_user, "status", text=note, data={"from": old, "to": body.status})
    notify(db, f.created_by_id, current_user, f"Your idea is {STATUS_LABEL[body.status].lower()}",
           f"{code(f.id)} · {f.title}" + (f": {note[:140]}" if note else ""), f.id)
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message=STATUS_LABEL[body.status])


@router.post("/{fid}/comments", response_model=APIResponse)
async def comment(
    fid: int, body: TextIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    f = await get_feature(db, fid)
    text = _clean(body.text)
    if not text:
        raise HTTPException(status_code=422, detail="Write something first.")
    log(db, f, current_user, "comment", text=text)
    notify(db, f.created_by_id, current_user, "New comment on your idea", f"{code(f.id)} · {text[:140]}", f.id)
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Comment added")


@router.post("/{fid}/me-too", response_model=APIResponse)
async def me_too(
    fid: int, body: TextIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """'I noticed this too' - once per person. It counts as more evidence, so it can't be taken back."""
    f = await get_feature(db, fid)
    try:
        db.add(ProductFeatureMeToo(feature_id=f.id, user_id=current_user.id))
        log(db, f, current_user, "me_too", text=_clean(body.text))
        await db.commit()
    except IntegrityError:
        # Already counted (a double click, or two tabs). The unique key keeps it to one.
        await db.rollback()
        f = await get_feature(db, fid)
    return success_response(data=await detail(db, f, current_user), message="Noted")


# ----------------------------------------------------------------- solutions

async def get_solution(db: AsyncSession, f: ProductFeature, sid: int) -> ProductFeatureSolution:
    s = (await db.execute(select(ProductFeatureSolution).filter(
        ProductFeatureSolution.id == sid, ProductFeatureSolution.feature_id == f.id, ProductFeatureSolution.is_deleted == False))).scalar_one_or_none()
    if not s:
        raise HTTPException(status_code=404, detail="Solution not found.")
    return s


@router.post("/{fid}/solutions", response_model=APIResponse)
async def add_solution(
    fid: int, body: SolutionIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Anyone can suggest a way to fix it, not just the person who raised it."""
    f = await get_feature(db, fid)
    text = _clean(body.text)
    if not text:
        raise HTTPException(status_code=422, detail="Write the solution first.")
    s = ProductFeatureSolution(feature_id=f.id, text=text, created_by_id=current_user.id)
    db.add(s)
    await db.flush()
    log(db, f, current_user, "solution_added", text=text, data={"solution_id": s.id})
    notify(db, f.created_by_id, current_user, "New solution for your idea", f"{code(f.id)} · {text[:140]}", f.id)
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Solution added")


@router.put("/{fid}/solutions/{sid}", response_model=APIResponse)
async def edit_solution(
    fid: int, sid: int, body: SolutionIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    f = await get_feature(db, fid)
    s = await get_solution(db, f, sid)
    if s.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the person who wrote this solution can change it.")
    text = _clean(body.text)
    if not text:
        raise HTTPException(status_code=422, detail="The solution can't be empty.")
    if text != s.text:
        log(db, f, current_user, "solution_edited", data={"solution_id": s.id, "changes": {"solution": [s.text, text]}})
        s.text = text
        await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Saved")


@router.post("/{fid}/solutions/{sid}/vote", response_model=APIResponse)
async def vote_solution(
    fid: int, sid: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Like a solution, or take the like back. Not on your own solution."""
    f = await get_feature(db, fid)
    s = await get_solution(db, f, sid)
    if s.created_by_id == current_user.id:
        raise HTTPException(status_code=422, detail="You can't like your own solution.")
    row = (await db.execute(select(ProductFeatureSolutionVote).filter(
        ProductFeatureSolutionVote.solution_id == s.id, ProductFeatureSolutionVote.user_id == current_user.id))).scalar_one_or_none()
    try:
        if row:
            await db.delete(row)
        else:
            db.add(ProductFeatureSolutionVote(solution_id=s.id, user_id=current_user.id))
        await db.commit()
    except IntegrityError:
        await db.rollback()
        f = await get_feature(db, fid)
    return success_response(data=await detail(db, f, current_user), message="Saved")


@router.post("/{fid}/solutions/{sid}/choose", response_model=APIResponse)
async def choose_solution(
    fid: int, sid: int, body: ChooseIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """The Director picks the solution to build. Its author is credited on the feature and told."""
    if not is_director(current_user):
        raise HTTPException(status_code=403, detail="Only the Director picks the solution.")
    f = await get_feature(db, fid)
    s = await get_solution(db, f, sid)
    if f.chosen_solution_id == s.id:
        return success_response(data=await detail(db, f, current_user), message="Already chosen")
    author = (await people(db, [s.created_by_id])).get(s.created_by_id)
    previous = f.chosen_solution_id
    f.chosen_solution_id = s.id
    log(db, f, current_user, "solution_chosen", text=_clean(body.note),
        data={"solution_id": s.id, "by": author["name"] if author else None, "solution": s.text[:300], "replaced": previous})
    notify(db, s.created_by_id, current_user, "Your solution was chosen",
           f"{code(f.id)} · {f.title}: we're going with your idea. Thank you!", f.id)
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Solution chosen")


# ----------------------------------------------------------------- Task Center links

@router.get("/{fid}/linkable-tasks", response_model=APIResponse)
async def linkable_tasks(
    fid: int, q: Optional[str] = Query(None, max_length=200),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    """Task Center tasks that could be linked: the Director sees all, everyone else their own."""
    f = await get_feature(db, fid)
    linked = select(ProductFeatureTask.task_id).filter(ProductFeatureTask.feature_id == f.id, ProductFeatureTask.is_deleted == False)
    stmt = select(Task.id, Task.title, cast(Task.status, String), Task.assigned_to_id).filter(
        Task.is_deleted == False, Task.parent_id.is_(None), Task.id.notin_(linked))
    if not is_director(current_user):
        stmt = stmt.filter(or_(Task.assigned_to_id == current_user.id, Task.assigned_by_id == current_user.id))
    if (q or "").strip():
        stmt = stmt.filter(Task.title.ilike(f"%{q.strip()}%"))
    rows = (await db.execute(stmt.order_by(Task.id.desc()).limit(40))).all()
    who = await people(db, [r[3] for r in rows])
    return success_response(data=[{"id": r[0], "title": r[1], "status": _status(r[2]), "assignee": who.get(r[3])} for r in rows])


@router.post("/{fid}/tasks", response_model=APIResponse)
async def link_task(
    fid: int, body: TaskLinkIn,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    f = await get_feature(db, fid)
    task = (await db.execute(select(Task).filter(Task.id == body.task_id, Task.is_deleted == False))).scalar_one_or_none()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    if not is_director(current_user) and current_user.id not in (task.assigned_to_id, task.assigned_by_id):
        raise HTTPException(status_code=403, detail="You can only link your own tasks.")
    row = (await db.execute(select(ProductFeatureTask).filter(
        ProductFeatureTask.feature_id == f.id, ProductFeatureTask.task_id == task.id))).scalar_one_or_none()
    if row and not row.is_deleted:
        return success_response(data=await detail(db, f, current_user), message="Already linked")
    if row:
        row.is_deleted, row.linked_by_id = False, current_user.id
    else:
        db.add(ProductFeatureTask(feature_id=f.id, task_id=task.id, linked_by_id=current_user.id))
    log(db, f, current_user, "task_linked", data={"task_id": task.id, "title": task.title})
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Task linked")


@router.delete("/{fid}/tasks/{task_id}", response_model=APIResponse)
async def unlink_task(
    fid: int, task_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
):
    f = await get_feature(db, fid)
    row = (await db.execute(select(ProductFeatureTask).filter(
        ProductFeatureTask.feature_id == f.id, ProductFeatureTask.task_id == task_id, ProductFeatureTask.is_deleted == False))).scalar_one_or_none()
    if row:
        task = (await db.execute(select(Task.assigned_to_id, Task.assigned_by_id).filter(Task.id == task_id))).first()
        owners = {row.linked_by_id, *(task or ())} - {None}
        if not (is_director(current_user) or f.created_by_id == current_user.id or current_user.id in owners):
            raise HTTPException(status_code=403, detail="Only the Director, the person who suggested this, or the task's owner can unlink it.")
        row.is_deleted = True
        title = (await db.execute(select(Task.title).filter(Task.id == task_id))).scalar_one_or_none()
        log(db, f, current_user, "task_unlinked", data={"task_id": task_id, "title": title})
        await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Task unlinked")
