from typing import Optional, List
from pydantic import BaseModel
from datetime import datetime

class DocumentBase(BaseModel):
    title: str
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    access_type: str = "code" # "access" or "code"

class DocumentCreate(DocumentBase):
    s3_key: str
    url: str
    passcode: Optional[str] = None

class DocumentUpdate(BaseModel):
    title: Optional[str] = None
    project_id: Optional[int] = None
    task_id: Optional[int] = None
    access_type: Optional[str] = None
    passcode: Optional[str] = None

class DocumentInDBBase(DocumentBase):
    id: int
    s3_key: str
    url: str
    created_at: datetime
    uploaded_by_id: int

    class Config:
        from_attributes = True
        from_attributes = True

class Document(DocumentInDBBase):
    pass

class DocumentWithPasscode(DocumentInDBBase):
    passcode: Optional[str] = None

class DocumentUserAccessBase(BaseModel):
    user_id: int

class DocumentUserAccessCreate(DocumentUserAccessBase):
    pass

class DocumentUserAccess(DocumentUserAccessBase):
    id: int
    document_id: int

    class Config:
        from_attributes = True
        from_attributes = True
