from sqlalchemy import Column, Integer, String, Enum, ForeignKey, JSON, DateTime
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from app.db.base_class import Base

class IssueStatusEnum(str, enum.Enum):
    open = "open"
    in_progress = "in_progress"
    resolved = "resolved"
    closed = "closed"

class IssuePriorityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

class IssueTypeEnum(str, enum.Enum):
    bug = "bug"
    feature = "feature"
    general = "general"

class IssueAssignmentStatusEnum(str, enum.Enum):
    pending_acceptance = "pending_acceptance"
    accepted = "accepted"

class Issue(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(String)
    category = Column(String)
    issue_type = Column(Enum(IssueTypeEnum), default=IssueTypeEnum.general)
    priority = Column(Enum(IssuePriorityEnum), default=IssuePriorityEnum.medium)
    status = Column(Enum(IssueStatusEnum), default=IssueStatusEnum.open)
    attachments = Column(JSON, default=[]) # Storing list of URLs
    deadline = Column(String, nullable=True)
    scheduled_date = Column(String, nullable=True)
    actual_completion_date = Column(String, nullable=True)
    
    # Auto-assignment & timeouts
    assignment_status = Column(Enum(IssueAssignmentStatusEnum), nullable=True)
    assignment_timestamp = Column(DateTime, nullable=True)
    declined_by_users = Column(JSON, default=[]) # List of user IDs who declined/timed out
    
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    assigned_user_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    reported_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    
    project = relationship("Project", back_populates="issues")
    assigned_user = relationship("User", foreign_keys=[assigned_user_id])
    reported_by = relationship("User", foreign_keys=[reported_by_id])
    followups = relationship("IssueFollowup", back_populates="issue", cascade="all, delete-orphan")
