from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.task import Task, TaskStatusEnum
from app.models.user import User
from app.models.notification import Notification, NotificationTypeEnum
from app.schemas.task import TaskCreate, TaskUpdate, Task as TaskSchema
from app.core.responses import APIResponse, success_response, error_response
from app.services.knowledge_indexer import KnowledgeIndexer

router = APIRouter()


async def _create_task_assignment_notification(
    db: AsyncSession, task: Task, assigned_by_user: User
):
    if not task.assigned_to_id or task.assigned_to_id == assigned_by_user.id:
        return
    assigner_name = assigned_by_user.full_name or assigned_by_user.username or "Someone"
    notif = Notification(
        title=f"Task assigned: {task.title}",
        message=f'{assigner_name} assigned you the task "{task.title}".',
        type=NotificationTypeEnum.task_assigned,
        user_id=task.assigned_to_id,
    )
    db.add(notif)

@router.get("", response_model=APIResponse)
async def read_tasks(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    stmt = (
        select(Task)
        .options(selectinload(Task.assigned_to), selectinload(Task.assigned_by))
        .filter(Task.is_deleted == False)
        .order_by(Task.id.desc())
    )

    result = await db.execute(stmt.offset(skip).limit(limit))
    tasks = result.scalars().all()
    
    from datetime import datetime, timedelta
    today_str = datetime.now().strftime('%Y-%m-%d')
    pushed_any = False
    for t in tasks:
        if t.assigned_to_id == current_user.id and str(t.status).split('.')[-1] not in ["completed", "done"]:
            date_to_check = t.scheduled_date
            if not date_to_check and t.deadline:
                date_to_check = t.deadline.split('T')[0] if isinstance(t.deadline, str) else t.deadline.strftime('%Y-%m-%d')
            if date_to_check and date_to_check < today_str:
                d = datetime.now()
                if d.weekday() == 6:
                    d += timedelta(days=1)
                t.scheduled_date = d.strftime('%Y-%m-%d')
                t.pushed_to_next_day = True
                pushed_any = True
    if pushed_any:
        await db.commit()
        
    data = [TaskSchema.from_orm(t).dict() for t in tasks]
    
    # Also fetch accepted issues for the Task Center
    from app.models.issue import Issue, IssueAssignmentStatusEnum
    issues_stmt = (
        select(Issue)
        .options(selectinload(Issue.assigned_user), selectinload(Issue.reported_by))
        .filter(Issue.is_deleted == False)
        .filter(Issue.assignment_status == IssueAssignmentStatusEnum.accepted)
    )
    # If not a CEO/admin, only show their issues
    if not (current_user.role and hasattr(current_user.role, "value") and current_user.role.value in ("ceo", "admin", "super_admin")) and current_user.role not in ("ceo", "admin", "super_admin"):
        issues_stmt = issues_stmt.filter(Issue.assigned_user_id == current_user.id)
        
    issues_result = await db.execute(issues_stmt.offset(skip).limit(limit))
    issues = issues_result.scalars().all()
    
    for issue in issues:
        mapped_status = "open"
        if issue.status:
            if issue.status in ["resolved", "closed"] or getattr(issue.status, "value", None) in ["resolved", "closed"]:
                mapped_status = "completed"
            else:
                mapped_status = issue.status.value if hasattr(issue.status, "value") else issue.status

        data.append({
            "id": issue.id,
            "title": f"[Issue] {issue.title}",
            "description": issue.description,
            "status": mapped_status,
            "priority": issue.priority.value if hasattr(issue.priority, "value") else issue.priority,
            "deadline": issue.deadline,
            "progress": 100.0 if mapped_status == "completed" else 0.0,
            "source": "issue",
            "assigned_to_id": issue.assigned_user_id,
            "assigned_by_id": issue.reported_by_id,
            "project_id": issue.project_id,
            "assigned_to": {"id": issue.assigned_user.id, "full_name": issue.assigned_user.full_name, "profile_picture": issue.assigned_user.profile_picture} if issue.assigned_user else None,
            "assigned_by": {"id": issue.reported_by.id, "full_name": issue.reported_by.full_name, "profile_picture": issue.reported_by.profile_picture} if issue.reported_by else None,
            "is_issue": True,
            "scheduled_date": getattr(issue, "scheduled_date", None),
            "actual_completion_date": getattr(issue, "actual_completion_date", None) or (issue.updated_at.strftime('%Y-%m-%d') if mapped_status == "completed" and issue.updated_at else None)
        })
        
    return success_response(data=data, message="Tasks retrieved successfully")

@router.get("/overdue", response_model=APIResponse)
async def read_overdue_tasks(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    from datetime import datetime
    from sqlalchemy import or_
    today_str = datetime.now().strftime('%Y-%m-%d')
    stmt = (
        select(Task)
        .options(selectinload(Task.assigned_to), selectinload(Task.assigned_by))
        .filter(Task.is_deleted == False)
        .filter(Task.status != TaskStatusEnum.completed)
        .filter(
            or_(
                Task.deadline < today_str,
                Task.scheduled_date < today_str
            )
        )
        .order_by(Task.id.desc())
    )

    result = await db.execute(stmt.offset(skip).limit(limit))
    tasks = result.scalars().all()
    data = [TaskSchema.from_orm(t).dict() for t in tasks]
    return success_response(data=data, message="Overdue tasks retrieved successfully")

async def update_project_progress(db: AsyncSession, project_id: int):
    from app.models.project import Project
    
    # 1. Get all tasks for this project
    result = await db.execute(select(Task).filter(Task.project_id == project_id, Task.is_deleted == False))
    tasks = result.scalars().all()
    
    if not tasks:
        progress = 0.0
    else:
        completed_tasks = [t for t in tasks if t.status == "completed"]
        progress = (len(completed_tasks) / len(tasks)) * 100.0
        
    # 2. Get project and update progress
    proj_result = await db.execute(select(Project).filter(Project.id == project_id))
    project = proj_result.scalars().first()
    if project:
        project.progress = progress
        await db.commit()

@router.post("", response_model=APIResponse)
async def create_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    task_in: TaskCreate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    try:
        task_data = task_in.dict(exclude_unset=True)
        
        idempotency_key = task_data.get("idempotency_key")
        if idempotency_key:
            existing_task_res = await db.execute(select(Task).filter(Task.idempotency_key == idempotency_key))
            existing_task = existing_task_res.scalars().first()
            if existing_task:
                return APIResponse(
                    status="success",
                    message="Task created successfully (idempotency)",
                    data=existing_task
                )
        
        # Determine quarter_id from deadline or scheduled_date
        date_str = task_data.get("deadline") or task_data.get("scheduled_date")
        if date_str:
            if hasattr(date_str, "strftime"):
                date_str = date_str.strftime("%Y-%m-%d")
            else:
                date_str = str(date_str)
        
        quarter_id = None
        if date_str:
            if 'T' in date_str:
                date_str = date_str.split('T')[0]
            from app.models.quarter import Quarter
            q_res = await db.execute(select(Quarter).filter(
                Quarter.is_deleted == False,
                Quarter.start_date <= date_str,
                Quarter.end_date >= date_str
            ))
            quarter = q_res.scalars().first()
            if quarter:
                quarter_id = quarter.id
                
        db_task = Task(**task_data, assigned_by_id=current_user.id, quarter_id=quarter_id)
        db.add(db_task)
        await db.flush()

        if db_task.assigned_to_id:
            await _create_task_assignment_notification(db, db_task, current_user)

        await db.commit()

        try:
            indexer = KnowledgeIndexer(db)
            await indexer.index_task(db_task.id)
            await db.commit()
        except Exception:
            pass

        await db.refresh(db_task)
        
        # Recalculate project progress
        if db_task.project_id:
            await update_project_progress(db, db_task.project_id)
            
        # Reload with relationships
        res = await db.execute(select(Task).options(selectinload(Task.assigned_to), selectinload(Task.assigned_by)).filter(Task.id == db_task.id))
        db_task_loaded = res.scalars().first()
        
        return success_response(
            data=TaskSchema.from_orm(db_task_loaded).dict(),
            message="Task created successfully"
        )
    except Exception as e:
        import traceback
        error_details = traceback.format_exc()
        from fastapi.responses import JSONResponse
        return JSONResponse(status_code=400, content={"message": str(e), "traceback": error_details})

@router.put("/{id}", response_model=APIResponse)
async def update_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    task_in: TaskUpdate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    result = await db.execute(select(Task).options(selectinload(Task.assigned_to), selectinload(Task.assigned_by)).filter(Task.id == id, Task.is_deleted == False))
    task = result.scalars().first()
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
        
    old_project_id = task.project_id
    old_assigned_to_id = task.assigned_to_id
    update_data = task_in.dict(exclude_unset=True)
    
    # Ensure actual_completion_date is a datetime object
    if "actual_completion_date" in update_data and isinstance(update_data["actual_completion_date"], str):
        try:
            from datetime import datetime
            # Handle both "YYYY-MM-DD" and ISO format strings
            date_str = update_data["actual_completion_date"]
            if 'T' in date_str:
                update_data["actual_completion_date"] = datetime.fromisoformat(date_str.replace('Z', '+00:00'))
            else:
                update_data["actual_completion_date"] = datetime.strptime(date_str, '%Y-%m-%d')
        except ValueError:
            pass # fallback to string, let sqlalchemy handle or fail

    for field, value in update_data.items():
        setattr(task, field, value)
        
    if update_data.get("status") in [TaskStatusEnum.completed, "completed", "done"]:
        if not task.actual_completion_date:
            from datetime import datetime
            task.actual_completion_date = datetime.utcnow()
    elif update_data.get("status") and update_data.get("status") not in [TaskStatusEnum.completed, "completed", "done"]:
        task.actual_completion_date = None

    # Determine quarter_id from updated deadline or scheduled_date
    date_str = update_data.get("deadline") or update_data.get("scheduled_date")
    if date_str:
        if hasattr(date_str, "strftime"):
            date_str = date_str.strftime("%Y-%m-%d")
        else:
            date_str = str(date_str)
            
        if 'T' in date_str:
            date_str = date_str.split('T')[0]
        from app.models.quarter import Quarter
        q_res = await db.execute(select(Quarter).filter(
            Quarter.is_deleted == False,
            Quarter.start_date <= date_str,
            Quarter.end_date >= date_str
        ))
        quarter = q_res.scalars().first()
        if quarter:
            task.quarter_id = quarter.id
        
    if task.assigned_to_id and task.assigned_to_id != old_assigned_to_id:
        await _create_task_assignment_notification(db, task, current_user)
        # Add task history record for legacy update
        try:
            from app.models.task_history import TaskHistory
            db.add(TaskHistory(
                task_id=task.id,
                action="assigned",
                description="Reassigned",
                old_value=str(old_assigned_to_id),
                new_value=str(task.assigned_to_id),
                performed_by_id=current_user.id
            ))
        except Exception:
            pass

    if str(update_data.get("status")).split('.')[-1] in ["completed", "done"] and update_data.get("extended_time"):
        extended_hours = float(update_data["extended_time"])
        if extended_hours > 0 and task.scheduled_date and task.scheduled_start_time:
            try:
                subsequent_res = await db.execute(
                    select(Task).filter(
                        Task.assigned_to_id == task.assigned_to_id,
                        Task.scheduled_date == task.scheduled_date,
                        Task.scheduled_start_time > task.scheduled_start_time,
                        Task.is_deleted == False,
                        Task.id != task.id,
                        Task.status != 'completed'
                    ).order_by(Task.scheduled_start_time)
                )
                subsequent_tasks = subsequent_res.scalars().all()
                for st_task in subsequent_tasks:
                    if st_task.scheduled_start_time:
                        parts = st_task.scheduled_start_time.split(':')
                        h, m = int(parts[0]), int(parts[1])
                        new_h = h + int(extended_hours)
                        if new_h >= 24:
                            new_h -= 24
                            from datetime import datetime, timedelta
                            d = datetime.strptime(st_task.scheduled_date, "%Y-%m-%d")
                            d += timedelta(days=1)
                            if d.weekday() == 6:
                                d += timedelta(days=1)
                            st_task.scheduled_date = d.strftime("%Y-%m-%d")
                            st_task.pushed_to_next_day = True
                        st_task.scheduled_start_time = f"{new_h:02d}:{m:02d}"
                    if st_task.scheduled_end_time:
                        parts = st_task.scheduled_end_time.split(':')
                        h, m = int(parts[0]), int(parts[1])
                        new_h = h + int(extended_hours)
                        if new_h >= 24:
                            new_h -= 24
                        st_task.scheduled_end_time = f"{new_h:02d}:{m:02d}"
            except Exception as e:
                print("Error cascading task", e)

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_task(task.id)
        await db.commit()
    except Exception:
        pass
    
    # Recalculate progress for new and old projects
    if task.project_id:
        await update_project_progress(db, task.project_id)
    if old_project_id and old_project_id != task.project_id:
        await update_project_progress(db, old_project_id)
        
    # Reload with relationships to avoid lazy loading issues in serialization
    res = await db.execute(select(Task).options(selectinload(Task.assigned_to), selectinload(Task.assigned_by)).filter(Task.id == id))
    task_loaded = res.scalars().first()
    
    return success_response(data=TaskSchema.from_orm(task_loaded).dict(), message="Task updated")

@router.delete("/{id}", response_model=APIResponse)
async def delete_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    result = await db.execute(select(Task).filter(Task.id == id))
    task = result.scalars().first()
    if not task:
        return error_response(message="Task not found")
        
    project_id = task.project_id
    task.is_deleted = True
    await db.commit()
    
    # Recalculate project progress
    if project_id:
        await update_project_progress(db, project_id)
        
    return success_response(message="Task deleted")
