from typing import Optional, List, Union
from datetime import datetime
from pydantic import BaseModel
from app.models.project import ProjectStatusEnum, ProjectPriorityEnum
from app.schemas.user import UserBasic
from app.schemas.task import Task as TaskSchema

class ProjectBase(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    status: Optional[ProjectStatusEnum] = ProjectStatusEnum.planning
    start_date: Optional[Union[datetime, str]] = None
    end_date: Optional[Union[datetime, str]] = None
    priority: Optional[ProjectPriorityEnum] = ProjectPriorityEnum.medium
    progress: Optional[float] = 0.0
    git_url: Optional[str] = None
    owner_id: Optional[int] = None
    budget: Optional[float] = 0.0
    spending: Optional[float] = 0.0

class ProjectCreate(ProjectBase):
    name: str

class ProjectUpdate(ProjectBase):
    pass

class ProjectInDBBase(ProjectBase):
    id: Optional[int] = None

    class Config:
        from_attributes = True
        from_attributes = True

class Project(ProjectInDBBase):
    owner: Optional[UserBasic] = None
    members: List[UserBasic] = []
    tasks: List[TaskSchema] = []

    class Config:
        from_attributes = True
        from_attributes = True
