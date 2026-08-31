from typing import Optional, List
from pydantic import BaseModel


class ConversationMessageBase(BaseModel):
    role: str
    content: str
    files_json: Optional[str] = None


class ConversationMessageCreate(ConversationMessageBase):
    pass


class ConversationMessageRead(ConversationMessageBase):
    id: int
    conversation_id: int
    created_at: Optional[str] = None

    class Config:
        from_attributes = True
        from_attributes = True


class ConversationBase(BaseModel):
    title: Optional[str] = "New Conversation"


class ConversationCreate(ConversationBase):
    pass


class ConversationUpdate(BaseModel):
    title: Optional[str] = None


class ConversationRead(ConversationBase):
    id: int
    user_id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    messages: List[ConversationMessageRead] = []

    class Config:
        from_attributes = True
        from_attributes = True


class ConversationListRead(BaseModel):
    id: int
    title: str
    user_id: int
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    message_count: int = 0

    class Config:
        from_attributes = True
        from_attributes = True
