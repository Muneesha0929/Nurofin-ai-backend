"""Department targets: the Director writes one message per department per quarter,
the HOD turns it into objectives and sub-objectives.

Who can do what:
- Director (ceo / super_admin): create and edit departments, write company headline and
  department messages, mark the quarter result.
- HOD of a department: read the message, add / edit / delete that department's objectives.
- Owner of an objective: update its status and progress.
- Director, HOD, owner and department members: post updates on an objective.
- HOD and members: read their own departments. Other departments are not visible to them.
- Everyone signed in: the company line, the overall progress, company and monthly targets.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import selectinload

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.department_target import (
    CompanyQuarterGoal,
    CompanyWidget,
    DepartmentObjective,
    MonthlyTarget,
    DepartmentObjectiveUpdate,
    DepartmentQuarterTarget,
    TargetDepartment,
)
from app.models.notification import Notification, NotificationTypeEnum
from app.models.user import User
from app.schemas.department_target import (
    CompanyGoalUpdate,
    DepartmentCreate,
    DepartmentUpdate,
    ObjectiveCreate,
    ObjectiveUpdate,
    MonthlyTargetCreate,
    MonthlyTargetUpdate,
    ObjectiveUpdatePost,
    WidgetCreate,
    WidgetOrder,
    WidgetUpdate,
    QuarterTargetUpdate,
)

router = APIRouter()

DIRECTOR_ROLES = ("ceo", "super_admin")
OUTCOMES = {"achieved": "Achieved", "met_partly": "Met partly", "not_met": "Not met"}
QUARTER_MONTHS = {1: ("Apr", "Jun"), 2: ("Jul", "Sep"), 3: ("Oct", "Dec"), 4: ("Jan", "Mar")}


# ----------------------------------------------------------------- helpers

def _role(user: User) -> str:
    role = getattr(user, "role", "") or ""
    return str(role.value if hasattr(role, "value") else role).lower()


def is_director(user: User) -> bool:
    return _role(user) in DIRECTOR_ROLES


def require_director(user: User) -> None:
    if not is_director(user):
        raise HTTPException(status_code=403, detail="Only the Director can do this.")


def current_quarter(today: Optional[date] = None) -> tuple[int, int]:
    today = today or date.today()
    if today.month >= 4:
        return today.year, (today.month - 4) // 3 + 1
    return today.year - 1, 4


def quarter_info(fy: int, q: int) -> Dict[str, Any]:
    start_year = fy if q < 4 else fy + 1
    start_month = {1: 4, 2: 7, 3: 10, 4: 1}[q]
    end_month = start_month + 2
    end_day = 30 if end_month in (6, 9) else 31
    first, last = QUARTER_MONTHS[q]
    cur = current_quarter()
    status = "current" if (fy, q) == cur else ("past" if (fy, q) < cur else "upcoming")
    return {
        "fiscal_year": fy,
        "quarter_number": q,
        "label": f"Q{q} FY {fy}-{str(fy + 1)[-2:]}",
        "short": f"Q{q}",
        "range": f"{first} – {last} {start_year}",
        "start": f"{start_year}-{start_month:02d}-01",
        "end": f"{start_year}-{end_month:02d}-{end_day}",
        "status": status,
    }


def check_quarter(fy: int, q: int) -> None:
    if q not in (1, 2, 3, 4) or not (2000 <= fy <= 2100):
        raise HTTPException(status_code=400, detail="Unknown quarter.")


def user_brief(u: Optional[User]) -> Optional[Dict[str, Any]]:
    if not u:
        return None
    return {"id": u.id, "name": u.full_name or u.email, "avatar": u.profile_picture}


async def get_department(db: AsyncSession, dept_id: int) -> TargetDepartment:
    res = await db.execute(
        select(TargetDepartment)
        .options(selectinload(TargetDepartment.hod), selectinload(TargetDepartment.members))
        .filter(TargetDepartment.id == dept_id, TargetDepartment.is_deleted == False)
    )
    dept = res.scalars().first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    return dept


async def load_users(db: AsyncSession, ids: List[int]) -> List[User]:
    ids = list(dict.fromkeys(i for i in ids if i))
    if not ids:
        return []
    res = await db.execute(select(User).filter(User.id.in_(ids), User.is_deleted == False))
    users = res.scalars().all()
    if len(users) != len(ids):
        raise HTTPException(status_code=400, detail="One or more people were not found.")
    return list(users)


def can_see(user: User, dept: TargetDepartment) -> bool:
    """Departments are private: the Director sees all, everyone else only the ones they head or belong to."""
    return is_director(user) or dept.hod_id == user.id or any(m.id == user.id for m in dept.members)


def require_see(user: User, dept: TargetDepartment) -> None:
    if not can_see(user, dept):
        raise HTTPException(status_code=403, detail="You are not part of this department.")


def can_plan(user: User, dept: TargetDepartment) -> bool:
    return is_director(user) or (dept.hod_id is not None and dept.hod_id == user.id)


def leaf_progress(o: DepartmentObjective) -> float:
    return 100.0 if o.status == "done" else float(o.progress or 0)


def build_tree(objs: List[DepartmentObjective], today: str, root_ids: Optional[List[int]] = None) -> List[Dict[str, Any]]:
    """Objectives with their sub-objectives; a parent's progress is the average of its children."""
    children: Dict[Optional[int], List[DepartmentObjective]] = {}
    for o in objs:
        children.setdefault(o.parent_id, []).append(o)
    for lst in children.values():
        lst.sort(key=lambda x: (x.sort_order or 0, x.id))

    def node(o: DepartmentObjective) -> Dict[str, Any]:
        kids = [node(k) for k in children.get(o.id, [])]
        progress = round(sum(k["progress"] for k in kids) / len(kids), 1) if kids else leaf_progress(o)
        done = progress >= 100
        return {
            "id": o.id,
            "parent_id": o.parent_id,
            "title": o.title,
            "description": o.description,
            "owner": user_brief(o.owner),
            "deadline": o.deadline,
            # a parent's status follows its children; a leaf keeps what its owner set
            "status": "done" if done else ("in_progress" if kids and progress > 0 else ("not_started" if kids else (o.status or "not_started"))),
            "progress": progress,
            "overdue": bool(o.deadline and o.deadline < today and not done),
            "children": kids,
        }

    roots = [node(o) for o in objs if o.id in root_ids] if root_ids is not None else [node(o) for o in children.get(None, [])]
    for r in roots:
        r["health"] = health(r)
    return roots


