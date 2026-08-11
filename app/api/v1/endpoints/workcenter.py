from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy import func, or_, case, exists, select
from typing import Any, Optional, List
from datetime import datetime

from app.api import deps
from app.models.user import User
from app.models.task import Task, TaskStatusEnum, TaskPriorityEnum
from app.models.quarter import Quarter
from app.models.project import Project
from app.models.task_history import TaskHistory
from app.models.task_transfer import TaskTransfer, TransferStatusEnum
from app.models.notification import Notification, NotificationTypeEnum
from app.core.responses import success_response, error_response

router = APIRouter()


async def _serialize_task(db: AsyncSession, t: Task, load_subtasks: bool = True) -> dict:
    subtasks = []
    if load_subtasks and hasattr(t, "subtasks") and t.subtasks:
        for s in t.subtasks:
            subtasks.append(await _serialize_task(db, s, load_subtasks=False))
    assignee = (await db.execute(select(User).filter(User.id == t.assigned_to_id))).scalars().first() if t.assigned_to_id else None
    assigner = (await db.execute(select(User).filter(User.id == t.assigned_by_id))).scalars().first() if t.assigned_by_id else None
    reviewer = (await db.execute(select(User).filter(User.id == t.reviewer_id))).scalars().first() if t.reviewer_id else None
    project = None
    if t.project_id:
        from app.models.project import Project
        project = (await db.execute(select(Project).filter(Project.id == t.project_id))).scalars().first()
    return {
        "id": t.id, "title": t.title, "description": t.description,
        "status": t.status.value if hasattr(t.status, 'value') else (t.status or "todo"),
        "priority": t.priority.value if hasattr(t.priority, 'value') else (t.priority or "medium"),
        "deadline": t.deadline, "start_date": t.start_date,
        "estimated_hours": t.estimated_hours, "progress": t.progress or 0.0,
        "assigned_to_id": t.assigned_to_id,
        "assigned_to_name": assignee.full_name if assignee else None,
        "assigned_to_avatar": assignee.profile_picture if assignee else None,
        "assigned_by_id": t.assigned_by_id,
        "assigned_by_name": assigner.full_name if assigner else None,
        "reviewer_id": t.reviewer_id,
        "reviewer_name": reviewer.full_name if reviewer else None,
        "project_id": t.project_id,
        "project_name": project.name if project else None,
        "parent_id": t.parent_id, "quarter_id": t.quarter_id,
        "meeting_id": t.meeting_id, "subtasks": subtasks,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }


def _serialize_quarter(q: Quarter) -> dict:
    return {
        "id": q.id, "name": q.name, "label": q.label,
        "fiscal_year": q.fiscal_year, "quarter_number": q.quarter_number,
        "start_date": q.start_date, "end_date": q.end_date,
        "status": q.status.value if q.status else "planning", "goals": q.goals,
    }


async def _add_history(db: AsyncSession, task_id: int, action: str, desc: str = None,
                       old_val: str = None, new_val: str = None, user_id: int = None):
    db.add(TaskHistory(task_id=task_id, action=action, description=desc,
                       old_value=old_val, new_value=new_val, performed_by_id=user_id))


async def _recalculate_project_progress(db: AsyncSession, project_id: Optional[int]):
    if not project_id:
        return
    q_all = select(Task).where(Task.project_id == project_id, Task.is_deleted == False)
    all_tasks = (await db.execute(q_all)).scalars().all()
    total = len(all_tasks)
    completed = 0
    for t in all_tasks:
        status_val = t.status.value if hasattr(t.status, 'value') else (t.status or "todo")
        if status_val in ("completed", "done"):
            completed += 1
            
    progress = round((completed / total) * 100.0, 2) if total > 0 else 0.0
    
    q_proj = select(Project).where(Project.id == project_id)
    proj = (await db.execute(q_proj)).scalars().first()
    if proj:
        proj.progress = progress
        db.add(proj)
        await db.commit()


async def _recalculate_parent_progress(db: AsyncSession, parent_id: Optional[int]):
    if not parent_id:
        return
    q_sub = select(Task).where(Task.parent_id == parent_id, Task.is_deleted == False)
    subtasks = (await db.execute(q_sub)).scalars().all()
    total = len(subtasks)
    completed = 0
    for t in subtasks:
        status_val = t.status.value if hasattr(t.status, 'value') else (t.status or "todo")
        if status_val in ("completed", "done"):
            completed += 1
            
    progress = round((completed / total) * 100.0, 2) if total > 0 else 0.0
    
    q_parent = select(Task).where(Task.id == parent_id)
    parent = (await db.execute(q_parent)).scalars().first()
    if parent:
        parent.progress = progress
        db.add(parent)
        await db.commit()
        if parent.project_id:
            await _recalculate_project_progress(db, parent.project_id)


