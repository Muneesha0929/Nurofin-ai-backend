from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from pydantic import BaseModel
import json
from app.models.task_comment import TaskComment
from app.services.mentions import extract_mentioned_users
from app.services.s3 import upload_file_to_s3, get_presigned_url
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload, aliased
from sqlalchemy import func, or_, and_, case, exists, select, update
from typing import Any, Optional, List
from datetime import datetime, timedelta

from app.api import deps
from app.models.user import User
from app.models.task import Task, TaskStatusEnum, TaskPriorityEnum
from app.models.issue import Issue, IssueStatusEnum
from app.models.quarter import Quarter
from app.models.project import Project
from app.models.task_history import TaskHistory
from app.models.task_transfer import TaskTransfer, TransferStatusEnum
from app.models.notification import Notification, NotificationTypeEnum
from app.core.responses import success_response, error_response
from app.services.cos_access import cos_today, require_task_edit, valid_date
from app.services.cos_timeline import add_entry, clean_date, clean_reason, deadline_stats
from app.core.scheduling import find_next_free_slot
from app.core.live_events import publish_live_event

router = APIRouter()

def _iso_utc(dt):
    if not dt:
        return None
    # If dt was stored in local IST (more than 15 minutes ahead of current UTC),
    # adjust back to UTC by subtracting 5 hours 30 minutes before appending 'Z'
    now_utc = datetime.utcnow()
    if dt > now_utc + timedelta(minutes=15):
        dt = dt - timedelta(hours=5, minutes=30)
    s = dt.isoformat()
    if not s.endswith("Z") and "+" not in s:
        s += "Z"
    return s



