from typing import Optional, List
from pydantic import BaseModel
from app.models.issue import IssueStatusEnum, IssuePriorityEnum

class IssueFollowupBase(BaseModel):
    message: str

class IssueFollowupCreate(IssueFollowupBase):
    pass

class IssueFollowup(IssueFollowupBase):
    id: Optional[int] = None
    issue_id: Optional[int] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    created_at: Optional[str] = None

    class Config:
        orm_mode = True
        from_attributes = True

class IssueBase(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    category: Optional[str] = None
    priority: Optional[IssuePriorityEnum] = None
    status: Optional[IssueStatusEnum] = None
    attachments: Optional[List[str]] = None
    project_id: Optional[int] = None
    assigned_user_id: Optional[int] = None
    deadline: Optional[str] = None

class IssueCreate(IssueBase):
    title: str

class IssueUpdate(IssueBase):
    pass

class IssueInDBBase(IssueBase):
    id: Optional[int] = None

    class Config:
        orm_mode = True
        from_attributes = True

class Issue(IssueInDBBase):
    pass
