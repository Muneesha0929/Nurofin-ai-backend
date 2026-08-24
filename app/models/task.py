from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Float, Text, Boolean
from sqlalchemy.orm import relationship
import enum
from app.db.base_class import Base


class TaskStatusEnum(str, enum.Enum):
    todo = "todo"
    in_progress = "in_progress"
    review = "review"
    completed = "completed"
    blocked = "blocked"


class TaskPriorityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class Task(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(Text, nullable=True)
    status = Column(Enum(TaskStatusEnum), default=TaskStatusEnum.todo)
    priority = Column(Enum(TaskPriorityEnum), default=TaskPriorityEnum.medium)
    deadline = Column(String)
    start_date = Column(String, nullable=True)
    scheduled_date = Column(String, nullable=True)
    scheduled_start_time = Column(String, nullable=True)
    scheduled_end_time = Column(String, nullable=True)
    estimated_hours = Column(Float, nullable=True)
    progress = Column(Float, default=0.0)
    source = Column(String)
    extended_time = Column(Float, nullable=True)
    pushed_to_next_day = Column(Boolean, default=False)

    assigned_to_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    assigned_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    reviewer_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    project_id = Column(Integer, ForeignKey("project.id"), nullable=True)
    meeting_id = Column(Integer, ForeignKey("meeting.id"), nullable=True)
    mom_id = Column(Integer, ForeignKey("meeting_extracted_tasks.id"), nullable=True)
    parent_id = Column(Integer, ForeignKey("task.id"), nullable=True)
    quarter_id = Column(Integer, ForeignKey("quarter.id"), nullable=True)

    assigned_to = relationship("User", foreign_keys=[assigned_to_id], back_populates="assigned_tasks")
    assigned_by = relationship("User", foreign_keys=[assigned_by_id], back_populates="created_tasks")
    reviewer = relationship("User", foreign_keys=[reviewer_id])
    parent = relationship("Task", remote_side=[id], backref="subtasks")
    project = relationship("Project", back_populates="tasks")
    meeting = relationship("Meeting")