async def _serialize_task(db: AsyncSession, t: Task, load_subtasks: bool = True, cache: dict = None, _depth: int = 0) -> dict:
    if cache is None:
        cache = {'users': {}, 'projects': {}, 'visited': set()}
    if 'visited' not in cache:
        cache['visited'] = set()
        
    if t.id in cache['visited']:
        # Prevent infinite recursion if there is a cycle in subtasks
        return None
    cache['visited'].add(t.id)

    max_subtask_depth = cache.get('max_subtask_depth')
        
    async def get_user_cached(uid):
        if not uid: return None
        if uid not in cache['users']:
            cache['users'][uid] = (await db.execute(select(User).filter(User.id == uid))).scalars().first()
        return cache['users'][uid]

    async def get_project_cached(pid):
        if not pid: return None
        if pid not in cache['projects']:
            from app.models.project import Project
            cache['projects'][pid] = (await db.execute(select(Project).filter(Project.id == pid))).scalars().first()
        return cache['projects'][pid]

    subtasks = []
    if load_subtasks:
        max_subtask_depth = cache.get('max_subtask_depth')
        # Enforce a recursion depth cap to avoid exponential N+1 on deep hierarchies
        if (max_subtask_depth is None) or (_depth < max_subtask_depth):
            try:
                st_list = [s for s in t.subtasks if not s.is_deleted]
            except Exception:
                st_res = await db.execute(select(Task).where(Task.parent_id == t.id, Task.is_deleted == False))
                st_list = st_res.scalars().all()
                
            for s in st_list:
                serialized_st = await _serialize_task(db, s, load_subtasks=True, cache=cache, _depth=_depth + 1)
                if serialized_st is not None:
                    subtasks.append(serialized_st)
            
    assignee = await get_user_cached(t.assigned_to_id)
    assigner = await get_user_cached(t.assigned_by_id)
    reviewer = await get_user_cached(t.reviewer_id)
    project = await get_project_cached(t.project_id)
    parent_title = None
    if t.parent_id:
        parent_t = (await db.execute(select(Task).filter(Task.id == t.parent_id))).scalars().first()
        if parent_t:
            parent_title = parent_t.title
            if not project and parent_t.project_id:
                project = await get_project_cached(parent_t.project_id)
    
    # Fetch latest transfer history
    transfer_date = None
    transfer_to_name = None
    if t.parent_id is None:
        try:
            # Prefer a pre-batched lookup when available (list views), else query per task
            prebuilt = cache.get('history_lookup')
            if prebuilt is not None:
                info = prebuilt.get(t.id)
                if info:
                    transfer_date = info.get('transfer_date')
                    transfer_to_name = info.get('transfer_to_name')
            else:
                hist_res = await db.execute(
                    select(TaskHistory)
                    .filter(TaskHistory.task_id == t.id, TaskHistory.action == "assigned", TaskHistory.is_deleted == False)
                    .order_by(TaskHistory.created_at.desc())
                    .limit(1)
                )
                latest_assigned_history = hist_res.scalars().first()
                if latest_assigned_history:
                    transfer_date = latest_assigned_history.created_at.strftime("%Y-%m-%d") if latest_assigned_history.created_at else None
                    if latest_assigned_history.new_value and latest_assigned_history.new_value != "None" and latest_assigned_history.new_value.isdigit():
                        new_assignee_id = int(latest_assigned_history.new_value)
                        t_user = await get_user_cached(new_assignee_id)
                        if t_user:
                            transfer_to_name = t_user.full_name
        except Exception:
            pass

    # Completion date change history (newest first)
    date_history = []
    prebuilt_dh = cache.get("date_history_lookup")
    if prebuilt_dh is not None:
        date_history = prebuilt_dh.get(t.id, [])
    else:
        dh_rows = (await db.execute(
            select(TaskHistory)
            .filter(
                TaskHistory.task_id == t.id,
                TaskHistory.action == "actual_completion_date_changed",
                TaskHistory.is_deleted == False,
            )
            .order_by(TaskHistory.created_at.desc(), TaskHistory.id.desc())
        )).scalars().all()
        for h in dh_rows:
            u = await get_user_cached(h.performed_by_id)
            date_history.append({
                "id": h.id,
                "old_value": h.old_value,
                "new_value": h.new_value,
                "description": h.description,
                "changed_by": u.full_name if u else None,
                "changed_at": _iso_utc(h.created_at),
            })

    prebuilt_dl = cache.get("deadline_stats_lookup")
    if prebuilt_dl is not None:
        dl_stats = prebuilt_dl.get(t.id) or {}
    else:
        dl_stats = (await deadline_stats(db, "task", [t.id])).get(t.id) or {}
    current_dl = (t.deadline or "")[:10] or None

    return {
        "id": t.id, "title": t.title, "description": t.description,
        "status": t.status.value if hasattr(t.status, 'value') else (t.status or "todo"),
        "priority": t.priority.value if hasattr(t.priority, 'value') else (t.priority or "medium"),
        "deadline": t.deadline, "start_date": t.start_date,
        "original_deadline": (t.original_deadline or "")[:10] or dl_stats.get("original") or current_dl,
        "deadline_changes": dl_stats.get("changes", 0),
        "estimated_hours": t.estimated_hours, "progress": t.progress or 0.0,
        "assigned_to_id": t.assigned_to_id,
        "assigned_to_name": assignee.full_name if assignee else None,
        "assigned_to_avatar": assignee.profile_picture if assignee else None,
        "assigned_by_id": t.assigned_by_id,
        "assigned_by_name": assigner.full_name if assigner else None,
        "reviewer_id": t.reviewer_id,
        "reviewer_name": reviewer.full_name if reviewer else None,
        "project_id": t.project_id or (project.id if project else None),
        "project_name": project.name if project else None,
        "parent_id": t.parent_id,
        "parent_title": parent_title,
        "quarter_id": t.quarter_id,
        "meeting_id": t.meeting_id, "subtasks": subtasks,
        "created_at": _iso_utc(t.created_at),
        "transfer_date": transfer_date,
        "transfer_to_name": transfer_to_name,
        "actual_completion_date": t.actual_completion_date,
        "completion_date_history": date_history,
        "extended_time": t.extended_time,
        "scheduled_date": t.scheduled_date,
        "scheduled_start_time": t.scheduled_start_time,
        "scheduled_end_time": t.scheduled_end_time,
        "comments_count": (cache.get("comment_counts") or {}).get(t.id, 0),
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
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=1000),
) -> Any:
    q = select(Task).options(selectinload(Task.subtasks)).where(Task.is_deleted == False, Task.parent_id == None)

    if quarter_id:
        q = q.where(
            or_(
                Task.quarter_id == quarter_id,
                Task.quarter_id == None,
                Task.subtasks.any(and_(Task.quarter_id == quarter_id, Task.is_deleted == False))
            )
        )
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
    cache = {'users': {}, 'projects': {}, 'max_subtask_depth': 5}

    # Batch-load the latest "assigned" history entry per parent task in a single query
    top_ids = [t.id for t in tasks if t.parent_id is None]
    if top_ids:
        try:
            rows = (await db.execute(
                select(TaskHistory)
                .where(
                    TaskHistory.task_id.in_(top_ids),
                    TaskHistory.action == "assigned",
                    TaskHistory.is_deleted == False,
                )
            )).scalars().all()
            latest = {}
            for h in rows:
                cur = latest.get(h.task_id)
                if cur is None or (h.created_at and (cur.created_at is None or h.created_at > cur.created_at)):
                    latest[h.task_id] = h
            lookup = {}
            for tid, h in latest.items():
                transfer_date = h.created_at.strftime("%Y-%m-%d") if h.created_at else None
                transfer_to_name = None
                if h.new_value and h.new_value != "None" and h.new_value.isdigit():
                    u_res = await db.execute(select(User).filter(User.id == int(h.new_value)))
                    u = u_res.scalars().first()
                    if u:
                        transfer_to_name = u.full_name
                        cache.setdefault('users', {})[u.id] = u
                lookup[tid] = {'transfer_date': transfer_date, 'transfer_to_name': transfer_to_name}
            cache['history_lookup'] = lookup
        except Exception:
            cache['history_lookup'] = {}

    if top_ids:
        try:
            cc_rows = (await db.execute(
                select(TaskComment.task_id, func.count(TaskComment.id))
                .where(TaskComment.task_id.in_(top_ids), TaskComment.is_deleted == False)
                .group_by(TaskComment.task_id)
            )).all()
            cache['comment_counts'] = {tid: cnt for tid, cnt in cc_rows}
        except Exception:
            cache['comment_counts'] = {}

    # Batch-load completion date change history across parent tasks & loaded subtasks
    all_ids = list(top_ids)
    def _collect_sub_ids(nodes, depth=0):
        if depth >= 5:
            return
        for node in nodes or []:
            try:
                for st in node.subtasks or []:
                    if not st.is_deleted:
                        all_ids.append(st.id)
                        _collect_sub_ids([st], depth + 1)
            except Exception:
                pass
    _collect_sub_ids(tasks)
    if all_ids:
        try:
            dh_rows = (await db.execute(
                select(TaskHistory)
                .where(
                    TaskHistory.task_id.in_(all_ids),
                    TaskHistory.action == "actual_completion_date_changed",
                    TaskHistory.is_deleted == False,
                )
                .order_by(TaskHistory.created_at.desc(), TaskHistory.id.desc())
            )).scalars().all()
            uid_set = {h.performed_by_id for h in dh_rows if h.performed_by_id and h.performed_by_id not in cache['users']}
            if uid_set:
                for u in (await db.execute(select(User).filter(User.id.in_(uid_set)))).scalars().all():
                    cache['users'][u.id] = u
            dh_map: dict = {}
            for h in dh_rows:
                u = cache['users'].get(h.performed_by_id)
                dh_map.setdefault(h.task_id, []).append({
                    "id": h.id,
                    "old_value": h.old_value,
                    "new_value": h.new_value,
                    "description": h.description,
                    "changed_by": u.full_name if u else None,
                    "changed_at": _iso_utc(h.created_at),
                })
            cache['date_history_lookup'] = dh_map
        except Exception:
            cache['date_history_lookup'] = {}
        cache['deadline_stats_lookup'] = await deadline_stats(db, "task", all_ids)

    data = []
    for t in tasks:
        serialized = await _serialize_task(db, t, cache=cache)
        if serialized is not None:
            data.append(serialized)
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
            "created_at": _iso_utc(h.created_at),
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
    scheduled_date: str = None, scheduled_start_time: str = None, scheduled_end_time: str = None,
) -> Any:
    # Handle sentinels from frontend
    actual_project_id = None if (project_id == 0 or project_id is None) else project_id
    actual_quarter_id = None if (quarter_id == 0 or quarter_id is None) else quarter_id
    actual_assigned_to_id = current_user.id if (assigned_to_id == 0 or assigned_to_id is None) else assigned_to_id
    actual_reviewer_id = None if (reviewer_id == 0 or reviewer_id is None) else reviewer_id
    actual_parent_id = None if (parent_id == 0 or parent_id is None) else parent_id
    
    actual_deadline = None if deadline == "" else deadline
    actual_start_date = None if start_date == "" else start_date
    actual_description = None if description == "" else description
    actual_estimated_hours = None if (estimated_hours is not None and estimated_hours < 0) else estimated_hours
    
    # If subtask, inherit parent start date/quarter/project if not explicitly set (the deadline is NOT inherited)
    parent = None
    if actual_parent_id:
        parent_r = await db.execute(select(Task).filter(Task.id == actual_parent_id))
        parent = parent_r.scalars().first()
        if parent:
            if not actual_start_date:
                actual_start_date = parent.start_date
            if not actual_project_id:
                actual_project_id = parent.project_id
                
    if not actual_quarter_id and (actual_deadline or actual_start_date):
        actual_quarter_id = await _determine_quarter_id(db, actual_deadline or actual_start_date)
    if not actual_quarter_id and parent and parent.quarter_id:
        actual_quarter_id = parent.quarter_id

    actual_scheduled_date = (None if scheduled_date == "" else scheduled_date) or actual_deadline
    if scheduled_start_time and scheduled_start_time != "":
        actual_scheduled_start = scheduled_start_time
        actual_scheduled_end = (None if scheduled_end_time == "" else scheduled_end_time) or "10:00"
    else:
        slot_date = actual_scheduled_date or datetime.utcnow().strftime("%Y-%m-%d")
        actual_scheduled_start, actual_scheduled_end = await find_next_free_slot(
            db, actual_assigned_to_id, slot_date
        )

    task = Task(title=title, description=actual_description, priority=priority,
                project_id=actual_project_id, quarter_id=actual_quarter_id, assigned_to_id=actual_assigned_to_id,
                assigned_by_id=current_user.id, reviewer_id=actual_reviewer_id, parent_id=actual_parent_id,
                deadline=actual_deadline, start_date=actual_start_date, estimated_hours=actual_estimated_hours,
                scheduled_date=actual_scheduled_date, scheduled_start_time=actual_scheduled_start, scheduled_end_time=actual_scheduled_end)
    db.add(task)
    await db.flush()
    await _add_history(db, task.id, "created", "Task created", user_id=current_user.id)
    if assigned_to_id:
        await _add_history(db, task.id, "assigned", f"Assigned to user {assigned_to_id}", user_id=current_user.id)
        db.add(Notification(title="New task assigned", message=f"You have been assigned: {title}",
                           type=NotificationTypeEnum.task_assigned, user_id=assigned_to_id, link=f"/workcenter?task={task.id}&tab=updates"))
    if parent_id:
        await _update_parent_task(db, parent_id, current_user.id)
        await _add_history(db, parent_id, "subtask_added", f"Subtask '{title}' added to Main Task", user_id=current_user.id)

    # Notify users mentioned in description
    if actual_description:
        mentioned = await extract_mentioned_users(db, actual_description)
        for u in mentioned:
            if u.id != current_user.id:
                db.add(Notification(
                    title=f"Tagged in task by {current_user.full_name or 'a teammate'}",
                    message=f"{current_user.full_name or 'A teammate'} tagged you in task '{task.title}': {actual_description[:120]}",
                    type=NotificationTypeEnum.project_update,
                    user_id=u.id,
                    link=f"/workcenter?task={task.id}&tab=updates&focus=reply",
                ))
    await db.commit()
    await _recalculate_project_progress(db, task.project_id)
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task.id))
    task = r.scalars().first()
    await publish_live_event("task_updated", {"task_id": task.id, "actor_id": current_user.id})
    return success_response(data=await _serialize_task(db, task))


