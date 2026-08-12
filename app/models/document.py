from sqlalchemy import Column, Integer, String, Boolean, ForeignKey, DateTime
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.db.base_class import Base

class Document(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True)
    s3_key = Column(String, unique=True, index=True, nullable=False)
    url = Column(String, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    is_deleted = Column(Boolean, default=False)
    
    # Linkage
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    task_id = Column(Integer, ForeignKey("task.id"), nullable=True)
    
    # Security
    access_type = Column(String, default="code") # "access" or "code"
    passcode = Column(String, nullable=True) # Auto-generated code
    
    # Uploader
    uploaded_by_id = Column(Integer, ForeignKey("user.id"))
    
    # Relationships
    project = relationship("Project")
    task = relationship("Task")
    uploaded_by = relationship("User")
    allowed_users = relationship("DocumentUserAccess", back_populates="document", cascade="all, delete-orphan")


class DocumentUserAccess(Base):
    id = Column(Integer, primary_key=True, index=True)
    document_id = Column(Integer, ForeignKey("document.id", ondelete="CASCADE"))
    user_id = Column(Integer, ForeignKey("user.id", ondelete="CASCADE"))
    granted_at = Column(DateTime(timezone=True), server_default=func.now())
    
    document = relationship("Document", back_populates="allowed_users")
    user = relationship("User")
