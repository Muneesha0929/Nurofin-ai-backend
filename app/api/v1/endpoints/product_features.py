"""Product Features: ideas the team raises, Sir's decision on each, and why.

Who can do what:
  - everyone signed in: read and search all features, suggest one, comment, say "I noticed this too",
    add their own solution and like other people's solutions
  - the person who suggested it, or the Director: edit the wording
  - anyone: link their own Task Center tasks to a feature (the Director can link any task); a link
    can be removed by the Director, the feature's author, the task's owner or whoever made it
  - the Director (ceo / super_admin) only: move the status, always with the reason written down
  - the Director, or the HOD of a department the author belongs to: pick the solution to build
    ("Go with this"; its author is credited and told) and keep the cost and profit estimate
  - anyone: tag products, attach screenshots or documents (the uploader or the Director removes one)

Nothing is ever deleted. Every change is written to cos_feature_event (append-only), with the old
and new values, so the history explains itself to someone who joins later.
"""
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import String, cast, exists, func, or_
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.notification import Notification, NotificationTypeEnum
from app.models.product_feature import (
    DEFAULT_PRODUCTS, FEATURE_SOURCES, FEATURE_STATUSES, AppProduct, ProductFeature, ProductFeatureAttachment, ProductFeatureCost,
    ProductFeatureEvent, ProductFeatureMeToo, ProductFeatureProduct, ProductFeatureSolution, ProductFeatureSolutionVote, ProductFeatureTask,
)
from app.models.department_target import TargetDepartment, target_department_members
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


async def hod_of_author(db: AsyncSession, user: User, f: ProductFeature) -> bool:
    """True if `user` heads a department the feature's author belongs to (or heads)."""
    if f.created_by_id == user.id:
        return bool((await db.execute(select(TargetDepartment.id).filter(
            TargetDepartment.hod_id == user.id, TargetDepartment.is_deleted == False))).first())
    member_of = select(target_department_members.c.department_id).where(target_department_members.c.user_id == f.created_by_id)
    q = select(TargetDepartment.id).filter(TargetDepartment.is_deleted == False, TargetDepartment.hod_id == user.id,
                                           or_(TargetDepartment.id.in_(member_of), TargetDepartment.hod_id == f.created_by_id))
    return bool((await db.execute(q)).first())


async def can_decide(db: AsyncSession, user: User, f: ProductFeature) -> bool:
    return is_director(user) or await hod_of_author(db, user, f)


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


# ----------------------------------------------------------------- products, money

async def all_products(db: AsyncSession) -> List[Dict[str, Any]]:
    rows = (await db.execute(select(AppProduct).filter(AppProduct.is_deleted == False).order_by(AppProduct.sort_order, AppProduct.id))).scalars().all()
    if not rows:   # first use: the products the company builds today
        for i, n in enumerate(DEFAULT_PRODUCTS):
            db.add(AppProduct(name=n, sort_order=i))
        await db.flush()
        rows = (await db.execute(select(AppProduct).filter(AppProduct.is_deleted == False).order_by(AppProduct.sort_order, AppProduct.id))).scalars().all()
    return [{"id": p.id, "name": p.name} for p in rows]


async def products_of(db: AsyncSession, ids: List[int]) -> Dict[int, List[Dict[str, Any]]]:
    if not ids:
        return {}
    out: Dict[int, List[Dict[str, Any]]] = {}
    for fid, pid, name in (await db.execute(
        select(ProductFeatureProduct.feature_id, AppProduct.id, AppProduct.name)
        .join(AppProduct, AppProduct.id == ProductFeatureProduct.product_id)
        .filter(ProductFeatureProduct.feature_id.in_(ids), AppProduct.is_deleted == False)
        .order_by(AppProduct.sort_order, AppProduct.id))).all():
        out.setdefault(fid, []).append({"id": pid, "name": name})
    return out


def money_totals(lines) -> Optional[Dict[str, Any]]:
    """Cost and profit in plain numbers: what it takes once and each month, what it brings each
    month, how many months until it pays for itself, and the result after a year."""
    if not lines:
        return None
    get = lambda side, kind: sum(float(c.amount) for c in lines if c.side == side and c.kind == kind)
    once_cost, month_cost = get("cost", "one_time"), get("cost", "monthly")
    once_gain, month_gain = get("benefit", "one_time"), get("benefit", "monthly")
    net_month = month_gain - month_cost
    upfront = once_cost - once_gain
    if upfront <= 0:
        payback = 0.0 if net_month >= 0 else None
    else:
        payback = round(upfront / net_month, 1) if net_month > 0 else None
    year = once_gain - once_cost + 12 * net_month
    spent_year = once_cost + 12 * month_cost
    return {"one_time_cost": once_cost, "monthly_cost": month_cost, "one_time_benefit": once_gain, "monthly_benefit": month_gain,
            "net_monthly": net_month, "payback_months": payback, "net_first_year": year,
            "return_first_year": round(year / spent_year * 100) if spent_year else None,
            "has_benefit": any(c.side == "benefit" for c in lines)}


