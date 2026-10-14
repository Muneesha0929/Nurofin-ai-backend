"""Department targets: the Director writes one message per department per quarter,
the HOD turns it into objectives and sub-objectives.

Who can do what:
- Director (ceo / super_admin): create and edit departments, write company headline and
  department messages, mark the quarter result.
- HOD of a department: read the message, add / edit / delete that department's objectives.
- Owner of an objective: update its status and progress.
- Everyone signed in: read.
"""
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.department_target import (
    CompanyQuarterGoal,
    DepartmentObjective,
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


def can_plan(user: User, dept: TargetDepartment) -> bool:
    return is_director(user) or (dept.hod_id is not None and dept.hod_id == user.id)


def leaf_progress(o: DepartmentObjective) -> float:
    return 100.0 if o.status == "done" else float(o.progress or 0)


def build_tree(objs: List[DepartmentObjective], today: str) -> List[Dict[str, Any]]:
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

    return [node(o) for o in children.get(None, [])]


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
        departments.append({**dept_payload(d), **target_payload(t), **stats, "new_for_me": unread})

    return success_response(data={
        "quarter": quarter_info(fy, q),
        "headline": goal.headline if goal else None,
        "progress": round(sum(progress_values) / len(progress_values), 1) if progress_values else None,
        "totals": totals,
        "departments": departments,
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
    return success_response(data=[dept_payload(d) for d in depts], message="Departments fetched")


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


@router.get("/departments/{dept_id}", response_model=APIResponse)
async def department_detail(
    dept_id: int,
    fiscal_year: Optional[int] = Query(None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """One department with all four quarters of a financial year (default: the current FY)."""
    dept = await get_department(db, dept_id)
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
