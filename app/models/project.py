from sqlalchemy import Column, Integer, String, DateTime, Enum, Float, ForeignKey, Table, Boolean
from sqlalchemy.orm import relationship
import enum
from app.db.base_class import Base

class ProjectStatusEnum(str, enum.Enum):
    planning = "planning"
    active = "active"
    on_hold = "on_hold"
    completed = "completed"
    cancelled = "cancelled"

class ProjectPriorityEnum(str, enum.Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"

# Association table for Project Members
project_members = Table(
    'project_members', Base.metadata,
    Column('project_id', Integer, ForeignKey('project.id'), primary_key=True),
    Column('user_id', Integer, ForeignKey('user.id'), primary_key=True)
)

class Project(Base):
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True, nullable=False)
    description = Column(String)
    status = Column(Enum(ProjectStatusEnum), default=ProjectStatusEnum.planning)
    is_deleted = Column(Boolean, default=False)
    start_date = Column(DateTime)
    end_date = Column(DateTime)
    priority = Column(Enum(ProjectPriorityEnum), default=ProjectPriorityEnum.medium)
    progress = Column(Float, default=0.0) # Percentage 0-100
    git_url = Column(String)
    budget = Column(Float, default=0.0)
    spending = Column(Float, default=0.0)
    
    owner_id = Column(Integer, ForeignKey("user.id"))
    owner = relationship("User", back_populates="owned_projects")
    members = relationship("User", secondary=project_members, backref="projects")
    tasks = relationship("Task", back_populates="project", primaryjoin="and_(Project.id==Task.project_id, Task.is_deleted==False)")
    issues = relationship("Issue", back_populates="project")
    knowledge_docs = relationship("Knowledge", back_populates="project")