async def _get_user(db: AsyncSession, uid: int) -> Optional[User]:
    if not uid:
        return None
    return (await db.execute(select(User).filter(User.id == uid))).scalars().first()


async def _update_parent_task(db: AsyncSession, parent_id: int, user_id: int):
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == parent_id, Task.is_deleted == False))
    parent = r.scalars().first()
    if not parent:
        return
    subtasks_q = select(Task).filter(Task.parent_id == parent_id, Task.is_deleted == False)
    subtasks_res = await db.execute(subtasks_q)
    subtasks = subtasks_res.scalars().all()
    if not subtasks:
        return
    total = len(subtasks)
    completed = 0
    for s in subtasks:
        status_val = s.status.value if hasattr(s.status, 'value') else (s.status or "todo")
        if status_val == "completed":
            completed += 1
    parent.progress = round((completed / total) * 100.0, 2)
    
    subtask_statuses = [s.status.value if hasattr(s.status, 'value') else (s.status or "todo") for s in subtasks]
    if all(st == "completed" for st in subtask_statuses):
        new_status = "completed"
    elif all(st == "todo" for st in subtask_statuses):
        new_status = "todo"
    else:
        new_status = "in_progress"
        
    old_status = parent.status.value if hasattr(parent.status, 'value') else (parent.status or "todo")
    if old_status != new_status:
        parent.status = new_status
        await _add_history(db, parent_id, "status_changed", f"Status: {old_status} → {new_status} (auto-derived from subtasks)", old_val=old_status, new_val=new_status, user_id=user_id)
    db.add(parent)
    await db.flush()


# ─── TASKS ────────────────────────────────────────────────────────────────────

@router.get("")
async def read_tasks(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    quarter_id: Optional[int] = None, project_id: Optional[int] = None,
    status: Optional[str] = None, priority: Optional[str] = None,
    assignee_id: Optional[int] = None, search: Optional[str] = None,
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
) -> Any:
    q = select(Task).options(selectinload(Task.subtasks)).where(Task.is_deleted == False, Task.parent_id == None)

    if quarter_id:
        q = q.where(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))
    if project_id:
        q = q.where(Task.project_id == project_id)
    if status:
        q = q.where(Task.status == status)
    if priority:
        q = q.where(Task.priority == priority)
    if assignee_id:
        subtask_alias = aliased(Task)
        subtask_exists = exists().where(
            (subtask_alias.parent_id == Task.id) &
            (subtask_alias.is_deleted == False) &
            (subtask_alias.assigned_to_id == assignee_id)
        )
        q = q.where(or_(Task.assigned_to_id == assignee_id, subtask_exists))
    if search:
        q = q.where(or_(Task.title.ilike(f"%{search}%"), Task.description.ilike(f"%{search}%")))

    count_q = select(func.count()).select_from(Task).where(Task.is_deleted == False, Task.parent_id == None)

    if quarter_id:
        count_q = count_q.where(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))
    if project_id:
        count_q = count_q.where(Task.project_id == project_id)
    if status:
        count_q = count_q.where(Task.status == status)
    if priority:
        count_q = count_q.where(Task.priority == priority)
    if assignee_id:
        subtask_alias = aliased(Task)
        subtask_exists = exists().where(
            (subtask_alias.parent_id == Task.id) &
            (subtask_alias.is_deleted == False) &
            (subtask_alias.assigned_to_id == assignee_id)
        )
        count_q = count_q.where(or_(Task.assigned_to_id == assignee_id, subtask_exists))
    if search:
        count_q = count_q.where(or_(Task.title.ilike(f"%{search}%"), Task.description.ilike(f"%{search}%")))
    total = (await db.execute(count_q)).scalar() or 0
    result = await db.execute(q.order_by(Task.created_at.desc()).offset((page - 1) * page_size).limit(page_size))
    tasks = result.scalars().all()
    data = []
    for t in tasks:
        data.append(await _serialize_task(db, t))
    return success_response(data={"tasks": data, "total": total, "page": page, "page_size": page_size})


