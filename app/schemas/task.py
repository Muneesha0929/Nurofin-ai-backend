from typing import Optional, Union
from datetime import datetime
from pydantic import BaseModel
from app.models.task import TaskStatusEnum, TaskPriorityEnum
from app.schemas.user import UserBasic

class TaskBase(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    status: Optional[TaskStatusEnum] = TaskStatusEnum.todo
    priority: Optional[TaskPriorityEnum] = TaskPriorityEnum.medium
    deadline: Optional[Union[datetime, str]] = None
    progress: Optional[float] = 0.0
    source: Optional[str] = None
    assigned_to_id: Optional[int] = None
    assigned_by_id: Optional[int] = None
    project_id: Optional[int] = None
    meeting_id: Optional[int] = None
    mom_id: Optional[int] = None
    scheduled_date: Optional[Union[datetime, str]] = None
    scheduled_start_time: Optional[str] = None
    scheduled_end_time: Optional[str] = None
    extended_time: Optional[float] = None
    pushed_to_next_day: Optional[bool] = False

class TaskCreate(TaskBase):
    title: str

class TaskUpdate(TaskBase):
    pass

class TaskInDBBase(TaskBase):
    id: Optional[int] = None

    class Config:
        orm_mode = True
        from_attributes = True

class Task(TaskInDBBase):
    assigned_to: Optional[UserBasic] = None
    assigned_by: Optional[UserBasic] = None

    class Config:
        orm_mode = True
        from_attributes = True
