from typing import Optional, List, Union
from datetime import date as dt_date, time as dt_time, datetime
from pydantic import BaseModel
from app.models.meeting import MeetingStatusEnum, MeetingTypeEnum, ParticipantStatusEnum


class MeetingAnalysisResponse(BaseModel):
    summary: Optional[str] = None
    key_points: List[str] = []
    participants: List[str] = []
    action_items: List[dict] = []
    decisions: List[str] = []
    risks: List[str] = []
    blockers: List[str] = []


class MeetingBase(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    agenda: Optional[str] = None
    meeting_link: Optional[str] = None
    location: Optional[str] = None
    date: Optional[Union[dt_date, datetime, str]] = None
    start_time: Optional[Union[dt_time, str]] = None
    end_time: Optional[Union[dt_time, str]] = None
    timezone: Optional[str] = None
    type: Optional[MeetingTypeEnum] = MeetingTypeEnum.meeting
    status: Optional[MeetingStatusEnum] = MeetingStatusEnum.scheduled
    owner_id: Optional[int] = None
    transcript: Optional[str] = None
    minutes_of_meeting: Optional[str] = None
    metadata_json: Optional[str] = None


class MeetingCreate(MeetingBase):
    title: str
    participant_ids: Optional[List[int]] = []


class MeetingUpdate(MeetingBase):
    pass


class MeetingParticipantOut(BaseModel):
    id: int
    user_id: int
    user_name: Optional[str] = None
    user_avatar: Optional[str] = None
    status: ParticipantStatusEnum = ParticipantStatusEnum.pending

    class Config:
        from_attributes = True
        from_attributes = True


class MOMUpload(BaseModel):
    summary: str


class TranscriptUpload(BaseModel):
    transcript: str


class MeetingTimelineOut(BaseModel):
    id: int
    action: str
    description: Optional[str] = None
    user_id: Optional[int] = None
    user_name: Optional[str] = None
    metadata_json: Optional[str] = None
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True
        from_attributes = True


class MeetingExtractedTaskOut(BaseModel):
    id: int
    meeting_id: int
    title: str
    description: Optional[str] = None
    priority: str = "medium"
    suggested_owner: Optional[str] = None
    deadline: Optional[str] = None
    dependencies: Optional[str] = None
    confidence: int = 80
    status: str = "pending"
    real_task_id: Optional[int] = None

    class Config:
        from_attributes = True
        from_attributes = True


class ExtractedTaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    suggested_owner: Optional[str] = None
    deadline: Optional[str] = None


class BulkApproveRequest(BaseModel):
    task_ids: List[int]


class TaskConfirmItem(BaseModel):
    extracted_task_id: int
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    deadline: Optional[str] = None
    assigned_to_id: Optional[int] = None


class TaskConfirmRequest(BaseModel):
    tasks: List[TaskConfirmItem]


class MeetingInDBBase(MeetingBase):
    id: Optional[int] = None

    class Config:
        from_attributes = True
        from_attributes = True


class Meeting(MeetingInDBBase):
    owner_name: Optional[str] = None
    owner_avatar: Optional[str] = None
    participants: List[MeetingParticipantOut] = []
    participants_count: int = 0
    mom_summary: Optional[str] = None
    mom_file_path: Optional[str] = None
    agenda: Optional[str] = None
    meeting_link: Optional[str] = None
    location: Optional[str] = None
    timezone: Optional[str] = None
    is_recurring: Optional[bool] = False
    created_at: Optional[str] = None
    mom_executive_summary: Optional[str] = None
    mom_decisions: Optional[List] = None
    mom_action_items: Optional[List] = None
    mom_risks: Optional[List] = None
    mom_blockers: Optional[List] = None
    mom_followups: Optional[List] = None
    mom_deadlines: Optional[List] = None
    mom_important_dates: Optional[List] = None
    created_by_id: Optional[int] = None
    transcript: Optional[str] = None
    ai_summary: Optional[str] = None
    minutes_of_meeting: Optional[str] = None
    analysis_status: Optional[str] = None
    metadata_json: Optional[str] = None
    document_file_path: Optional[str] = None
    document_filename: Optional[str] = None
    mom_questions: Optional[List] = None

    class Config:
        from_attributes = True
        from_attributes = True