@router.get("/summary")
async def get_summary(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    quarter_id: Optional[int] = None,
) -> Any:
    base = [Task.is_deleted == False, Task.parent_id == None]

    if quarter_id:
        base.append(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))

    async def _count(extra=None):
        cond = base + (extra or [])
        r = await db.execute(select(func.count()).select_from(Task).where(*cond))
        return r.scalar() or 0

    total = await _count()
    in_progress = await _count([Task.status == TaskStatusEnum.in_progress])
    completed = await _count([Task.status == TaskStatusEnum.completed])
    blocked = await _count([Task.status == TaskStatusEnum.blocked])
    review = await _count([Task.status == TaskStatusEnum.review])
    todo = await _count([Task.status == TaskStatusEnum.todo])
    today = datetime.utcnow().strftime("%Y-%m-%d")
    overdue = await _count([Task.deadline != None, Task.deadline < today, Task.status != TaskStatusEnum.completed])
    qp = round((completed / total) * 100, 1) if total > 0 else 0.0

    return success_response(data={
        "totalTasks": total, "todo": todo, "inProgress": in_progress,
        "completed": completed, "overdue": overdue, "blocked": blocked,
        "review": review, "quarterProgress": qp,
    })


@router.get("/insights")
async def get_insights(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    quarter_id: Optional[int] = None,
) -> Any:
    base = [Task.is_deleted == False, Task.parent_id == None]
    if quarter_id:
        base.append(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))


    today = datetime.utcnow().strftime("%Y-%m-%d")
    deadline_q = select(Task).where(*base, Task.deadline != None, Task.deadline >= today, Task.status != TaskStatusEnum.completed).order_by(Task.deadline.asc()).limit(10)
    upcoming = (await db.execute(deadline_q)).scalars().all()

    upcoming_list = []
    for t in upcoming:
        u = await _get_user(db, t.assigned_to_id)
        days = (datetime.strptime(t.deadline, "%Y-%m-%d") - datetime.utcnow()).days if t.deadline else None
        upcoming_list.append({
            "id": t.id, "title": t.title, "deadline": t.deadline,
            "priority": t.priority.value if hasattr(t.priority, 'value') else (t.priority or "medium"),
            "assignee": u.full_name if u else None, "assignee_avatar": u.profile_picture if u else None,
            "days_remaining": days,
        })

    hist_q = select(TaskHistory).where(TaskHistory.is_deleted == False).order_by(TaskHistory.created_at.desc()).limit(15)
    hist_result = await db.execute(hist_q)
    recents = hist_result.scalars().all()
    activity = []
    for h in recents:
        task_r = await db.execute(select(Task).filter(Task.id == h.task_id))
        task = task_r.scalars().first()
        user = await _get_user(db, h.performed_by_id)
        activity.append({
            "id": h.id, "action": h.action, "description": h.description,
            "task_id": h.task_id, "task_title": task.title if task else None,
            "user_name": user.full_name if user else None,
            "user_avatar": user.profile_picture if user else None,
            "created_at": h.created_at.isoformat() if h.created_at else None,
        })

    perf_q = select(
        Task.assigned_to_id,
        func.count(Task.id).label("total"),
        func.count(case((Task.status == TaskStatusEnum.completed, 1))).label("completed"),
    ).where(Task.is_deleted == False, Task.assigned_to_id != None)
    if quarter_id:
        perf_q = perf_q.where(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))
    perf_q = perf_q.group_by(Task.assigned_to_id)
    perf_result = await db.execute(perf_q)
    performers = []
    for row in perf_result.all():
        user = await _get_user(db, row.assigned_to_id)
        if user:
            pct = round((row.completed / row.total) * 100, 1) if row.total > 0 else 0
            performers.append({
                "user_id": user.id, "name": user.full_name, "avatar": user.profile_picture,
                "total_tasks": row.total, "completed": row.completed, "performance_pct": pct,
            })
    performers.sort(key=lambda x: x["performance_pct"], reverse=True)

    return success_response(data={
        "upcomingDeadlines": upcoming_list, "recentActivity": activity, "topPerformers": performers[:10],
    })


