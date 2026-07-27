from sqlalchemy import Column, Integer, String, Enum, ForeignKey, Text, Boolean
from sqlalchemy.orm import relationship
import enum
from app.db.base_class import Base


class MeetingStatusEnum(str, enum.Enum):
    scheduled = "scheduled"
    in_progress = "in_progress"
    completed = "completed"
    cancelled = "cancelled"


class AnalysisStatusEnum(str, enum.Enum):
    uploaded = "uploaded"
    processing = "processing"
    completed = "completed"
    failed = "failed"


class MeetingTypeEnum(str, enum.Enum):
    meeting = "meeting"
    reminder = "reminder"
    event = "event"


class ParticipantStatusEnum(str, enum.Enum):
    pending = "pending"
    accepted = "accepted"
    declined = "declined"
    maybe = "maybe"


class MeetingParticipant(Base):
    __tablename__ = "meeting_participants"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meeting.id"), nullable=False)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=False)
    status = Column(Enum(ParticipantStatusEnum), default=ParticipantStatusEnum.pending)
    invited_at = Column(Integer, nullable=True)

    meeting = relationship("Meeting", back_populates="participant_entries")
    user = relationship("User", back_populates="meeting_participations")


class MeetingTimeline(Base):
    __tablename__ = "meeting_timeline"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meeting.id"), nullable=False)
    action = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    user_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    metadata_json = Column(Text, nullable=True)

    meeting = relationship("Meeting", back_populates="timeline_entries")
    user = relationship("User")


class MeetingExtractedTask(Base):
    __tablename__ = "meeting_extracted_tasks"

    id = Column(Integer, primary_key=True, index=True)
    meeting_id = Column(Integer, ForeignKey("meeting.id"), nullable=False)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    priority = Column(String, default="medium")
    suggested_owner = Column(String, nullable=True)
    deadline = Column(String, nullable=True)
    dependencies = Column(Text, nullable=True)
    confidence = Column(Integer, default=80)
    status = Column(String, default="pending")
    real_task_id = Column(Integer, ForeignKey("task.id"), nullable=True)
    is_deleted = Column(Boolean, default=False)

    meeting = relationship("Meeting", back_populates="extracted_tasks")
    real_task = relationship("Task", foreign_keys=[real_task_id])


class Meeting(Base):
    id = Column(Integer, primary_key=True, index=True)
    title = Column(String, index=True, nullable=False)
    description = Column(String)
    agenda = Column(Text, nullable=True)
    meeting_link = Column(String, nullable=True)
    location = Column(String, nullable=True)
    date = Column(String)
    start_time = Column(String)
    end_time = Column(String)
    timezone = Column(String, nullable=True)
    type = Column(Enum(MeetingTypeEnum), default=MeetingTypeEnum.meeting)
    status = Column(Enum(MeetingStatusEnum), default=MeetingStatusEnum.scheduled)
    is_deleted = Column(Boolean, default=False)
    is_recurring = Column(Boolean, default=False)
    recurrence_rule = Column(String, nullable=True)

    owner_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    created_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)

    owner = relationship("User", foreign_keys=[owner_id], back_populates="owned_meetings")
    created_by = relationship("User", foreign_keys=[created_by_id])

    # MOM fields
    mom_file_path = Column(String, nullable=True)
    mom_summary = Column(String, nullable=True)
    mom_uploaded_by_id = Column(Integer, ForeignKey("user.id"), nullable=True)
    mom_uploaded_by = relationship("User", foreign_keys=[mom_uploaded_by_id])

    # Structured MOM analysis (JSON strings)
    mom_executive_summary = Column(Text, nullable=True)
    mom_decisions = Column(Text, nullable=True)
    mom_action_items = Column(Text, nullable=True)
    mom_risks = Column(Text, nullable=True)
    mom_blockers = Column(Text, nullable=True)
    mom_followups = Column(Text, nullable=True)
    mom_deadlines = Column(Text, nullable=True)
    mom_important_dates = Column(Text, nullable=True)

    # Meeting Intelligence Phase 1 fields
    transcript = Column(Text, nullable=True)
    ai_summary = Column(Text, nullable=True)
    minutes_of_meeting = Column(Text, nullable=True)
    analysis_status = Column(String, nullable=True)
    metadata_json = Column(Text, nullable=True)

    # Phase 2.5: Meeting Intelligence → Task Assignment
    document_file_path = Column(String, nullable=True)
    document_filename = Column(String, nullable=True)
    mom_questions = Column(Text, nullable=True)

    # Participant relationships
    participant_entries = relationship(
        "MeetingParticipant", back_populates="meeting", cascade="all, delete-orphan"
    )

    timeline_entries = relationship(
        "MeetingTimeline", back_populates="meeting", cascade="all, delete-orphan"
    )

    extracted_tasks = relationship(
        "MeetingExtractedTask", back_populates="meeting", cascade="all, delete-orphan"
    )

    @property
    def participants_list(self):
        return [pe.user for pe in self.participant_entries if pe.user]