async def cost_totals_of(db: AsyncSession, ids: List[int]) -> Dict[int, Optional[Dict[str, Any]]]:
    if not ids:
        return {}
    by: Dict[int, list] = {}
    for c in (await db.execute(select(ProductFeatureCost).filter(ProductFeatureCost.feature_id.in_(ids), ProductFeatureCost.is_deleted == False))).scalars().all():
        by.setdefault(c.feature_id, []).append(c)
    return {fid: money_totals(v) for fid, v in by.items()}


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
    solution_id: Optional[int] = None   # a reply under one idea


class SolutionIn(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)


class ChooseIn(BaseModel):
    note: Optional[str] = Field(None, max_length=5000)


class TaskLinkIn(BaseModel):
    task_id: int


class ProductIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=120)


class FeatureProductsIn(BaseModel):
    product_ids: List[int] = Field(default_factory=list, max_length=50)


class CostLineIn(BaseModel):
    side: str = Field(..., pattern="^(cost|benefit)$")
    label: str = Field(..., min_length=1, max_length=200)
    kind: str = Field("one_time", pattern="^(one_time|monthly)$")
    amount: Optional[float] = Field(None, ge=0, le=1e12)
    days: Optional[float] = Field(None, ge=0, le=10000)
    rate: Optional[float] = Field(None, ge=0, le=1e9)
    note: Optional[str] = Field(None, max_length=300)


class CostsIn(BaseModel):
    lines: List[CostLineIn] = Field(default_factory=list, max_length=100)


# ----------------------------------------------------------------- list + search