async def _determine_quarter_id(db: AsyncSession, date_str: str) -> Optional[int]:
    if not date_str:
        return None
    if 'T' in date_str:
        date_str = date_str.split('T')[0]
    q = select(Quarter).filter(
        Quarter.is_deleted == False,
        Quarter.start_date <= date_str,
        Quarter.end_date >= date_str
    )
    result = await db.execute(q)
    quarter = result.scalars().first()
    return quarter.id if quarter else None


# ─── CREATE / UPDATE / DELETE ─────────────────────────────────────────────────

@router.post("")
async def create_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    title: str, description: str = None, priority: str = "medium",
    project_id: int = None, quarter_id: int = None, assigned_to_id: int = None,
    reviewer_id: int = None, parent_id: int = None, deadline: str = None,
    start_date: str = None, estimated_hours: float = None,
) -> Any:
    if not quarter_id:
        quarter_id = await _determine_quarter_id(db, deadline or start_date)
    task = Task(title=title, description=description, priority=priority,
                project_id=project_id, quarter_id=quarter_id, assigned_to_id=assigned_to_id,
                assigned_by_id=current_user.id, reviewer_id=reviewer_id, parent_id=parent_id,
                deadline=deadline, start_date=start_date, estimated_hours=estimated_hours)
    db.add(task)
    await db.flush()
    await _add_history(db, task.id, "created", "Task created", user_id=current_user.id)
    if assigned_to_id:
        await _add_history(db, task.id, "assigned", f"Assigned to user {assigned_to_id}", user_id=current_user.id)
        db.add(Notification(title="New task assigned", message=f"You have been assigned: {title}",
                           type=NotificationTypeEnum.task_assigned, user_id=assigned_to_id, link=f"/tasks?id={task.id}"))
    if parent_id:
        await _update_parent_task(db, parent_id, current_user.id)
        await _add_history(db, parent_id, "subtask_added", f"Subtask '{title}' added to Main Task", user_id=current_user.id)
    await db.commit()
    await _recalculate_project_progress(db, task.project_id)
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task.id))
    task = r.scalars().first()
    return success_response(data=await _serialize_task(db, task))


@router.put("/{task_id}")
async def update_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_id: int, title: str = None, description: str = None, status: str = None,
    priority: str = None, deadline: str = None, start_date: str = None,
    estimated_hours: float = None, assigned_to_id: int = None,
    reviewer_id: int = None, progress: float = None,
) -> Any:
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
    if title is not None:
        task.title = title
    if status is not None:
        old = task.status.value if task.status else None
        task.status = status
        await _add_history(db, task_id, "status_changed", f"Status: {old} → {status}", old_val=old, new_val=status, user_id=current_user.id)
    if priority is not None:
        task.priority = priority
    if deadline is not None:
        old_dl = task.deadline
        task.deadline = deadline
        await _add_history(db, task_id, "deadline_updated", f"Deadline: {old_dl} → {deadline}", old_val=old_dl, new_val=deadline, user_id=current_user.id)
    if start_date is not None:
        task.start_date = start_date
    if deadline is not None or start_date is not None:
        new_dl = deadline if deadline is not None else task.deadline
        new_sd = start_date if start_date is not None else task.start_date
        new_q_id = await _determine_quarter_id(db, new_dl or new_sd)
        if new_q_id:
            task.quarter_id = new_q_id
    if estimated_hours is not None:
        task.estimated_hours = estimated_hours
    if assigned_to_id is not None and assigned_to_id != task.assigned_to_id:
        old_a = task.assigned_to_id
        task.assigned_to_id = assigned_to_id
        await _add_history(db, task_id, "assigned", "Reassigned", old_val=str(old_a), new_val=str(assigned_to_id), user_id=current_user.id)
        if assigned_to_id:
            db.add(Notification(title="Task assigned", message=f"You have been assigned: {task.title}",
                               type=NotificationTypeEnum.task_assigned, user_id=assigned_to_id, link=f"/tasks?id={task_id}"))
    if reviewer_id is not None:
        task.reviewer_id = reviewer_id
    if progress is not None:
        task.progress = progress
    if task.parent_id:
        await _update_parent_task(db, task.parent_id, current_user.id)
    await db.commit()
    await _recalculate_project_progress(db, task.project_id)
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id))
    task = r.scalars().first()
    return success_response(data=await _serialize_task(db, task))