def health(n: Dict[str, Any]) -> str:
    """Like the design: done; off track if an overdue item is under 50%; at risk if anything is overdue; else on track."""
    if n["progress"] >= 100:
        return "done"
    late: List[Dict[str, Any]] = []

    def walk(x):
        if not x["children"] and x["overdue"]:
            late.append(x)
        for c in x["children"]:
            walk(c)

    walk(n)
    if any(x["progress"] < 50 for x in late):
        return "off"
    return "risk" if late else "on"


def summarize(tree: List[Dict[str, Any]]) -> Dict[str, Any]:
    flat: List[Dict[str, Any]] = []

    def walk(nodes):
        for n in nodes:
            flat.append(n)
            walk(n["children"])

    walk(tree)
    return {
        "progress": round(sum(n["progress"] for n in tree) / len(tree), 1) if tree else None,
        "objectives": len(tree),
        "items": len(flat),
        "done": sum(1 for n in flat if n["status"] == "done"),
        "overdue": sum(1 for n in flat if n["overdue"] and not n["children"]),
        "health": {k: sum(1 for n in tree if n["health"] == k) for k in ("on", "risk", "off", "done")},
    }


def target_payload(t: Optional[DepartmentQuarterTarget]) -> Dict[str, Any]:
    return {
        "message": t.message if t else None,
        "message_updated_at": t.message_updated_at.isoformat() if t and t.message_updated_at else None,
        "outcome": t.outcome if t else None,
        "outcome_label": OUTCOMES.get(t.outcome) if t and t.outcome else None,
        "outcome_note": t.outcome_note if t else None,
    }


def is_unread_for_hod(t: Optional[DepartmentQuarterTarget]) -> bool:
    return bool(t and t.message and t.message_updated_at and (t.hod_seen_at is None or t.hod_seen_at < t.message_updated_at))


def dept_payload(d: TargetDepartment) -> Dict[str, Any]:
    return {
        "id": d.id,
        "name": d.name,
        "hod": user_brief(d.hod),
        "members": [user_brief(m) for m in d.members if not m.is_deleted],
    }


async def objectives_for(db: AsyncSession, dept_ids: List[int], fy: int, q: Optional[int] = None) -> List[DepartmentObjective]:
    if not dept_ids:
        return []
    stmt = (
        select(DepartmentObjective)
        .options(selectinload(DepartmentObjective.owner))
        .filter(
            DepartmentObjective.department_id.in_(dept_ids),
            DepartmentObjective.fiscal_year == fy,
            DepartmentObjective.is_deleted == False,
        )
    )
    if q is not None:
        stmt = stmt.filter(DepartmentObjective.quarter_number == q)
    return list((await db.execute(stmt)).scalars().all())


# ----------------------------------------------------------------- overview

