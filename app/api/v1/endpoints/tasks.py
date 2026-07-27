from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.task import Task
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
    )
    if current_user.role not in ["super_admin", "ceo"]:
        stmt = stmt.filter(
            (Task.assigned_to_id == current_user.id) | 
            (Task.assigned_by_id == current_user.id)
        )
        
    result = await db.execute(stmt.offset(skip).limit(limit))
    tasks = result.scalars().all()
    data = [TaskSchema.from_orm(t).dict() for t in tasks]
    return success_response(data=data, message="Tasks retrieved successfully")

@router.get("/overdue", response_model=APIResponse)
async def read_overdue_tasks(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    from datetime import datetime
    today_str = datetime.now().strftime('%Y-%m-%d')
    stmt = (
        select(Task)
        .options(selectinload(Task.assigned_to), selectinload(Task.assigned_by))
        .filter(Task.is_deleted == False)
        .filter(Task.status != TaskStatusEnum.completed)
        .filter(Task.deadline < today_str)
    )
    if current_user.role not in ["super_admin", "ceo"]:
        stmt = stmt.filter(
            (Task.assigned_to_id == current_user.id) | 
            (Task.assigned_by_id == current_user.id)
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
        db_task = Task(**task_data, assigned_by_id=current_user.id)
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
    for field, value in update_data.items():
        setattr(task, field, value)
        
    if task.assigned_to_id and task.assigned_to_id != old_assigned_to_id:
        await _create_task_assignment_notification(db, task, current_user)

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