@router.delete("/{task_id}")
async def delete_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user), task_id: int,
) -> Any:
    r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
    parent_id_saved = task.parent_id
    project_id_saved = task.project_id
    task.is_deleted = True
    await _add_history(db, task_id, "deleted", "Task deleted", user_id=current_user.id)
    if task.parent_id:
        await _update_parent_task(db, task.parent_id, current_user.id)
    await db.commit()
    await _recalculate_project_progress(db, project_id_saved)
    if parent_id_saved:
        await _recalculate_parent_progress(db, parent_id_saved)
    return success_response(message="Task deleted")


@router.get("/{task_id}")
async def get_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user), task_id: int,
) -> Any:
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
    return success_response(data=await _serialize_task(db, task))


@router.put("/{task_id}/status")
async def update_status(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_id: int, status: str, reason: Optional[str] = None,
) -> Any:
    r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
    old = task.status.value if task.status else None
    task.status = status
    desc = f"Status: {old} → {status}"
    if status == "blocked" and reason:
        desc += f" (Reason: {reason})"
    await _add_history(db, task_id, "status_changed", desc, old_val=old, new_val=status, user_id=current_user.id)
    if task.parent_id:
        await _update_parent_task(db, task.parent_id, current_user.id)
    await db.commit()
    await _recalculate_project_progress(db, task.project_id)
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    return success_response(data={"status": status})


@router.put("/bulk/status")
async def bulk_status(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_ids: List[int], status: str,
) -> Any:
    r = await db.execute(select(Task).filter(Task.id.in_(task_ids), Task.is_deleted == False))
    parent_ids = set()
    project_ids = set()
    for task in r.scalars().all():
        old = task.status.value if task.status else None
        task.status = status
        await _add_history(db, task.id, "status_changed", f"Status: {old} → {status}", old_val=old, new_val=status, user_id=current_user.id)
        if task.parent_id:
            parent_ids.add(task.parent_id)
        if task.project_id:
            project_ids.add(task.project_id)
    for p_id in parent_ids:
        await _update_parent_task(db, p_id, current_user.id)
    await db.commit()
    for p_id in project_ids:
        await _recalculate_project_progress(db, p_id)
    for p_id in parent_ids:
        await _recalculate_parent_progress(db, p_id)
    return success_response(message=f"Updated {len(task_ids)} tasks")


# ─── HISTORY / TRANSFERS ──────────────────────────────────────────────────────

@router.get("/{task_id}/history")
async def get_history(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user), task_id: int,
) -> Any:
    r = await db.execute(select(TaskHistory).where(TaskHistory.task_id == task_id, TaskHistory.is_deleted == False).order_by(TaskHistory.created_at.desc()))
    result = []
    for e in r.scalars().all():
        user = await _get_user(db, e.performed_by_id)
        result.append({
            "id": e.id, "action": e.action, "description": e.description,
            "old_value": e.old_value, "new_value": e.new_value,
            "user_name": user.full_name if user else None,
            "user_avatar": user.profile_picture if user else None,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })
    return success_response(data=result)


@router.post("/{task_id}/transfer")
async def transfer_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_id: int, to_user_id: int, reason: str, current_progress: float = None,
    remaining_work: str = None, new_deadline: str = None, transfer_notes: str = None,
) -> Any:
    r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
    from_id = task.assigned_to_id or current_user.id
    transfer = TaskTransfer(task_id=task_id, from_user_id=from_id, to_user_id=to_user_id,
                            reason=reason, current_progress=current_progress, remaining_work=remaining_work,
                            new_deadline=new_deadline, transfer_notes=transfer_notes,
                            status=TransferStatusEnum.pending, transferred_by_id=current_user.id)
    db.add(transfer)
    task.assigned_to_id = to_user_id
    if new_deadline:
        task.deadline = new_deadline
    await _add_history(db, task_id, "transferred", f"Transferred from {from_id} to {to_user_id}",
                       old_val=str(from_id), new_val=str(to_user_id), user_id=current_user.id)
    db.add(Notification(title="Task transferred to you", message=f"You received: {task.title}",
                       type=NotificationTypeEnum.task_assigned, user_id=to_user_id, link=f"/tasks?id={task_id}"))
    await db.commit()
    await db.refresh(transfer)
    return success_response(message="Task transferred", data={"transfer_id": transfer.id})