async def _sync_task_status_to_issue(db: AsyncSession, task: Task, status: str):
    try:
        is_issue_task = (task.source and (task.source == "issue" or task.source.startswith("issue:"))) or (task.title and task.title.startswith("[Issue]"))
        if not is_issue_task:
            return
            
        issue_id = None
        if task.source and task.source.startswith("issue:"):
            try:
                issue_id = int(task.source.split(":")[1])
            except (ValueError, IndexError):
                pass
        
        clean_title = task.title.replace("[Issue]", "").strip()
        issue_stmt = select(Issue).filter(Issue.is_deleted == False)
        if issue_id:
            issue_stmt = issue_stmt.filter(Issue.id == issue_id)
        else:
            issue_stmt = issue_stmt.filter(or_(Issue.title == clean_title, Issue.title.ilike(clean_title)))
            
        issue_res = await db.execute(issue_stmt)
        matched_issue = issue_res.scalars().first()
        if matched_issue:
            task.source = f"issue:{matched_issue.id}"
            is_completed = status in ["completed", "done", "resolved", "closed"]
            new_issue_status = IssueStatusEnum.resolved if is_completed else IssueStatusEnum.in_progress
            matched_issue.status = new_issue_status
            if is_completed and not matched_issue.actual_completion_date:
                matched_issue.actual_completion_date = datetime.utcnow().strftime('%Y-%m-%d')
    except Exception as e:
        import logging
        logging.getLogger(__name__).error(f"Failed to sync task status to issue: {e}")


