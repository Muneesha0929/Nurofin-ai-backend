from sqlalchemy import Column, Integer, String, ForeignKey, Text, DateTime, Boolean
from sqlalchemy.orm import relationship
from app.db.base_class import Base
import datetime


class KnowledgeChunk(Base):
    __tablename__ = "knowledge_chunk"

    id = Column(Integer, primary_key=True, index=True)

    # Source reference
    source_type = Column(String, nullable=False, index=True)
    source_id = Column(Integer, nullable=False, index=True)
    source_title = Column(String, nullable=False)

    # Content for search
    title = Column(String, nullable=False, index=True)
    content = Column(Text, nullable=False)

    # Chunk classification
    chunk_type = Column(String, nullable=False, index=True)

    # Relationships
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meeting.id"), nullable=True, index=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=True, index=True)
    conversation_id = Column(Integer, ForeignKey("conversation.id"), nullable=True, index=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True, index=True)

    # Metadata
    chunk_metadata = Column(Text, nullable=True)