@router.get("/{task_id}/transfers")
async def get_transfers(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user), task_id: int,
) -> Any:
    r = await db.execute(select(TaskTransfer).filter(TaskTransfer.task_id == task_id, TaskTransfer.is_deleted == False).order_by(TaskTransfer.created_at.desc()))
    result = []
    for t in r.scalars().all():
        fu = await _get_user(db, t.from_user_id)
        tu = await _get_user(db, t.to_user_id)
        result.append({
            "id": t.id, "from_user_name": fu.full_name if fu else None,
            "from_user_avatar": fu.profile_picture if fu else None,
            "to_user_name": tu.full_name if tu else None, "to_user_avatar": tu.profile_picture if tu else None,
            "reason": t.reason, "current_progress": t.current_progress,
            "remaining_work": t.remaining_work, "new_deadline": t.new_deadline,
            "transfer_notes": t.transfer_notes, "status": t.status.value if t.status else "pending",
            "created_at": t.created_at.isoformat() if t.created_at else None,
        })
    return success_response(data=result)


# ─── QUARTERS ─────────────────────────────────────────────────────────────────

@router.get("/quarters/list")
async def list_quarters(*, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user)) -> Any:
    r = await db.execute(select(Quarter).filter(Quarter.is_deleted == False).order_by(Quarter.fiscal_year.desc(), Quarter.quarter_number.desc()))
    return success_response(data=[_serialize_quarter(q) for q in r.scalars().all()])


@router.post("/quarters")
async def create_quarter(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    name: str, fiscal_year: int, quarter_number: int, start_date: str = None, end_date: str = None, goals: str = None,
) -> Any:
    q = Quarter(name=name, fiscal_year=fiscal_year, quarter_number=quarter_number,
                start_date=start_date, end_date=end_date, goals=goals, created_by_id=current_user.id)
    db.add(q)
    await db.commit()
    await db.refresh(q)
    return success_response(data=_serialize_quarter(q))


@router.put("/quarters/{quarter_id}")
async def update_quarter(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    quarter_id: int, name: str = None, status: str = None, goals: str = None, start_date: str = None, end_date: str = None,
) -> Any:
    r = await db.execute(select(Quarter).filter(Quarter.id == quarter_id, Quarter.is_deleted == False))
    q = r.scalars().first()
    if not q:
        return error_response(message="Quarter not found")
    if name is not None: q.name = name
    if status is not None: q.status = status
    if goals is not None: q.goals = goals
    if start_date is not None: q.start_date = start_date
    if end_date is not None: q.end_date = end_date
    await db.commit()
    await db.refresh(q)
    return success_response(data=_serialize_quarter(q))


# ─── EMPLOYEE PERFORMANCE ─────────────────────────────────────────────────────

@router.get("/performance/{user_id}")
async def get_performance(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    user_id: int, quarter_id: int = None,
) -> Any:
    user = await _get_user(db, user_id)
    if not user:
        return error_response(message="User not found")
    base = [Task.assigned_to_id == user_id, Task.is_deleted == False]
    if quarter_id:
        base.append(or_(Task.quarter_id == quarter_id, Task.quarter_id == None))

    total = (await db.execute(select(func.count()).select_from(Task).where(*base))).scalar() or 0
    completed = (await db.execute(select(func.count()).select_from(Task).where(*base, Task.status == TaskStatusEnum.completed))).scalar() or 0
    today = datetime.utcnow().strftime("%Y-%m-%d")
    overdue = (await db.execute(select(func.count()).select_from(Task).where(*base, Task.deadline < today, Task.status != TaskStatusEnum.completed))).scalar() or 0
    t_out = (await db.execute(select(func.count()).select_from(TaskTransfer).where(TaskTransfer.from_user_id == user_id, TaskTransfer.is_deleted == False))).scalar() or 0
    t_in = (await db.execute(select(func.count()).select_from(TaskTransfer).where(TaskTransfer.to_user_id == user_id, TaskTransfer.is_deleted == False))).scalar() or 0
    pct = round((completed / total) * 100, 1) if total > 0 else 0.0

    tasks_r = await db.execute(select(Task).options(selectinload(Task.subtasks)).where(*base).order_by(Task.created_at.desc()).limit(50))
    tasks = [await _serialize_task(db, t) for t in tasks_r.scalars().all()]

    return success_response(data={
        "user": {"id": user.id, "name": user.full_name, "avatar": user.profile_picture, "department": user.department, "role": user.role if user.role else None},
        "stats": {"totalTasks": total, "completedTasks": completed, "overdueTasks": overdue, "completionPct": pct, "transfersOut": t_out, "transfersIn": t_in},
        "tasks": tasks,
    })