async def _would_create_cycle(db: AsyncSession, task_id: int, new_parent_id: Optional[int]) -> bool:
    if not new_parent_id:
        return False
    if new_parent_id == task_id:
        return True
    cur = new_parent_id
    seen = {task_id}
    for _ in range(25):
        if cur in seen:
            return True
        seen.add(cur)
        r = await db.execute(select(Task.parent_id).filter(Task.id == cur, Task.is_deleted == False))
        pid = r.scalar()
        if not pid:
            return False
        cur = pid
    return True


@router.put("/bulk/parent")
async def bulk_parent(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_ids: List[int] = Query(...), parent_id: Optional[int] = Query(None),
) -> Any:
    """Move one or more existing tasks under a target parent task (or parent_id=0/None to make top-level)."""
    target_parent_id = None if (parent_id is None or parent_id == 0) else parent_id
    target_parent = None
    if target_parent_id is not None:
        pr = await db.execute(select(Task).filter(Task.id == target_parent_id, Task.is_deleted == False))
        target_parent = pr.scalars().first()
        if not target_parent:
            return error_response(message="Target parent task not found")

    r = await db.execute(select(Task).filter(Task.id.in_(task_ids), Task.is_deleted == False))
    tasks_to_move = r.scalars().all()
    # you must be allowed to change every task you move, and the task you move them under
    await require_task_edit(db, current_user, list(tasks_to_move) + ([target_parent] if target_parent else []), "move these tasks")
    affected_parents = set()
    affected_projects = set()

    for task in tasks_to_move:
        if target_parent_id is not None and await _would_create_cycle(db, task.id, target_parent_id):
            return error_response(message=f"Cannot move task '{task.title}' under itself or its own subtask")

    for task in tasks_to_move:
        old_parent_id = task.parent_id
        if old_parent_id == target_parent_id:
            continue
        if old_parent_id:
            affected_parents.add(old_parent_id)
        if target_parent_id:
            affected_parents.add(target_parent_id)
        if task.project_id:
            affected_projects.add(task.project_id)
        if target_parent and target_parent.project_id and not task.project_id:
            task.project_id = target_parent.project_id
            affected_projects.add(target_parent.project_id)
        if target_parent and target_parent.quarter_id and not task.quarter_id:
            task.quarter_id = target_parent.quarter_id
        task.parent_id = target_parent_id
        await _add_history(
            db, task.id, "parent_changed",
            f"Moved under task #{target_parent_id}" if target_parent_id else "Detached to top-level task",
            old_val=str(old_parent_id), new_val=str(target_parent_id), user_id=current_user.id,
        )

    await db.commit()
    for p_id in affected_parents:
        await _update_parent_task(db, p_id, current_user.id)
        await _recalculate_parent_progress(db, p_id)
    for proj_id in affected_projects:
        await _recalculate_project_progress(db, proj_id)
    await db.commit()
    return success_response(message=f"Updated parent for {len(tasks_to_move)} tasks")


# ─── DEADLINE CHANGES (with a reason) ─────────────────────────────────────────

class DeadlineIn(BaseModel):
    new_date: Optional[str] = None
    reason: Optional[str] = None


class BulkDeadlineIn(BaseModel):
    task_ids: List[int]
    new_date: Optional[str] = None
    shift_days: Optional[int] = None
    reason: Optional[str] = None


async def _apply_new_deadline(db: AsyncSession, task: Task, new_date: str, reason: str, user_id: int) -> None:
    """Move the current deadline. The first deadline is kept as original_deadline; the reason goes on the timeline."""
    old = (task.deadline or "")[:10] or None
    if not task.original_deadline:
        task.original_deadline = old or new_date
    task.deadline = new_date
    if not task.scheduled_date or (old and task.scheduled_date[:10] == old):
        task.scheduled_date = new_date  # it was following the deadline
    qid = await _determine_quarter_id(db, new_date)
    if qid and qid != task.quarter_id:
        task.quarter_id = qid
        await db.execute(update(Task).where(Task.parent_id == task.id, Task.is_deleted == False).values(quarter_id=qid))
    add_entry(db, "task", task.id, "date_change", user_id, old_date=old, new_date=new_date, text=reason)
    await _add_history(db, task.id, "deadline_updated", f"Deadline: {old or 'Not set'} → {new_date} ({reason})", old_val=old, new_val=new_date, user_id=user_id)