@router.get("/overview", response_model=APIResponse)
async def overview(
    fiscal_year: Optional[int] = Query(None),
    quarter: Optional[int] = Query(None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Everything the Overview needs for one quarter (default: the current one)."""
    fy, q = (fiscal_year, quarter) if fiscal_year and quarter else current_quarter()
    check_quarter(fy, q)
    today = date.today().isoformat()

    goal = (await db.execute(
        select(CompanyQuarterGoal).filter(CompanyQuarterGoal.fiscal_year == fy, CompanyQuarterGoal.quarter_number == q, CompanyQuarterGoal.is_deleted == False)
    )).scalars().first()

    depts = list((await db.execute(
        select(TargetDepartment)
        .options(selectinload(TargetDepartment.hod), selectinload(TargetDepartment.members))
        .filter(TargetDepartment.is_deleted == False)
        .order_by(TargetDepartment.name)
    )).scalars().all())
    ids = [d.id for d in depts]

    targets = {
        t.department_id: t
        for t in (await db.execute(
            select(DepartmentQuarterTarget).filter(
                DepartmentQuarterTarget.department_id.in_(ids),
                DepartmentQuarterTarget.fiscal_year == fy,
                DepartmentQuarterTarget.quarter_number == q,
                DepartmentQuarterTarget.is_deleted == False,
            )
        )).scalars().all()
    } if ids else {}

    objs = await objectives_for(db, ids, fy, q)
    by_dept: Dict[int, List[DepartmentObjective]] = {}
    for o in objs:
        by_dept.setdefault(o.department_id, []).append(o)

    departments, new_targets, totals = [], [], {"objectives": 0, "items": 0, "done": 0, "overdue": 0}
    progress_values = []
    for d in depts:
        t = targets.get(d.id)
        stats = summarize(build_tree(by_dept.get(d.id, []), today))
        for k in totals:
            totals[k] += stats[k]
        if stats["progress"] is not None:
            progress_values.append(stats["progress"])
        unread = d.hod_id == current_user.id and is_unread_for_hod(t)
        if unread:
            new_targets.append({"department_id": d.id, "department_name": d.name, "objectives": stats["objectives"]})
        # totals and progress above stay company-wide; the department cards are only for people in them
        if can_see(current_user, d):
            departments.append({**dept_payload(d), **target_payload(t), **stats, "new_for_me": unread})

    widgets = (await db.execute(
        select(CompanyWidget).filter(CompanyWidget.fiscal_year == fy, CompanyWidget.quarter_number == q, CompanyWidget.is_deleted == False)
        .order_by(CompanyWidget.sort_order, CompanyWidget.id)
    )).scalars().all()
    names = {d.id: d.name for d in depts}

    return success_response(data={
        "quarter": quarter_info(fy, q),
        "headline": goal.headline if goal else None,
        "widgets": [widget_payload(w, names) for w in widgets],
        "monthly_targets": [monthly_payload(m, names, current_user, depts) for m in await monthly_list(db)],
        "progress": round(sum(progress_values) / len(progress_values), 1) if progress_values else None,
        "totals": totals,
        "departments": departments,
        "department_count": len(depts),
        "new_targets_for_me": new_targets,
        "can_edit": is_director(current_user),
    }, message="Quarter overview fetched")


@router.put("/company/{fiscal_year}/{quarter}", response_model=APIResponse)
async def set_company_goal(
    fiscal_year: int,
    quarter: int,
    goal_in: CompanyGoalUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    require_director(current_user)
    check_quarter(fiscal_year, quarter)
    goal = (await db.execute(
        select(CompanyQuarterGoal).filter(CompanyQuarterGoal.fiscal_year == fiscal_year, CompanyQuarterGoal.quarter_number == quarter)
    )).scalars().first()
    if not goal:
        goal = CompanyQuarterGoal(fiscal_year=fiscal_year, quarter_number=quarter)
        db.add(goal)
    goal.headline = (goal_in.headline or "").strip() or None
    goal.updated_by_id = current_user.id
    goal.is_deleted = False
    await db.commit()
    return success_response(data={"headline": goal.headline}, message="Quarter target saved")


# ----------------------------------------------------------------- departments

@router.get("/departments", response_model=APIResponse)
async def list_departments(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    depts = (await db.execute(
        select(TargetDepartment)
        .options(selectinload(TargetDepartment.hod), selectinload(TargetDepartment.members))
        .filter(TargetDepartment.is_deleted == False)
        .order_by(TargetDepartment.name)
    )).scalars().all()
    return success_response(data=[dept_payload(d) for d in depts if can_see(current_user, d)], message="Departments fetched")


@router.post("/departments", response_model=APIResponse)
async def create_department(
    dept_in: DepartmentCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    require_director(current_user)
    name = dept_in.name.strip()
    clash = (await db.execute(
        select(TargetDepartment).filter(TargetDepartment.name.ilike(name), TargetDepartment.is_deleted == False)
    )).scalars().first()
    if clash:
        raise HTTPException(status_code=400, detail="A department with this name already exists.")
    hod = (await load_users(db, [dept_in.hod_id]))[0] if dept_in.hod_id else None
    members = await load_users(db, [i for i in dept_in.member_ids if i != dept_in.hod_id])
    dept = TargetDepartment(name=name, hod=hod, created_by_id=current_user.id, members=members)
    db.add(dept)
    await db.commit()
    dept = await get_department(db, dept.id)
    return success_response(data=dept_payload(dept), message="Department created")


@router.put("/departments/{dept_id}", response_model=APIResponse)
async def update_department(
    dept_id: int,
    dept_in: DepartmentUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    require_director(current_user)
    dept = await get_department(db, dept_id)
    fields = dept_in.model_fields_set
    if "name" in fields and dept_in.name is not None:
        name = dept_in.name.strip()
        clash = (await db.execute(
            select(TargetDepartment).filter(TargetDepartment.name.ilike(name), TargetDepartment.id != dept_id, TargetDepartment.is_deleted == False)
        )).scalars().first()
        if clash:
            raise HTTPException(status_code=400, detail="A department with this name already exists.")
        dept.name = name
    if "hod_id" in fields:
        # set the relationship, not just the id: the session keeps loaded objects after commit
        dept.hod = (await load_users(db, [dept_in.hod_id]))[0] if dept_in.hod_id else None
    if "member_ids" in fields and dept_in.member_ids is not None:
        hod_id = dept.hod.id if dept.hod else None
        dept.members = await load_users(db, [i for i in dept_in.member_ids if i != hod_id])
    await db.commit()
    dept = await get_department(db, dept_id)
    return success_response(data=dept_payload(dept), message="Department saved")


@router.delete("/departments/{dept_id}", response_model=APIResponse)
async def delete_department(
    dept_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    require_director(current_user)
    dept = await get_department(db, dept_id)
    dept.is_deleted = True
    await db.commit()
    return success_response(message="Department deleted")


@router.post("/departments/{dept_id}/restore", response_model=APIResponse)
async def restore_department(
    dept_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Undo a delete. Deleting only hides the department, so its targets and objectives come back too."""
    require_director(current_user)
    dept = (await db.execute(select(TargetDepartment).filter(TargetDepartment.id == dept_id))).scalars().first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found.")
    if dept.is_deleted:
        clash = (await db.execute(
            select(TargetDepartment).filter(TargetDepartment.name.ilike(dept.name), TargetDepartment.id != dept_id, TargetDepartment.is_deleted == False)
        )).scalars().first()
        if clash:
            raise HTTPException(status_code=400, detail="Another department now has this name. Rename it first, then undo.")
        dept.is_deleted = False
        await db.commit()
    dept = await get_department(db, dept_id)
    return success_response(data=dept_payload(dept), message="Department restored")


@router.get("/departments/{dept_id}", response_model=APIResponse)
async def department_detail(
    dept_id: int,
    fiscal_year: Optional[int] = Query(None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """One department with all four quarters of a financial year (default: the current FY)."""
    dept = await get_department(db, dept_id)
    require_see(current_user, dept)
    fy = fiscal_year or current_quarter()[0]
    check_quarter(fy, 1)
    today = date.today().isoformat()

    targets = {
        t.quarter_number: t
        for t in (await db.execute(
            select(DepartmentQuarterTarget).filter(
                DepartmentQuarterTarget.department_id == dept_id,
                DepartmentQuarterTarget.fiscal_year == fy,
                DepartmentQuarterTarget.is_deleted == False,
            )
        )).scalars().all()
    }
    objs = await objectives_for(db, [dept_id], fy)
    is_hod = dept.hod_id == current_user.id
    planner = can_plan(current_user, dept)

    quarters = []
    for q in (1, 2, 3, 4):
        t = targets.get(q)
        tree = build_tree([o for o in objs if o.quarter_number == q], today)
        quarters.append({
            **quarter_info(fy, q),
            **target_payload(t),
            **summarize(tree),
            "objectives_tree": tree,
            "new_for_me": is_hod and is_unread_for_hod(t),
        })

    return success_response(data={
        **dept_payload(dept),
        "fiscal_year": fy,
        "fy_label": f"FY {fy}-{str(fy + 1)[-2:]}",
        "current": dict(zip(("fiscal_year", "quarter_number"), current_quarter())),
        "quarters": quarters,
        "permissions": {
            "is_director": is_director(current_user),
            "is_hod": is_hod,
            "can_plan": planner,
        },
    }, message="Department fetched")


@router.put("/departments/{dept_id}/quarters/{fiscal_year}/{quarter}", response_model=APIResponse)
async def update_quarter_target(
    dept_id: int,
    fiscal_year: int,
    quarter: int,
    target_in: QuarterTargetUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Director writes the message and / or marks the quarter result."""
    require_director(current_user)
    check_quarter(fiscal_year, quarter)
    dept = await get_department(db, dept_id)
    t = (await db.execute(
        select(DepartmentQuarterTarget).filter(
            DepartmentQuarterTarget.department_id == dept_id,
            DepartmentQuarterTarget.fiscal_year == fiscal_year,
            DepartmentQuarterTarget.quarter_number == quarter,
        )
    )).scalars().first()
    if not t:
        t = DepartmentQuarterTarget(department_id=dept_id, fiscal_year=fiscal_year, quarter_number=quarter)
        db.add(t)
    t.is_deleted = False
    fields = target_in.model_fields_set
    now = datetime.utcnow()
    message_changed = False

    if "message" in fields:
        new_message = (target_in.message or "").strip() or None
        if new_message != t.message:
            t.message = new_message
            t.message_updated_at = now
            t.message_updated_by_id = current_user.id
            message_changed = bool(new_message)

    if "outcome" in fields:
        if target_in.outcome and target_in.outcome not in OUTCOMES:
            raise HTTPException(status_code=400, detail="Result must be achieved, met_partly or not_met.")
        t.outcome = target_in.outcome or None
        t.outcome_set_by_id = current_user.id if target_in.outcome else None
        t.outcome_set_at = now if target_in.outcome else None
    if "outcome_note" in fields:
        t.outcome_note = (target_in.outcome_note or "").strip() or None

    if message_changed and dept.hod_id and dept.hod_id != current_user.id:
        label = quarter_info(fiscal_year, quarter)["label"]
        db.add(Notification(
            title="New target from the Director",
            message=f"{dept.name} · {label}: read the target and set your objectives.",
            type=NotificationTypeEnum.project_update,
            user_id=dept.hod_id,
            link=f"/departments?id={dept.id}",
        ))

    await db.commit()
    return success_response(data=target_payload(t), message="Quarter target saved")


@router.post("/departments/{dept_id}/quarters/{fiscal_year}/{quarter}/seen", response_model=APIResponse)
async def mark_target_seen(
    dept_id: int,
    fiscal_year: int,
    quarter: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """The HOD has read the latest message; stop showing 'new target' on their dashboard."""
    dept = await get_department(db, dept_id)
    if dept.hod_id != current_user.id:
        return success_response(message="Nothing to mark")
    t = (await db.execute(
        select(DepartmentQuarterTarget).filter(
            DepartmentQuarterTarget.department_id == dept_id,
            DepartmentQuarterTarget.fiscal_year == fiscal_year,
            DepartmentQuarterTarget.quarter_number == quarter,
            DepartmentQuarterTarget.is_deleted == False,
        )
    )).scalars().first()
    if t:
        t.hod_seen_at = datetime.utcnow()
        await db.commit()
    return success_response(message="Marked as read")


# ----------------------------------------------------------------- objectives

async def get_objective(db: AsyncSession, obj_id: int) -> DepartmentObjective:
    o = (await db.execute(
        select(DepartmentObjective).filter(DepartmentObjective.id == obj_id, DepartmentObjective.is_deleted == False)
    )).scalars().first()
    if not o:
        raise HTTPException(status_code=404, detail="Objective not found.")
    return o


@router.post("/objectives", response_model=APIResponse)
async def create_objective(
    obj_in: ObjectiveCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    check_quarter(obj_in.fiscal_year, obj_in.quarter_number)
    dept = await get_department(db, obj_in.department_id)
    if not can_plan(current_user, dept):
        raise HTTPException(status_code=403, detail="Only the HOD of this department or the Director can add objectives.")
    if obj_in.parent_id:
        parent = await get_objective(db, obj_in.parent_id)
        if (parent.department_id, parent.fiscal_year, parent.quarter_number) != (dept.id, obj_in.fiscal_year, obj_in.quarter_number):
            raise HTTPException(status_code=400, detail="The parent objective belongs to another department or quarter.")
        if parent.parent_id:
            raise HTTPException(status_code=400, detail="Sub-objectives cannot have their own sub-objectives. Add tasks from the Task center instead.")
    if obj_in.owner_id:
        await load_users(db, [obj_in.owner_id])
    siblings = (await db.execute(
        select(DepartmentObjective).filter(
            DepartmentObjective.department_id == dept.id,
            DepartmentObjective.fiscal_year == obj_in.fiscal_year,
            DepartmentObjective.quarter_number == obj_in.quarter_number,
            DepartmentObjective.parent_id == obj_in.parent_id,
            DepartmentObjective.is_deleted == False,
        )
    )).scalars().all()
    o = DepartmentObjective(
        department_id=dept.id,
        fiscal_year=obj_in.fiscal_year,
        quarter_number=obj_in.quarter_number,
        parent_id=obj_in.parent_id,
        title=obj_in.title.strip(),
        description=(obj_in.description or "").strip() or None,
        owner_id=obj_in.owner_id,
        deadline=obj_in.deadline,
        status="not_started",
        progress=0.0,
        sort_order=len(siblings),
        created_by_id=current_user.id,
    )
    db.add(o)
    await db.flush()
    if obj_in.owner_id and obj_in.owner_id != current_user.id:
        db.add(Notification(
            title="New objective for you" if not obj_in.parent_id else "New sub-objective for you",
            message=f"{dept.name}: {o.title}",
            type=NotificationTypeEnum.task_assigned,
            user_id=obj_in.owner_id,
            link=f"/departments?id={dept.id}",
        ))
    await db.commit()
    return success_response(data={"id": o.id}, message="Objective added")


@router.put("/objectives/{obj_id}", response_model=APIResponse)
async def update_objective(
    obj_id: int,
    obj_in: ObjectiveUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    o = await get_objective(db, obj_id)
    dept = await get_department(db, o.department_id)
    planner = can_plan(current_user, dept)
    owner = o.owner_id == current_user.id
    fields = obj_in.model_fields_set
    if not planner and not (owner and fields <= {"status", "progress"}):
        raise HTTPException(status_code=403, detail="You can only update progress on objectives assigned to you.")

    if "title" in fields and obj_in.title is not None:
        o.title = obj_in.title.strip()
    if "description" in fields:
        o.description = (obj_in.description or "").strip() or None
    if "owner_id" in fields:
        if obj_in.owner_id:
            await load_users(db, [obj_in.owner_id])
        o.owner_id = obj_in.owner_id or None
    if "deadline" in fields:
        o.deadline = obj_in.deadline
    if "status" in fields and obj_in.status is not None:
        o.status = obj_in.status
        if obj_in.status == "done":
            o.progress = 100.0
    if "progress" in fields and obj_in.progress is not None:
        o.progress = obj_in.progress
        if obj_in.progress >= 100:
            o.status = "done"
        elif o.status == "done" or (o.status == "not_started" and obj_in.progress > 0):
            o.status = "in_progress"
    await db.commit()
    return success_response(data={"id": o.id}, message="Objective saved")


@router.delete("/objectives/{obj_id}", response_model=APIResponse)
async def delete_objective(
    obj_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    o = await get_objective(db, obj_id)
    dept = await get_department(db, o.department_id)
    if not can_plan(current_user, dept):
        raise HTTPException(status_code=403, detail="Only the HOD of this department or the Director can delete objectives.")
    o.is_deleted = True
    kids = (await db.execute(
        select(DepartmentObjective).filter(DepartmentObjective.parent_id == o.id, DepartmentObjective.is_deleted == False)
    )).scalars().all()
    for k in kids:
        k.is_deleted = True
    await db.commit()
    return success_response(message="Objective deleted")


@router.get("/objectives/{obj_id}", response_model=APIResponse)
async def objective_detail(
    obj_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """One objective for the side panel: where it sits, its sub-objectives and its updates."""
    o = await get_objective(db, obj_id)
    dept = await get_department(db, o.department_id)
    require_see(current_user, dept)
    today = date.today().isoformat()
    family = [x for x in await objectives_for(db, [dept.id], o.fiscal_year, o.quarter_number) if x.id == o.id or x.parent_id == o.id]
    node = build_tree(family, today, root_ids=[o.id])[0]
    parent = await get_objective(db, o.parent_id) if o.parent_id else None
    target = (await db.execute(
        select(DepartmentQuarterTarget).filter(
            DepartmentQuarterTarget.department_id == dept.id,
            DepartmentQuarterTarget.fiscal_year == o.fiscal_year,
            DepartmentQuarterTarget.quarter_number == o.quarter_number,
            DepartmentQuarterTarget.is_deleted == False,
        )
    )).scalars().first()
    updates = (await db.execute(
        select(DepartmentObjectiveUpdate)
        .options(selectinload(DepartmentObjectiveUpdate.user))
        .filter(DepartmentObjectiveUpdate.objective_id == o.id, DepartmentObjectiveUpdate.is_deleted == False)
        .order_by(DepartmentObjectiveUpdate.created_at)
    )).scalars().all()
    planner = can_plan(current_user, dept)
    return success_response(data={
        **node,
        "description": o.description,
        "level": "sub_objective" if o.parent_id else "objective",
        "department": {"id": dept.id, "name": dept.name, "hod": user_brief(dept.hod)},
        "quarter": quarter_info(o.fiscal_year, o.quarter_number),
        "director_message": target.message if target else None,
        "parent": {"id": parent.id, "title": parent.title} if parent else None,
        "updates": [
            {"id": u.id, "user": user_brief(u.user), "text": u.text, "created_at": u.created_at.isoformat() if u.created_at else None}
            for u in updates
        ],
        "permissions": {
            "can_plan": planner,
            "can_update_progress": not node["children"] and (planner or o.owner_id == current_user.id),
            "can_post": can_post_update(current_user, dept, o),
        },
    }, message="Objective fetched")


def can_post_update(user: User, dept: TargetDepartment, o: DepartmentObjective) -> bool:
    return can_plan(user, dept) or o.owner_id == user.id or any(m.id == user.id for m in dept.members)


@router.post("/objectives/{obj_id}/updates", response_model=APIResponse)
async def post_objective_update(
    obj_id: int,
    update_in: ObjectiveUpdatePost,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    o = await get_objective(db, obj_id)
    dept = await get_department(db, o.department_id)
    if not can_post_update(current_user, dept, o):
        raise HTTPException(status_code=403, detail="Only people in this department can post updates here.")
    u = DepartmentObjectiveUpdate(objective_id=o.id, user_id=current_user.id, text=update_in.text.strip())
    db.add(u)
    for uid in {o.owner_id, dept.hod_id} - {None, current_user.id}:
        db.add(Notification(
            title="New update on an objective",
            message=f"{current_user.full_name or current_user.email} on \"{o.title}\": {u.text[:120]}",
            type=NotificationTypeEnum.project_update,
            user_id=uid,
            link=f"/departments?id={dept.id}",
        ))
    await db.commit()
    return success_response(data={"id": u.id}, message="Update posted")


@router.get("/deadlines", response_model=APIResponse)
async def deadlines(
    start: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    end: str = Query(..., pattern=r"^\d{4}-\d{2}-\d{2}$"),
    everyone: bool = Query(False),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Objective deadlines between two dates for the dashboard calendar.

    Default: the caller's own items, plus every item in departments they head.
    `everyone=true` (Director only): all departments.
    """
    stmt = (
        select(DepartmentObjective)
        .options(selectinload(DepartmentObjective.owner))
        .join(TargetDepartment, TargetDepartment.id == DepartmentObjective.department_id)
        .filter(
            DepartmentObjective.is_deleted == False,
            TargetDepartment.is_deleted == False,
            DepartmentObjective.deadline >= start,
            DepartmentObjective.deadline <= end,
        )
    )
    if not (everyone and is_director(current_user)):
        hod_of = select(TargetDepartment.id).filter(TargetDepartment.hod_id == current_user.id, TargetDepartment.is_deleted == False)
        stmt = stmt.filter((DepartmentObjective.owner_id == current_user.id) | (DepartmentObjective.department_id.in_(hod_of)))
    objs = list((await db.execute(stmt.order_by(DepartmentObjective.deadline))).scalars().all())
    depts = {d.id: d.name for d in (await db.execute(select(TargetDepartment).filter(TargetDepartment.id.in_({o.department_id for o in objs} or {0})))).scalars().all()}
    # parents' progress depends on their children, which may fall outside the date range
    parent_ids = [o.id for o in objs if o.parent_id is None]
    kids: Dict[int, List[DepartmentObjective]] = {}
    if parent_ids:
        for k in (await db.execute(select(DepartmentObjective).filter(DepartmentObjective.parent_id.in_(parent_ids), DepartmentObjective.is_deleted == False))).scalars().all():
            kids.setdefault(k.parent_id, []).append(k)
    today = date.today().isoformat()
    items = []
    for o in objs:
        ks = kids.get(o.id, [])
        progress = round(sum(leaf_progress(k) for k in ks) / len(ks), 1) if ks else leaf_progress(o)
        done = progress >= 100
        items.append({
            "id": o.id,
            "title": o.title,
            "deadline": o.deadline,
            "level": "sub_objective" if o.parent_id else "objective",
            "department": {"id": o.department_id, "name": depts.get(o.department_id)},
            "owner": user_brief(o.owner),
            "progress": progress,
            "status": "done" if done else (o.status or "not_started"),
            "overdue": o.deadline < today and not done,
            "mine": o.owner_id == current_user.id,
        })
    return success_response(data=items, message="Deadlines fetched")


# ----------------------------------------------------------------- company target widgets

def widget_payload(w: CompanyWidget, dept_names: Dict[int, str]) -> Dict[str, Any]:
    span = (w.base or 0) - (w.target or 0) if w.lower_is_better else (w.target or 0) - (w.base or 0)
    moved = (w.base or 0) - (w.current or 0) if w.lower_is_better else (w.current or 0) - (w.base or 0)
    if w.kind == "milestone":
        progress = 100.0 if (w.current or 0) >= 1 else 0.0
    else:
        progress = max(0.0, min(100.0, moved / span * 100)) if span else (100.0 if moved >= 0 and w.current == w.target else 0.0)
    return {
        "id": w.id, "title": w.title, "kind": w.kind or "number", "unit": w.unit,
        "base": w.base or 0, "current": w.current or 0, "target": w.target or 0,
        "lower_is_better": bool(w.lower_is_better), "note": w.note, "progress": round(progress, 1),
        "department": {"id": w.department_id, "name": dept_names.get(w.department_id)} if w.department_id else None,
    }


async def get_widget(db: AsyncSession, wid: int) -> CompanyWidget:
    w = (await db.execute(select(CompanyWidget).filter(CompanyWidget.id == wid, CompanyWidget.is_deleted == False))).scalars().first()
    if not w:
        raise HTTPException(status_code=404, detail="Widget not found.")
    return w


async def dept_names_for(db: AsyncSession) -> Dict[int, str]:
    return {d.id: d.name for d in (await db.execute(select(TargetDepartment).filter(TargetDepartment.is_deleted == False))).scalars().all()}


@router.post("/widgets", response_model=APIResponse)
async def create_widget(w_in: WidgetCreate, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    check_quarter(w_in.fiscal_year, w_in.quarter_number)
    if w_in.department_id:
        await get_department(db, w_in.department_id)
    count = len((await db.execute(select(CompanyWidget.id).filter(
        CompanyWidget.fiscal_year == w_in.fiscal_year, CompanyWidget.quarter_number == w_in.quarter_number, CompanyWidget.is_deleted == False
    ))).all())
    w = CompanyWidget(**w_in.model_dump(), sort_order=count, updated_by_id=current_user.id)
    w.title = w.title.strip()
    db.add(w)
    await db.commit()
    return success_response(data=widget_payload(w, await dept_names_for(db)), message="Widget added")


@router.put("/widgets/order", response_model=APIResponse)
async def reorder_widgets(order: WidgetOrder, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    rows = {w.id: w for w in (await db.execute(select(CompanyWidget).filter(CompanyWidget.id.in_(order.ids or [0]), CompanyWidget.is_deleted == False))).scalars().all()}
    for i, wid in enumerate(order.ids):
        if wid in rows:
            rows[wid].sort_order = i
    await db.commit()
    return success_response(message="Order saved")


@router.put("/widgets/{wid}", response_model=APIResponse)
async def update_widget(wid: int, w_in: WidgetUpdate, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    w = await get_widget(db, wid)
    data = w_in.model_dump(exclude_unset=True)
    if data.get("department_id"):
        await get_department(db, data["department_id"])
    for k, v in data.items():
        setattr(w, k, v.strip() if k == "title" and isinstance(v, str) else v)
    w.updated_by_id = current_user.id
    await db.commit()
    return success_response(data=widget_payload(w, await dept_names_for(db)), message="Widget saved")


@router.delete("/widgets/{wid}", response_model=APIResponse)
async def delete_widget(wid: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    w = await get_widget(db, wid)
    w.is_deleted = True
    await db.commit()
    return success_response(message="Widget deleted")


# ----------------------------------------------------------------- self-check

@router.get("/self-check", response_model=APIResponse)
async def self_check(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    """Director only. Compares each table with the code, then tries a full write inside a
    transaction that is rolled back, so nothing is saved."""
    require_director(current_user)
    from app.db.base_class import Base
    from app.db.session import engine

    tables = ["cos_department", "cos_department_member", "cos_company_goal", "cos_quarter_target",
              "cos_objective", "cos_objective_update", "cos_company_widget", "cos_monthly_target"]
    report: Dict[str, Any] = {"tables": {}, "write_test": None}

    def inspect_tables(sync_conn):
        insp = sa_inspect(sync_conn)
        have = set(insp.get_table_names())
        out = {}
        for name in tables:
            model = Base.metadata.tables[name]
            if name not in have:
                out[name] = {"ok": False, "problem": "table missing"}
                continue
            cols = {c["name"]: c for c in insp.get_columns(name)}
            missing = [c.name for c in model.columns if c.name not in cols]
            extra_required = [n for n, c in cols.items() if n not in model.c and not c["nullable"] and c.get("default") is None]
            out[name] = {"ok": not missing and not extra_required, "missing_columns": missing, "unknown_required_columns": extra_required}
        return out

    async with engine.connect() as conn:
        report["tables"] = await conn.run_sync(inspect_tables)
    async with engine.connect() as conn:
        trans = await conn.begin()
        step = "start"
        try:
            md = Base.metadata.tables
            step = "department"
            dep_id = (await conn.execute(md["cos_department"].insert().values(name="__self_check__", is_deleted=False).returning(md["cos_department"].c.id))).scalar()
            step = "quarter target"
            await conn.execute(md["cos_quarter_target"].insert().values(department_id=dep_id, fiscal_year=2000, quarter_number=1, message="x", is_deleted=False))
            step = "objective"
            oid = (await conn.execute(md["cos_objective"].insert().values(department_id=dep_id, fiscal_year=2000, quarter_number=1, title="x", deadline="2000-04-30", status="not_started", progress=0, sort_order=0, is_deleted=False).returning(md["cos_objective"].c.id))).scalar()
            step = "sub-objective"
            await conn.execute(md["cos_objective"].insert().values(department_id=dep_id, fiscal_year=2000, quarter_number=1, parent_id=oid, title="y", is_deleted=False))
            step = "objective update"
            await conn.execute(md["cos_objective_update"].insert().values(objective_id=oid, user_id=current_user.id, text="x", is_deleted=False))
            step = "company widget"
            await conn.execute(md["cos_company_widget"].insert().values(fiscal_year=2000, quarter_number=1, title="x", kind="money", is_deleted=False))
            step = "monthly target"
            await conn.execute(md["cos_monthly_target"].insert().values(title="x", kind="number", target=1, current=0, due_date="2000-04-30", department_id=dep_id, is_deleted=False))
            report["write_test"] = {"ok": True}
        except Exception as e:
            report["write_test"] = {"ok": False, "failed_at": step, "error": f"{type(e).__name__}: {str(e).splitlines()[0][:300]}"}
        finally:
            await trans.rollback()

    report["ok"] = all(t["ok"] for t in report["tables"].values()) and report["write_test"]["ok"]
    return success_response(data=report, message="Self-check finished")


# ----------------------------------------------------------------- monthly targets (right side of the Overview)

async def monthly_list(db: AsyncSession) -> List[MonthlyTarget]:
    return list((await db.execute(
        select(MonthlyTarget).filter(MonthlyTarget.is_deleted == False).order_by(MonthlyTarget.due_date, MonthlyTarget.sort_order, MonthlyTarget.id)
    )).scalars().all())


def monthly_payload(m: MonthlyTarget, dept_names: Dict[int, str], user: User, depts: List[TargetDepartment]) -> Dict[str, Any]:
    if m.kind == "milestone":
        progress = 100.0 if (m.current or 0) >= 1 else 0.0
    else:
        progress = max(0.0, min(100.0, (m.current or 0) / m.target * 100)) if m.target else 0.0
    dept = next((d for d in depts if d.id == m.department_id), None)
    can_update = is_director(user) or bool(dept and dept.hod_id == user.id)
    return {
        "id": m.id, "title": m.title, "kind": m.kind or "number", "unit": m.unit,
        "target": m.target or 0, "current": m.current or 0, "due_date": m.due_date, "note": m.note,
        "progress": round(progress, 1),
        "overdue": m.due_date < date.today().isoformat() and progress < 100,
        "department": {"id": m.department_id, "name": dept_names.get(m.department_id)} if m.department_id else None,
        "can_update": can_update,
    }


async def all_depts(db: AsyncSession) -> List[TargetDepartment]:
    return list((await db.execute(select(TargetDepartment).filter(TargetDepartment.is_deleted == False))).scalars().all())


async def get_monthly(db: AsyncSession, mid: int) -> MonthlyTarget:
    m = (await db.execute(select(MonthlyTarget).filter(MonthlyTarget.id == mid, MonthlyTarget.is_deleted == False))).scalars().first()
    if not m:
        raise HTTPException(status_code=404, detail="Target not found.")
    return m


@router.get("/monthly-targets", response_model=APIResponse)
async def list_monthly_targets(db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    depts = await all_depts(db)
    names = {d.id: d.name for d in depts}
    return success_response(data=[monthly_payload(m, names, current_user, depts) for m in await monthly_list(db)], message="Monthly targets fetched")


@router.post("/monthly-targets", response_model=APIResponse)
async def create_monthly_target(m_in: MonthlyTargetCreate, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    if m_in.department_id:
        await get_department(db, m_in.department_id)
    m = MonthlyTarget(**m_in.model_dump(), updated_by_id=current_user.id)
    m.title = m.title.strip()
    db.add(m)
    await db.commit()
    depts = await all_depts(db)
    return success_response(data=monthly_payload(m, {d.id: d.name for d in depts}, current_user, depts), message="Target added")


@router.put("/monthly-targets/{mid}", response_model=APIResponse)
async def update_monthly_target(mid: int, m_in: MonthlyTargetUpdate, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    """The Director can change anything. The responsible department's HOD can update the current value only."""
    m = await get_monthly(db, mid)
    data = m_in.model_dump(exclude_unset=True)
    if not is_director(current_user):
        dept = await get_department(db, m.department_id) if m.department_id else None
        if not (dept and dept.hod_id == current_user.id and set(data) <= {"current"}):
            raise HTTPException(status_code=403, detail="Only the Director, or the HOD of the responsible department (current value only), can change this.")
    if data.get("department_id"):
        await get_department(db, data["department_id"])
    for k, v in data.items():
        setattr(m, k, v.strip() if k == "title" and isinstance(v, str) else v)
    m.updated_by_id = current_user.id
    await db.commit()
    depts = await all_depts(db)
    return success_response(data=monthly_payload(m, {d.id: d.name for d in depts}, current_user, depts), message="Target saved")


@router.delete("/monthly-targets/{mid}", response_model=APIResponse)
async def delete_monthly_target(mid: int, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    require_director(current_user)
    m = await get_monthly(db, mid)
    m.is_deleted = True
    await db.commit()
    return success_response(message="Target deleted")
