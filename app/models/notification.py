from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Boolean, DateTime
from sqlalchemy.orm import relationship
import enum
from datetime import datetime
from app.db.base_class import Base

class NotificationTypeEnum(str, enum.Enum):
    task_assigned = "task_assigned"
    deadline = "deadline"
    meeting_reminder = "meeting_reminder"
    project_update = "project_update"
    finance_reminder = "finance_reminder"
    meeting_invitation = "meeting_invitation"
    meeting_update = "meeting_update"
    meeting_cancellation = "meeting_cancellation"
    meeting_acceptance = "meeting_acceptance"
    meeting_decline = "meeting_decline"
    meeting_mom_uploaded = "meeting_mom_uploaded"
    meeting_tasks_extracted = "meeting_tasks_extracted"
    meeting_task_assigned = "meeting_task_assigned"
    issue_assigned = "issue_assigned"
    issue_followup = "issue_followup"
    issue_status_changed = "issue_status_changed"
    performance_reviewed = "performance_reviewed"

class Notification(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, nullable=False)
    message = Column(String)
    type = Column(Enum(NotificationTypeEnum))
    is_read = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    link = Column(String, nullable=True)
    
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    user = relationship("User", backref="notifications")