@router.get("", response_model=APIResponse)
async def list_features(
    q: Optional[str] = Query(None, max_length=200),
    status: Optional[str] = None,
    area: Optional[str] = None,
    product: Optional[int] = None,
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
    if product:
        base = base.filter(exists().where(ProductFeatureProduct.feature_id == ProductFeature.id, ProductFeatureProduct.product_id == product))
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

    tags = await products_of(db, ids)
    money = await cost_totals_of(db, ids)
    out = []
    for f in rows:
        row = summary(f, who, tasks.get(f.id, []), tally.get(f.id, {}).get("me_too", 0),
                      tally.get(f.id, {}).get("comment", 0), f.id in mine_too,
                      tally.get(f.id, {}).get("solutions", 0), chosen_of(f))
        row["products"] = tags.get(f.id, [])
        row["money"] = money.get(f.id)
        out.append(row)
    return success_response(data={
        "features": out,
        "counts": counts, "total": len(everything), "areas": areas,
        "products": await all_products(db),
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
        "permissions": {"can_edit": can_edit(user, f), "can_decide": await can_decide(db, user, f), "can_link": True,
                        "can_set_status": is_director(user)},
    })
    # replies under an idea are comments that name it
    replies: Dict[int, List[Dict[str, Any]]] = {}
    for e in out["events"]:
        if e["kind"] == "comment" and (e.get("data") or {}).get("solution_id"):
            replies.setdefault(e["data"]["solution_id"], []).append(e)
    for x in out["solutions_list"]:
        x["replies"] = replies.get(x["id"], [])
    out["products"] = (await products_of(db, [f.id])).get(f.id, [])
    out["all_products"] = await all_products(db)
    atts = (await db.execute(select(ProductFeatureAttachment).filter(
        ProductFeatureAttachment.feature_id == f.id, ProductFeatureAttachment.is_deleted == False).order_by(ProductFeatureAttachment.id))).scalars().all()
    upl = await people(db, [a.uploaded_by_id for a in atts])
    out["attachments"] = [{"id": a.id, "filename": a.filename, "content_type": a.content_type, "size": a.size, "solution_id": a.solution_id,
                           "is_image": a.content_type.startswith("image/"), "by": upl.get(a.uploaded_by_id), "created_at": _iso(a.created_at),
                           "can_remove": is_director(user) or a.uploaded_by_id == user.id} for a in atts]
    lines = (await db.execute(select(ProductFeatureCost).filter(ProductFeatureCost.feature_id == f.id, ProductFeatureCost.is_deleted == False)
                              .order_by(ProductFeatureCost.sort_order, ProductFeatureCost.id))).scalars().all()
    out["costs"] = {"lines": [{"id": c.id, "side": c.side, "label": c.label, "kind": c.kind, "amount": float(c.amount),
                               "days": float(c.days) if c.days is not None else None, "rate": float(c.rate) if c.rate is not None else None,
                               "note": c.note} for c in lines],
                    "totals": money_totals(lines)}
    out["permissions"]["can_cost"] = out["permissions"]["can_decide"] or f.created_by_id == user.id
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


@router.get("/products", response_model=APIResponse)
async def list_products(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    out = await all_products(db)
    await db.commit()
    return success_response(data=out)


async def can_manage_products(db: AsyncSession, user: User) -> bool:
    if is_director(user):
        return True
    return bool((await db.execute(select(TargetDepartment.id).filter(TargetDepartment.hod_id == user.id, TargetDepartment.is_deleted == False))).first())


@router.post("/products", response_model=APIResponse)
async def add_product(body: ProductIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    if not await can_manage_products(db, current_user):
        raise HTTPException(status_code=403, detail="The Director or an HOD adds products.")
    name = _clean(body.name, 120)
    existing = await all_products(db)
    if any(p["name"].lower() == (name or "").lower() for p in existing):
        raise HTTPException(status_code=409, detail="That product is already on the list.")
    db.add(AppProduct(name=name, sort_order=len(existing)))
    await db.commit()
    return success_response(data=await all_products(db), message="Product added")


@router.put("/products/{pid}", response_model=APIResponse)
async def rename_product(pid: int, body: ProductIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    if not await can_manage_products(db, current_user):
        raise HTTPException(status_code=403, detail="The Director or an HOD renames products.")
    p = (await db.execute(select(AppProduct).filter(AppProduct.id == pid, AppProduct.is_deleted == False))).scalars().first()
    if not p:
        raise HTTPException(status_code=404, detail="Product not found.")
    p.name = _clean(body.name, 120)
    await db.commit()
    return success_response(data=await all_products(db), message="Product renamed")


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
    if body.solution_id:
        sol = await get_solution(db, f, body.solution_id)
        log(db, f, current_user, "comment", text=text, data={"solution_id": sol.id})
        if sol.created_by_id != current_user.id:
            notify(db, sol.created_by_id, current_user, "Reply to your idea", f"{code(f.id)} · {text[:140]}", f.id)
    else:
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
    """The Director, or the author's HOD, picks the solution to build. Its author is credited and told.
    The Director can change an HOD's pick; the event log keeps both."""
    f = await get_feature(db, fid)
    if not await can_decide(db, current_user, f):
        raise HTTPException(status_code=403, detail="Only the Director or this person's HOD picks the solution.")
    s = await get_solution(db, f, sid)
    if f.chosen_solution_id == s.id:
        return success_response(data=await detail(db, f, current_user), message="Already chosen")
    author = (await people(db, [s.created_by_id])).get(s.created_by_id)
    previous = f.chosen_solution_id
    f.chosen_solution_id = s.id
    log(db, f, current_user, "solution_chosen", text=_clean(body.note),
        data={"solution_id": s.id, "by": author["name"] if author else None, "solution": s.text[:300], "replaced": previous,
              "as": "Director" if is_director(current_user) else "HOD"})
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


# ----------------------------------------------------------------- products on a feature

@router.put("/{fid}/products", response_model=APIResponse)
async def set_products(fid: int, body: FeatureProductsIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    f = await get_feature(db, fid)
    if not (can_edit(current_user, f) or await hod_of_author(db, current_user, f)):
        raise HTTPException(status_code=403, detail="The author, their HOD or the Director tags products.")
    valid = {p["id"]: p["name"] for p in await all_products(db)}
    want = [i for i in dict.fromkeys(body.product_ids) if i in valid]
    have = (await db.execute(select(ProductFeatureProduct).filter(ProductFeatureProduct.feature_id == f.id))).scalars().all()
    before = sorted(valid.get(x.product_id, "?") for x in have)
    for x in have:
        if x.product_id not in want:
            await db.delete(x)
    for i in want:
        if all(x.product_id != i for x in have):
            db.add(ProductFeatureProduct(feature_id=f.id, product_id=i))
    after = sorted(valid[i] for i in want)
    if before != after:
        log(db, f, current_user, "edited", data={"field": "products", "old": ", ".join(before) or None, "new": ", ".join(after) or None})
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Products saved")


# ----------------------------------------------------------------- screenshots and documents

MAX_ATTACHMENT = 10 * 1024 * 1024
ALLOWED_TYPES = ("image/", "application/pdf", "text/plain", "text/csv",
                 "application/vnd.openxmlformats-officedocument", "application/msword", "application/vnd.ms-excel")


@router.post("/{fid}/attachments", response_model=APIResponse)
async def add_attachment(
    fid: int, file: UploadFile = File(...), solution_id: Optional[int] = Form(None),
    db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
):
    from app.services.s3 import upload_file_to_s3
    import io
    f = await get_feature(db, fid)
    if solution_id:
        await get_solution(db, f, solution_id)
    ctype = (file.content_type or "application/octet-stream").lower()
    if not any(ctype.startswith(t) for t in ALLOWED_TYPES):
        raise HTTPException(status_code=415, detail="Attach a screenshot, a PDF, a Word or Excel file, or a text file.")
    raw = await file.read(MAX_ATTACHMENT + 1)
    if len(raw) > MAX_ATTACHMENT:
        raise HTTPException(status_code=413, detail="That file is over 10 MB.")
    if not raw:
        raise HTTPException(status_code=422, detail="That file is empty.")
    name = (file.filename or "attachment").replace("/", "_").replace("\\", "_")[-200:]
    key = upload_file_to_s3(io.BytesIO(raw), f"feature-{f.id}-{name}", ctype)   # flat name: the local fallback writes a plain file
    a = ProductFeatureAttachment(feature_id=f.id, solution_id=solution_id, filename=name, content_type=ctype, size=len(raw),
                                 storage_key=key, uploaded_by_id=current_user.id)
    db.add(a)
    await db.flush()
    log(db, f, current_user, "attachment", text=name, data={"attachment_id": a.id, "solution_id": solution_id})
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Attached")


@router.get("/{fid}/attachments/{aid}/file")
async def attachment_file(fid: int, aid: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    """The file itself, streamed through the API so it needs the same sign-in as the page."""
    import os
    from app.services.s3 import UPLOAD_DIR, get_s3_client
    from app.core.config import settings
    a = (await db.execute(select(ProductFeatureAttachment).filter(
        ProductFeatureAttachment.id == aid, ProductFeatureAttachment.feature_id == fid, ProductFeatureAttachment.is_deleted == False))).scalars().first()
    if not a:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if a.storage_key.startswith("local/"):
        path = os.path.join(UPLOAD_DIR, a.storage_key[len("local/"):])
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail="The file is missing on the server.")
        with open(path, "rb") as fh:
            body = fh.read()
    else:
        client = get_s3_client()
        if not client:
            raise HTTPException(status_code=503, detail="File storage is not configured.")
        body = client.get_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=a.storage_key)["Body"].read()
    disp = "inline" if a.content_type.startswith("image/") or a.content_type == "application/pdf" else "attachment"
    safe = a.filename.replace('"', "")
    return Response(content=body, media_type=a.content_type, headers={"Content-Disposition": f'{disp}; filename="{safe}"',
                                                                      "Cache-Control": "private, max-age=3600"})


@router.delete("/{fid}/attachments/{aid}", response_model=APIResponse)
async def remove_attachment(fid: int, aid: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    f = await get_feature(db, fid)
    a = (await db.execute(select(ProductFeatureAttachment).filter(
        ProductFeatureAttachment.id == aid, ProductFeatureAttachment.feature_id == fid, ProductFeatureAttachment.is_deleted == False))).scalars().first()
    if not a:
        raise HTTPException(status_code=404, detail="Attachment not found.")
    if not (is_director(current_user) or a.uploaded_by_id == current_user.id):
        raise HTTPException(status_code=403, detail="Only whoever attached it, or the Director, can remove it.")
    a.is_deleted = True   # the file and its history stay; it just stops showing
    log(db, f, current_user, "attachment_removed", text=a.filename, data={"attachment_id": a.id})
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Removed")


# ----------------------------------------------------------------- cost and profit

@router.put("/{fid}/costs", response_model=APIResponse)
async def set_costs(fid: int, body: CostsIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)):
    """Replace the estimate. People's time can be days x rate per day; the amount is then worked out."""
    f = await get_feature(db, fid)
    if not (f.created_by_id == current_user.id or await can_decide(db, current_user, f)):
        raise HTTPException(status_code=403, detail="The author, their HOD or the Director keeps the estimate.")
    before = money_totals((await db.execute(select(ProductFeatureCost).filter(
        ProductFeatureCost.feature_id == f.id, ProductFeatureCost.is_deleted == False))).scalars().all())
    for c in (await db.execute(select(ProductFeatureCost).filter(ProductFeatureCost.feature_id == f.id))).scalars().all():
        await db.delete(c)
    new_rows = []
    for i, line in enumerate(body.lines):
        amount = line.amount
        if line.days is not None and line.rate is not None:
            amount = round(line.days * line.rate, 2)
        if amount is None:
            raise HTTPException(status_code=422, detail=f"“{line.label}” needs an amount, or days and a rate.")
        row = ProductFeatureCost(feature_id=f.id, side=line.side, label=_clean(line.label, 200), kind=line.kind, amount=amount,
                                 days=line.days, rate=line.rate, note=_clean(line.note, 300), sort_order=i)
        db.add(row)
        new_rows.append(row)
    await db.flush()
    after = money_totals(new_rows)
    log(db, f, current_user, "costs", data={"before": before, "after": after})
    await db.commit()
    return success_response(data=await detail(db, f, current_user), message="Estimate saved")
