from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class TargetBase(BaseModel):
    title: str
    description: Optional[str] = None
    month: str
    is_global: Optional[bool] = False

class TargetCreate(TargetBase):
    user_id: int

class TargetUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    is_completed: Optional[bool] = None

class TargetScoreUpdate(BaseModel):
    score: float

class ReviewerScore(BaseModel):
    reviewer_id: int
    score: float

class TargetResponse(TargetBase):
    id: int
    user_id: int
    created_by_id: int
    is_completed: bool
    completed_at: Optional[datetime] = None
    
    average_score: Optional[float] = None
    score_count: int = 0
    my_score: Optional[float] = None
    reviewer_scores: Optional[list[ReviewerScore]] = None

    class Config:
        from_attributes = True

class TargetPermissionBase(BaseModel):
    grantee_id: int
    target_user_id: int
    can_score: Optional[bool] = False
    can_add_targets: Optional[bool] = False

class TargetPermissionCreate(TargetPermissionBase):
    pass

class TargetPermissionResponse(TargetPermissionBase):
    id: int
    granted_by_id: int

    class Config:
        from_attributes = True