@router.post("/bulk/deadline")
async def bulk_deadline(
    *, body: BulkDeadlineIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Move the deadline of many tasks at once, to one date or by a number of days. All or nothing:
    if you may not change one of them, none changes. Returns the old dates so the page can offer Undo."""
    ids = list(dict.fromkeys(body.task_ids or []))
    if not ids:
        raise HTTPException(status_code=400, detail="Pick at least one task.")
    if len(ids) > 500:
        raise HTTPException(status_code=400, detail="Too many tasks at once (limit 500).")
    if (body.new_date is None) == (body.shift_days is None):
        raise HTTPException(status_code=400, detail="Give either a new date or a number of days to shift by, not both.")
    if body.shift_days is not None and abs(body.shift_days) > 3650:
        raise HTTPException(status_code=400, detail="That is too many days to shift.")
    tasks = (await db.execute(select(Task).filter(Task.id.in_(ids), Task.is_deleted == False))).scalars().all()
    missing = sorted(set(ids) - {t.id for t in tasks})
    if missing:
        raise HTTPException(status_code=404, detail="These tasks were not found: " + ", ".join(str(i) for i in missing) + ". Nothing was changed.")
    await require_task_edit(db, current_user, list(tasks), "change this deadline", list_ids=True)
    reason = clean_reason(body.reason)
    fixed = clean_date(body.new_date) if body.new_date is not None else None
    today = cos_today()
    updated, previous = [], {}
    for t in sorted(tasks, key=lambda x: x.id):
        old = (t.deadline or "")[:10] or None
        if fixed:
            new = fixed
        else:
            base = datetime.strptime(old, "%Y-%m-%d").date() if valid_date(old) else today
            new = (base + timedelta(days=body.shift_days)).isoformat()
        if new == old:
            continue
        previous[str(t.id)] = old
        await _apply_new_deadline(db, t, new, reason, current_user.id)
        updated.append(t.id)
    await db.commit()
    for pid in {t.project_id for t in tasks if t.id in updated and t.project_id}:
        await _recalculate_project_progress(db, pid)
    return success_response(data={"updated": updated, "previous": previous}, message=f"Deadline changed for {len(updated)} task(s)")


@router.post("/{task_id}/deadline")
async def extend_deadline(
    *, task_id: int, body: DeadlineIn, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
) -> Any:
    """Move one task's deadline. A reason is required and shown on its timeline.
    Allowed for the assignee, the person who created the task, the HOD of a linked department and the Director."""
    task = (await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id, Task.is_deleted == False))).scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found.")
    await require_task_edit(db, current_user, [task], "change this deadline")
    new_date = clean_date(body.new_date)
    reason = clean_reason(body.reason)
    if (task.deadline or "")[:10] == new_date:
        raise HTTPException(status_code=400, detail="That is already the deadline.")
    await _apply_new_deadline(db, task, new_date, reason, current_user.id)
    await db.commit()
    if task.project_id:
        await _recalculate_project_progress(db, task.project_id)
    task = (await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id))).scalars().first()
    return success_response(data=await _serialize_task(db, task), message="Deadline changed")


@router.put("/{task_id}")
async def update_task(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_id: int, title: str = None, description: str = None, status: str = None,
    priority: str = None, deadline: str = None, start_date: str = None,
    estimated_hours: float = None, assigned_to_id: int = None,
    reviewer_id: int = None, progress: float = None,
    project_id: int = None, quarter_id: int = None, parent_id: int = None,
    actual_completion_date: str = None, extended_time: float = None,
    scheduled_date: str = None, scheduled_start_time: str = None, scheduled_end_time: str = None,
) -> Any:
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id, Task.is_deleted == False))
    task = r.scalars().first()
    if not task:
        return error_response(message="Task not found")
        
    # The deadline can be set once here. Moving it later needs a reason: POST /workcenter/{id}/deadline
    if deadline is not None:
        wanted = (deadline or "").strip()[:10]
        current = (task.deadline or "")[:10]
        if current and wanted != current:
            raise HTTPException(status_code=400, detail="Use Extend deadline and give a reason.")
    # Moving a task under another one: you must be allowed to change both
    if parent_id is not None:
        wanted_parent = None if parent_id == 0 else parent_id
        if wanted_parent != task.parent_id:
            to_check = [task]
            if wanted_parent is not None:
                np_row = (await db.execute(select(Task).filter(Task.id == wanted_parent, Task.is_deleted == False))).scalars().first()
                if np_row:
                    to_check.append(np_row)
            await require_task_edit(db, current_user, to_check, "move this task")

    old_project_id = task.project_id
    old_parent_id = task.parent_id
    
    if title is not None:
        task.title = title
    if description is not None:
        task.description = description if description != "" else None
    if status is not None:
        old = task.status.value if task.status else None
        task.status = status
        await _add_history(db, task_id, "status_changed", f"Status: {old} → {status}", old_val=old, new_val=status, user_id=current_user.id)
        await _sync_task_status_to_issue(db, task, status)
    if priority is not None:
        task.priority = priority
    # first-time deadline (changing an existing one was refused above)
    if deadline is not None and not task.deadline:
        new_dl = deadline if deadline != "" else None
        if new_dl != task.deadline:
            old_dl = task.deadline
            task.deadline = new_dl
            if new_dl and not task.original_deadline:
                task.original_deadline = new_dl[:10]
            if scheduled_date is None and new_dl:
                task.scheduled_date = new_dl
            await _add_history(db, task_id, "deadline_updated", f"Target Date set: {new_dl}", old_val=old_dl, new_val=new_dl, user_id=current_user.id)
    if scheduled_date is not None:
        task.scheduled_date = scheduled_date if scheduled_date != "" else None
    if scheduled_start_time is not None:
        task.scheduled_start_time = scheduled_start_time if scheduled_start_time != "" else None
    if scheduled_end_time is not None:
        task.scheduled_end_time = scheduled_end_time if scheduled_end_time != "" else None
    if actual_completion_date is not None:
        old_acd = task.actual_completion_date
        new_acd = actual_completion_date if actual_completion_date != "" else None
        if new_acd != old_acd:
            task.actual_completion_date = new_acd
            await _add_history(
                db,
                task_id,
                "actual_completion_date_changed",
                f"Actual Completion Date: {old_acd or 'Not set'} → {new_acd or 'Cleared'}",
                old_val=old_acd,
                new_val=new_acd,
                user_id=current_user.id,
            )
    if extended_time is not None:
        task.extended_time = extended_time
        # Propagate deadline to subtasks
        await db.execute(
            update(Task)
            .where(Task.parent_id == task_id, Task.is_deleted == False)
            .values(deadline=task.deadline)
        )
    if start_date is not None:
        task.start_date = start_date if start_date != "" else None
        # Propagate start_date to subtasks
        await db.execute(
            update(Task)
            .where(Task.parent_id == task_id, Task.is_deleted == False)
            .values(start_date=task.start_date)
        )
        
    if deadline is not None or start_date is not None:
        new_dl = deadline if deadline is not None else task.deadline
        new_sd = start_date if start_date is not None else task.start_date
        new_q_id = await _determine_quarter_id(db, new_dl or new_sd)
        if new_q_id:
            task.quarter_id = new_q_id
            # Propagate quarter_id to subtasks
            await db.execute(
                update(Task)
                .where(Task.parent_id == task_id, Task.is_deleted == False)
                .values(quarter_id=new_q_id)
            )
            
    if estimated_hours is not None:
        task.estimated_hours = estimated_hours if estimated_hours >= 0 else None
        
    if assigned_to_id is not None:
        actual_assignee_id = None if assigned_to_id == 0 else assigned_to_id
        if actual_assignee_id != task.assigned_to_id:
            old_a = task.assigned_to_id
            task.assigned_to_id = actual_assignee_id
            await _add_history(db, task_id, "assigned", "Reassigned", old_val=str(old_a), new_val=str(actual_assignee_id), user_id=current_user.id)
            if actual_assignee_id:
                db.add(Notification(title="Task assigned", message=f"You have been assigned: {task.title}",
                                   type=NotificationTypeEnum.task_assigned, user_id=actual_assignee_id, link=f"/workcenter?task={task_id}&tab=updates"))
                                   
    if reviewer_id is not None:
        task.reviewer_id = None if reviewer_id == 0 else reviewer_id
        
    if project_id is not None:
        task.project_id = None if project_id == 0 else project_id
        
    if quarter_id is not None:
        task.quarter_id = None if quarter_id == 0 else quarter_id

    if parent_id is not None:
        new_parent_id = None if parent_id == 0 else parent_id
        if new_parent_id != task.parent_id:
            if new_parent_id is not None:
                if await _would_create_cycle(db, task_id, new_parent_id):
                    return error_response(message="Cannot move task under itself or one of its subtasks")
                pr = await db.execute(select(Task).filter(Task.id == new_parent_id, Task.is_deleted == False))
                new_parent = pr.scalars().first()
                if not new_parent:
                    return error_response(message="Target parent task not found")
                if not task.project_id and new_parent.project_id:
                    task.project_id = new_parent.project_id
                if not task.quarter_id and new_parent.quarter_id:
                    task.quarter_id = new_parent.quarter_id
            task.parent_id = new_parent_id
            await _add_history(
                db, task_id, "parent_changed",
                f"Moved under task #{new_parent_id}" if new_parent_id else "Detached to top-level task",
                old_val=str(old_parent_id), new_val=str(new_parent_id), user_id=current_user.id,
            )

    if progress is not None:
        task.progress = progress
        
    if task.parent_id:
        await _update_parent_task(db, task.parent_id, current_user.id)
    if old_parent_id and old_parent_id != task.parent_id:
        await _update_parent_task(db, old_parent_id, current_user.id)

    # Notify users mentioned in description if description was updated
    if description is not None and description.strip():
        mentioned = await extract_mentioned_users(db, description)
        for u in mentioned:
            if u.id != current_user.id:
                db.add(Notification(
                    title="Tagged in task description",
                    message=f"{current_user.full_name or 'A teammate'} tagged you in task '{task.title}': {description[:120]}",
                    type=NotificationTypeEnum.project_update,
                    user_id=u.id,
                    link=f"/workcenter?task={task.id}&tab=updates&focus=reply",
                ))
        
    await db.commit()
    
    if task.project_id:
        await _recalculate_project_progress(db, task.project_id)
    if old_project_id and old_project_id != task.project_id:
        await _recalculate_project_progress(db, old_project_id)
        
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    if old_parent_id and old_parent_id != task.parent_id:
        await _recalculate_parent_progress(db, old_parent_id)
        
    r = await db.execute(select(Task).options(selectinload(Task.subtasks)).filter(Task.id == task_id))
    task = r.scalars().first()
    await publish_live_event("task_updated", {"task_id": task.id, "actor_id": current_user.id})
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


@router.put("/bulk/status")
async def bulk_status(
    *, db: AsyncSession = Depends(deps.get_db), current_user: User = Depends(deps.get_current_user),
    task_ids: List[int] = Query(...), status: str,
) -> Any:
    """Set the status of many tasks. Defined before /{task_id}/status so that "bulk" is not read as a task id."""
    r = await db.execute(select(Task).filter(Task.id.in_(task_ids), Task.is_deleted == False))
    tasks = r.scalars().all()
    await require_task_edit(db, current_user, list(tasks), "change this status")
    parent_ids = set()
    project_ids = set()
    for task in tasks:
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
    return success_response(message=f"Updated {len(tasks)} tasks")


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
    await _sync_task_status_to_issue(db, task, status)
    if task.parent_id:
        await _update_parent_task(db, task.parent_id, current_user.id)
    await db.commit()
    await _recalculate_project_progress(db, task.project_id)
    if task.parent_id:
        await _recalculate_parent_progress(db, task.parent_id)
    await publish_live_event("task_updated", {"task_id": task.id, "status": status, "actor_id": current_user.id})
    return success_response(data={"status": status})


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
            "created_at": _iso_utc(e.created_at),
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
        new_day = new_deadline[:10]
        old_day = (task.deadline or "")[:10] or None
        if not task.original_deadline:
            task.original_deadline = old_day or new_day
        if old_day != new_day:
            add_entry(db, "task", task.id, "date_change", current_user.id, old_date=old_day, new_date=new_day, text=f"Transferred: {reason}")
        task.deadline = new_deadline
    await _add_history(db, task_id, "transferred", f"Transferred from {from_id} to {to_user_id}",
                       old_val=str(from_id), new_val=str(to_user_id), user_id=current_user.id)
    db.add(Notification(title="Task transferred to you", message=f"You received: {task.title}",
                       type=NotificationTypeEnum.task_assigned, user_id=to_user_id, link=f"/workcenter?task={task_id}&tab=updates"))
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
            "created_at": _iso_utc(t.created_at),
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
    cache = {'users': {}, 'projects': {}}
    tasks = [await _serialize_task(db, t, cache=cache) for t in tasks_r.scalars().all()]

    return success_response(data={
        "user": {"id": user.id, "name": user.full_name, "avatar": user.profile_picture, "department": user.department, "role": user.role if user.role else None},
        "stats": {"totalTasks": total, "completedTasks": completed, "overdueTasks": overdue, "completionPct": pct, "transfersOut": t_out, "transfersIn": t_in},
        "tasks": tasks,
    })


# ─── TASK COMMENTS & FOLLOW-UPS ───────────────────────────────────────────────

class TaskCommentCreate(BaseModel):
    content: str
    parent_id: Optional[int] = None
    is_followup_completed: Optional[bool] = False
    attachments: Optional[List[dict]] = None


@router.get("/{task_id}/comments")
async def get_task_comments(
    task_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    task_r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = task_r.scalars().first()
    if not task:
        return error_response(message="Task not found")

    comments_r = await db.execute(
        select(TaskComment, User)
        .outerjoin(User, TaskComment.user_id == User.id)
        .filter(TaskComment.task_id == task_id, TaskComment.is_deleted == False)
        .order_by(TaskComment.created_at.asc(), TaskComment.id.asc())
    )
    rows = comments_r.all()

    comments_map = {}
    root_comments = []

    for comment, user in rows:
        attachments_data = []
        is_followup = False
        if comment.attachments:
            try:
                parsed = json.loads(comment.attachments)
                if isinstance(parsed, dict):
                    attachments_data = parsed.get("files", [])
                    is_followup = parsed.get("is_followup_completed", False)
                elif isinstance(parsed, list):
                    attachments_data = parsed
            except Exception:
                attachments_data = [{"name": "Attachment", "url": comment.attachments}]

        completed_by_name = None
        completed_at = None
        if comment.attachments:
            try:
                parsed_att = json.loads(comment.attachments)
                if isinstance(parsed_att, dict):
                    completed_by_name = parsed_att.get("completed_by_name")
                    completed_at = parsed_att.get("completed_at")
            except Exception:
                pass

        c_dict = {
            "id": comment.id,
            "task_id": comment.task_id,
            "user_id": comment.user_id,
            "user_name": user.full_name if user else "Unknown User",
            "user_avatar": user.profile_picture if user else None,
            "user_role": user.role if user else None,
            "content": comment.content,
            "parent_id": comment.parent_id,
            "attachments": attachments_data,
            "is_followup_completed": is_followup,
            "completed_by_name": completed_by_name,
            "completed_at": completed_at,
            "created_at": _iso_utc(comment.created_at),
            "replies": [],
        }
        comments_map[comment.id] = c_dict

    for cid, c_dict in comments_map.items():
        pid = c_dict["parent_id"]
        if pid and pid in comments_map:
            comments_map[pid]["replies"].append(c_dict)
        else:
            root_comments.append(c_dict)

    return success_response(data=root_comments)


@router.post("/{task_id}/comments")
async def create_task_comment(
    task_id: int,
    body: TaskCommentCreate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    task_r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = task_r.scalars().first()
    if not task:
        return error_response(message="Task not found")

    content = (body.content or "").strip()
    if not content:
        return error_response(message="Comment content cannot be empty")

    attach_dict = {
        "files": body.attachments or [],
        "is_followup_completed": bool(body.is_followup_completed),
        "completed_by_id": current_user.id if body.is_followup_completed else None,
        "completed_by_name": (current_user.full_name or "Teammate") if body.is_followup_completed else None,
        "completed_at": _iso_utc(datetime.utcnow()) if body.is_followup_completed else None,
    }
    attach_json = json.dumps(attach_dict)

    now_utc = datetime.utcnow()
    comment = TaskComment(
        task_id=task.id,
        user_id=current_user.id,
        content=content,
        parent_id=body.parent_id,
        attachments=attach_json,
        created_at=now_utc,
        updated_at=now_utc,
    )
    db.add(comment)
    await db.flush()

    # Record history if follow-up is completed
    if body.is_followup_completed:
        await _add_history(
            db, task.id, "followup_completed",
            f"{current_user.full_name or 'User'} marked follow-up completed: {content[:80]}",
            user_id=current_user.id
        )

    # Determine notification recipients for this update / follow-up
    recipients = set()
    if task.assigned_by_id and task.assigned_by_id != current_user.id:
        recipients.add(task.assigned_by_id)
    if task.assigned_to_id and task.assigned_to_id != current_user.id:
        recipients.add(task.assigned_to_id)

    # Prior commenters on this task
    prior_comments = await db.execute(
        select(TaskComment.user_id).filter(TaskComment.task_id == task.id, TaskComment.user_id != current_user.id)
    )
    for (p_uid,) in prior_comments.all():
        if p_uid:
            recipients.add(p_uid)

    # Prior history actors (e.g. tagger/assigner)
    prior_hist = await db.execute(
        select(TaskHistory.performed_by_id).filter(TaskHistory.task_id == task.id, TaskHistory.performed_by_id.isnot(None), TaskHistory.performed_by_id != current_user.id)
    )
    for (h_uid,) in prior_hist.all():
        if h_uid:
            recipients.add(h_uid)

    # Anyone mentioned in task description
    if task.description and "@" in task.description:
        desc_mentioned = await extract_mentioned_users(db, task.description)
        for u in desc_mentioned:
            if u.id != current_user.id:
                recipients.add(u.id)

    # Parent comment author if this is a reply
    if body.parent_id:
        parent_r = await db.execute(select(TaskComment).filter(TaskComment.id == body.parent_id))
        parent_comment = parent_r.scalars().first()
        if parent_comment and parent_comment.user_id != current_user.id:
            recipients.add(parent_comment.user_id)

    # Mention notifications for any users tagged with @ in this comment
    content_mentioned = await extract_mentioned_users(db, content)
    content_mentioned_ids = {u.id for u in content_mentioned if u.id != current_user.id}

    for r_id in recipients:
        if r_id in content_mentioned_ids:
            continue
        if body.is_followup_completed:
            db.add(Notification(
                title=f"Follow-up completed on '{task.title}'",
                message=f"{current_user.full_name or 'A teammate'} completed the follow-up: {content[:140]}",
                type=NotificationTypeEnum.project_update,
                user_id=r_id,
                link=f"/workcenter?task={task.id}&tab=updates",
            ))
        else:
            db.add(Notification(
                title=f"New follow-up update on '{task.title}'",
                message=f"{current_user.full_name or 'A teammate'} posted a follow-up: {content[:140]}",
                type=NotificationTypeEnum.project_update,
                user_id=r_id,
                link=f"/workcenter?task={task.id}&tab=updates",
            ))

    for u in content_mentioned:
        if u.id != current_user.id:
            db.add(Notification(
                title=f"Tagged by {current_user.full_name or 'a teammate'}",
                message=f"{current_user.full_name or 'A teammate'} tagged you in an update on '{task.title}': {content[:140]}",
                type=NotificationTypeEnum.project_update,
                user_id=u.id,
                link=f"/workcenter?task={task.id}&tab=updates",
            ))

    await db.commit()
    await db.refresh(comment)
    await publish_live_event("comment_added", {"task_id": task.id, "comment_id": comment.id, "actor_id": current_user.id})

    return success_response(
        data={
            "id": comment.id,
            "task_id": comment.task_id,
            "user_id": comment.user_id,
            "user_name": current_user.full_name,
            "user_avatar": current_user.profile_picture,
            "user_role": current_user.role,
            "content": comment.content,
            "parent_id": comment.parent_id,
            "attachments": attach_dict["files"],
            "is_followup_completed": attach_dict["is_followup_completed"],
            "created_at": _iso_utc(comment.created_at), "completed_by_name": attach_dict.get("completed_by_name"), "completed_at": attach_dict.get("completed_at"),
            "replies": [],
        },
        message="Update posted successfully"
    )



class TaskCommentUpdate(BaseModel):
    content: Optional[str] = None
    is_followup_completed: Optional[bool] = None


@router.patch("/{task_id}/comments/{comment_id}")
async def update_task_comment(
    task_id: int,
    comment_id: int,
    body: TaskCommentUpdate,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    task_r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = task_r.scalars().first()
    if not task:
        return error_response(message="Task not found")

    comm_r = await db.execute(select(TaskComment).filter(TaskComment.id == comment_id, TaskComment.task_id == task_id, TaskComment.is_deleted == False))
    comment = comm_r.scalars().first()
    if not comment:
        return error_response(message="Comment not found")

    # Anyone who has access can update anything in the follow-up and updates!
    attach_dict = {}
    if comment.attachments:
        try:
            parsed = json.loads(comment.attachments)
            if isinstance(parsed, dict):
                attach_dict = parsed
            elif isinstance(parsed, list):
                attach_dict = {"files": parsed}
        except Exception:
            attach_dict = {"files": []}

    if body.content is not None and body.content.strip():
        comment.content = body.content.strip()

    status_changed = False
    if body.is_followup_completed is not None:
        old_status = attach_dict.get("is_followup_completed", False)
        new_status = bool(body.is_followup_completed)
        if old_status != new_status:
            status_changed = True
            attach_dict["is_followup_completed"] = new_status
            if new_status:
                attach_dict["completed_by_id"] = current_user.id
                attach_dict["completed_by_name"] = current_user.full_name or "Teammate"
                attach_dict["completed_at"] = _iso_utc(datetime.utcnow())
            else:
                attach_dict.pop("completed_by_id", None)
                attach_dict.pop("completed_by_name", None)
                attach_dict.pop("completed_at", None)

    comment.attachments = json.dumps(attach_dict)
    comment.updated_at = datetime.utcnow()
    db.add(comment)

    if status_changed and attach_dict.get("is_followup_completed"):
        await _add_history(
            db, task.id, "followup_completed",
            f"{current_user.full_name or 'Teammate'} marked follow-up #{comment.id} completed",
            user_id=current_user.id
        )

        # Notify comment author and task assigner
        notif_recipients = set()
        if comment.user_id != current_user.id:
            notif_recipients.add(comment.user_id)
        if task.assigned_by_id and task.assigned_by_id != current_user.id:
            notif_recipients.add(task.assigned_by_id)
        if task.assigned_to_id and task.assigned_to_id != current_user.id:
            notif_recipients.add(task.assigned_to_id)

        for r_id in notif_recipients:
            db.add(Notification(
                title=f"Follow-up completed on '{task.title}'",
                message=f"{current_user.full_name or 'A teammate'} marked follow-up #{comment.id} as completed.",
                type=NotificationTypeEnum.project_update,
                user_id=r_id,
                link=f"/workcenter?task={task.id}&tab=updates",
            ))

    await db.commit()
    await db.refresh(comment)
    await publish_live_event("comment_updated", {"task_id": task.id, "comment_id": comment.id, "actor_id": current_user.id})

    comm_user = await _get_user(db, comment.user_id)

    return success_response(
        data={
            "id": comment.id,
            "task_id": comment.task_id,
            "user_id": comment.user_id,
            "user_name": comm_user.full_name if comm_user else "Teammate",
            "user_avatar": comm_user.profile_picture if comm_user else None,
            "user_role": comm_user.role if comm_user else None,
            "content": comment.content,
            "parent_id": comment.parent_id,
            "attachments": attach_dict.get("files", []),
            "is_followup_completed": attach_dict.get("is_followup_completed", False),
            "completed_by_name": attach_dict.get("completed_by_name"),
            "completed_at": attach_dict.get("completed_at"),
            "created_at": _iso_utc(comment.created_at),
            "replies": [],
        },
        message="Follow-up updated successfully"
    )


@router.post("/{task_id}/comments/upload")
async def upload_task_comment_attachment(
    task_id: int,
    file: UploadFile = File(...),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    task_r = await db.execute(select(Task).filter(Task.id == task_id, Task.is_deleted == False))
    task = task_r.scalars().first()
    if not task:
        return error_response(message="Task not found")

    s3_key = upload_file_to_s3(file.file, file.filename, file.content_type or "application/octet-stream")
    file_url = get_presigned_url(s3_key)
    return success_response(data={
        "filename": file.filename,
        "s3_key": s3_key,
        "url": file_url,
        "content_type": file.content_type,
    }, message="File uploaded successfully")
