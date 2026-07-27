from typing import Optional, List, Any
from pydantic import BaseModel


class KnowledgeBase(BaseModel):
    file_name: Optional[str] = None
    file_type: Optional[str] = None
    category: Optional[str] = None
    uploaded_by_id: Optional[int] = None
    project_id: Optional[int] = None


class KnowledgeCreate(KnowledgeBase):
    file_name: str


class KnowledgeUpdate(KnowledgeBase):
    pass


class KnowledgeInDBBase(KnowledgeBase):
    id: Optional[int] = None

    class Config:
        orm_mode = True
        from_attributes = True


class Knowledge(KnowledgeInDBBase):
    pass


class KnowledgeChunkOut(BaseModel):
    id: int
    source_type: str
    source_id: int
    source_title: str
    title: str
    content: str
    chunk_type: str
    project_id: Optional[int] = None
    meeting_id: Optional[int] = None
    task_id: Optional[int] = None
    conversation_id: Optional[int] = None
    score: Optional[float] = None
    chunk_metadata: Optional[dict] = None
    created_at: Optional[str] = None

    class Config:
        orm_mode = True
        from_attributes = True


class KnowledgeSearchRequest(BaseModel):
    q: str
    source_type: Optional[str] = None
    project_id: Optional[int] = None
    top_k: int = 20


class KnowledgeIndexRequest(BaseModel):
    source_type: str
    source_id: int


class KnowledgeSearchResult(BaseModel):
    chunk_id: int
    score: float
    title: str
    content: str
    source_type: str
    source_id: int
    source_title: str
    chunk_type: str
    metadata: Optional[dict] = None
