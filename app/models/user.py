from sqlalchemy import Column, Integer, String, Boolean, Enum, DateTime, Float
from sqlalchemy.orm import relationship
import enum
from app.db.base_class import Base

class RoleEnum(str, enum.Enum):
    super_admin = "super_admin"
    admin = "admin"
    ceo = "ceo"
    manager = "manager"
    team_lead = "team_lead"
    employee = "employee"

class User(Base):
    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String, index=True)
    username = Column(String, unique=True, index=True)
    email = Column(String, unique=True, index=True, nullable=False)
    hashed_password = Column(String, nullable=False)
    role = Column(String, default="employee")
    department = Column(String)
    github = Column(String)
    linkedin = Column(String)
    phone = Column(String)
    profile_picture = Column(String)
    is_active = Column(Boolean(), default=True)
    
    # Compensation & Performance
    salary = Column(Float, default=0.0)
    performance_score = Column(Float, default=0.0)
    
    # Google Calendar Integration
    google_access_token = Column(String, nullable=True)
    google_refresh_token = Column(String, nullable=True)
    google_token_expires_at = Column(DateTime, nullable=True)
    
    # Relationships
    owned_projects = relationship("Project", back_populates="owner")
    assigned_tasks = relationship("Task", foreign_keys="Task.assigned_to_id", back_populates="assigned_to")
    created_tasks = relationship("Task", foreign_keys="Task.assigned_by_id", back_populates="assigned_by")
    owned_meetings = relationship("Meeting", foreign_keys="Meeting.owner_id", back_populates="owner")
    meeting_participations = relationship("MeetingParticipant", back_populates="user")

    @property
    def name(self):
        return self.full_name

    @property
    def avatar(self):
        return self.profile_picture
