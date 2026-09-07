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

class TargetResponse(TargetBase):
    id: int
    user_id: int
    created_by_id: int
    is_completed: bool
    completed_at: Optional[datetime] = None
    
    # We optionally include score and scored_by_id. 
    # The API endpoint will exclude them for non-authorized users.
    score: Optional[float] = None
    scored_by_id: Optional[int] = None

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
