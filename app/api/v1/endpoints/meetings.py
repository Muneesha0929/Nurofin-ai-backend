from typing import Any, Optional
from datetime import datetime, timedelta
import json
import os
import time as _time
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.meeting import (
    Meeting, MeetingParticipant, ParticipantStatusEnum,
    MeetingTimeline, MeetingExtractedTask,
)
from app.models.user import User
from app.models.task import Task, TaskStatusEnum, TaskPriorityEnum
from app.models.notification import Notification, NotificationTypeEnum
from app.schemas.meeting import (
    MeetingCreate, MeetingUpdate, Meeting as MeetingSchema,
    MeetingParticipantOut, MOMUpload, TranscriptUpload, MeetingTimelineOut,
    MeetingExtractedTaskOut, ExtractedTaskUpdate, BulkApproveRequest,
    MeetingAnalysisResponse, TaskConfirmRequest,
)
from app.core.responses import APIResponse, success_response, error_response
from app.services.google_calendar import fetch_calendar_events
from app.services.knowledge_indexer import KnowledgeIndexer
from datetime import timezone

router = APIRouter()


async def _add_timeline(db: AsyncSession, meeting_id: int, action: str, description: str = None, user_id: int = None, metadata: dict = None):
    entry = MeetingTimeline(
        meeting_id=meeting_id,
        action=action,
        description=description,
        user_id=user_id,
        metadata_json=json.dumps(metadata) if metadata else None,
    )
    db.add(entry)


def _parse_json_list(raw: Any) -> list:
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, list) else []
        except (json.JSONDecodeError, ValueError):
            return []
    return []


def _parse_json_str(raw: Any) -> Optional[str]:
    if raw is None:
        return None
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
            return str(parsed) if parsed is not None else None
        except (json.JSONDecodeError, ValueError):
            return raw
    return raw


async def _create_notification(db: AsyncSession, user_id: int, title: str, message: str, notif_type: NotificationTypeEnum, link: str = None):
    notif = Notification(
        title=title,
        message=message,
        type=notif_type,
        user_id=user_id,
        link=link,
    )
    db.add(notif)


def _serialize_meeting(m: Meeting) -> dict:
    owner_name = None
    owner_avatar = None
    if m.owner:
        owner_name = m.owner.full_name
        owner_avatar = m.owner.profile_picture

    participants = []
    for pe in (m.participant_entries or []):
        if pe.user:
            participants.append({
                "id": pe.id,
                "user_id": pe.user_id,
                "user_name": pe.user.full_name,
                "user_avatar": pe.user.profile_picture,
                "status": pe.status.value if pe.status else "pending",
            })

    return {
        "id": m.id,
        "title": m.title,
        "description": m.description,
        "agenda": m.agenda,
        "meeting_link": m.meeting_link,
        "location": m.location,
        "date": m.date,
        "start_time": m.start_time,
        "end_time": m.end_time,
        "timezone": m.timezone,
        "type": m.type.value if m.type else "meeting",
        "status": m.status.value if m.status else "scheduled",
        "is_recurring": m.is_recurring,
        "owner_id": m.owner_id,
        "owner_name": owner_name,
        "owner_avatar": owner_avatar,
        "created_by_id": m.created_by_id,
        "participants": participants,
        "participants_count": len(participants),
        "mom_summary": m.mom_summary,
        "mom_file_path": m.mom_file_path,
        "mom_executive_summary": _parse_json_str(m.mom_executive_summary),
        "mom_decisions": _parse_json_list(m.mom_decisions),
        "mom_action_items": _parse_json_list(m.mom_action_items),
        "mom_risks": _parse_json_list(m.mom_risks),
        "mom_blockers": _parse_json_list(m.mom_blockers),
        "mom_followups": _parse_json_list(m.mom_followups),
        "mom_deadlines": _parse_json_list(m.mom_deadlines),
        "mom_important_dates": _parse_json_list(m.mom_important_dates),
        "created_at": m.created_at.isoformat() if m.created_at else None,
        "transcript": m.transcript,
        "ai_summary": m.ai_summary,
        "minutes_of_meeting": m.minutes_of_meeting,
        "analysis_status": m.analysis_status,
        "metadata_json": m.metadata_json,
        "document_file_path": m.document_file_path,
        "document_filename": m.document_filename,
        "mom_questions": _parse_json_list(m.mom_questions),
    }


def _serialize_timeline(entry: MeetingTimeline) -> dict:
    return {
        "id": entry.id,
        "action": entry.action,
        "description": entry.description,
        "user_id": entry.user_id,
        "user_name": entry.user.full_name if entry.user else None,
        "metadata_json": entry.metadata_json,
        "created_at": entry.created_at.isoformat() if entry.created_at else None,
    }


def _serialize_extracted_task(t: MeetingExtractedTask) -> dict:
    return {
        "id": t.id,
        "meeting_id": t.meeting_id,
        "title": t.title,
        "description": t.description,
        "priority": t.priority,
        "suggested_owner": t.suggested_owner,
        "deadline": t.deadline,
        "dependencies": t.dependencies,
        "confidence": t.confidence,
        "status": t.status,
        "real_task_id": t.real_task_id,
    }


@router.get("", response_model=APIResponse)
async def read_meetings(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    filter: Optional[str] = Query(None, description="Filter by: today, weekly, monthly"),
    search: Optional[str] = Query(None, description="Search by title"),
    sort: Optional[str] = Query(None, description="Sort by: date, title, status"),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    stmt = (
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.is_deleted == False)
    )
    
    if current_user.role not in ["super_admin", "ceo"]:
        stmt = stmt.outerjoin(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        stmt = stmt.filter(
            (Meeting.owner_id == current_user.id) | 
            (MeetingParticipant.user_id == current_user.id)
        )

    if filter:
        today_date = datetime.now()
        if filter == "today":
            today_str = today_date.strftime("%Y-%m-%d")
            stmt = stmt.filter(Meeting.date == today_str)
        elif filter == "weekly":
            end_date = today_date + timedelta(days=7)
            stmt = stmt.filter(
                Meeting.date >= today_date.strftime("%Y-%m-%d"),
                Meeting.date <= end_date.strftime("%Y-%m-%d"),
            )
        elif filter == "monthly":
            month_prefix = today_date.strftime("%Y-%m")
            stmt = stmt.filter(Meeting.date.startswith(month_prefix))

    if search:
        stmt = stmt.filter(Meeting.title.ilike(f"%{search}%"))

    if sort == "date":
        stmt = stmt.order_by(Meeting.date.desc())
    elif sort == "title":
        stmt = stmt.order_by(Meeting.title)
    elif sort == "status":
        stmt = stmt.order_by(Meeting.status)
    else:
        stmt = stmt.order_by(Meeting.date.desc())

    result = await db.execute(stmt.offset(skip).limit(limit))
    meetings = result.scalars().unique().all()

    data = [_serialize_meeting(m) for m in meetings]

    # Fetch Google Calendar Events if connected
    if current_user.google_access_token and current_user.google_refresh_token:
        time_min = datetime.now(timezone.utc)
        time_max = time_min + timedelta(days=7)
        if filter == "today":
            time_max = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59)
        elif filter == "monthly":
            time_max = time_min + timedelta(days=30)
            
        try:
            google_events = fetch_calendar_events(current_user, time_min, time_max)
            for item in google_events:
                start = item['start'].get('dateTime', item['start'].get('date'))
                end = item['end'].get('dateTime', item['end'].get('date'))
                data.append({
                    "id": item.get("id"),
                    "title": item.get('summary', 'Busy'),
                    "description": item.get('description', ''),
                    "date": start[:10] if start else "",
                    "start_time": start[11:16] if start and len(start) > 10 else "00:00",
                    "end_time": end[11:16] if end and len(end) > 10 else "00:00",
                    "type": "google_event",
                    "status": "scheduled",
                    "source": "google_calendar"
                })
        except Exception as e:
            print(f"Failed to fetch Google Calendar: {e}")

    # Sort combined data
    if sort == "date" or not sort:
        data.sort(key=lambda x: (x.get("date") or ""), reverse=True)
    elif sort == "title":
        data.sort(key=lambda x: (x.get("title") or ""))

    return success_response(data=data, message="Events fetched successfully")


@router.get("/{id}", response_model=APIResponse)
async def read_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
            selectinload(Meeting.mom_uploaded_by),
            selectinload(Meeting.created_by),
            selectinload(Meeting.timeline_entries),
            selectinload(Meeting.extracted_tasks),
        )
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Event not found")

    data = _serialize_meeting(meeting)
    data["timeline"] = [_serialize_timeline(t) for t in (meeting.timeline_entries or [])]
    data["extracted_tasks"] = [_serialize_extracted_task(t) for t in (meeting.extracted_tasks or [])]

    return success_response(data=data, message="Event fetched successfully")


def get_alternative_times(busy_blocks: list, req_date_str: str, req_duration_minutes: int, start_hour: int = 9, end_hour: int = 17) -> list:
    suggestions = []
    
    dates_to_check = [
        datetime.strptime(req_date_str, "%Y-%m-%d").date(),
        (datetime.strptime(req_date_str, "%Y-%m-%d") + timedelta(days=1)).date()
    ]
    
    for check_date in dates_to_check:
        if len(suggestions) >= 3:
            break
            
        current_time = datetime.combine(check_date, datetime.min.time()).replace(hour=start_hour, minute=0)
        end_of_day = datetime.combine(check_date, datetime.min.time()).replace(hour=end_hour, minute=0)
        
        day_blocks = []
        for b in busy_blocks:
            if "start_time" in b and "end_time" in b:
                try:
                    b_start = datetime.combine(check_date, datetime.strptime(b["start_time"], "%H:%M").time())
                    b_end = datetime.combine(check_date, datetime.strptime(b["end_time"], "%H:%M").time())
                    day_blocks.append((b_start, b_end))
                except: pass
            elif "start" in b and "end" in b:
                try:
                    b_s = datetime.fromisoformat(b["start"].replace('Z', '+00:00'))
                    b_e = datetime.fromisoformat(b["end"].replace('Z', '+00:00'))
                    if b_s.date() == check_date:
                        b_start = b_s.replace(tzinfo=None)
                        b_end = b_e.replace(tzinfo=None)
                        day_blocks.append((b_start, b_end))
                except: pass
                
        day_blocks.sort(key=lambda x: x[0])
        
        while current_time + timedelta(minutes=req_duration_minutes) <= end_of_day:
            slot_end = current_time + timedelta(minutes=req_duration_minutes)
            
            conflict = False
            for bs, be in day_blocks:
                if (current_time < be and slot_end > bs):
                    conflict = True
                    current_time = be
                    break
            
            if not conflict:
                suggestions.append({
                    "date": check_date.strftime("%Y-%m-%d"),
                    "start_time": current_time.strftime("%H:%M"),
                    "end_time": slot_end.strftime("%H:%M")
                })
                current_time = slot_end
                if len(suggestions) >= 3:
                    break
                    
    return suggestions


@router.post("", response_model=APIResponse)
async def create_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    meeting_in: MeetingCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    participant_ids = meeting_in.participant_ids or []
    
    # --- Conflict Detection ---
    if meeting_in.date and meeting_in.start_time and meeting_in.end_time:
        all_ids = set(participant_ids)
        all_ids.add(current_user.id)
        
        try:
            req_start_dt = datetime.strptime(meeting_in.start_time, "%H:%M")
            req_end_dt = datetime.strptime(meeting_in.end_time, "%H:%M")
            duration_mins = int((req_end_dt - req_start_dt).total_seconds() / 60)
            if duration_mins <= 0:
                duration_mins = 60
        except:
            duration_mins = 60
            
        busy_blocks = []
        try:
            time_min = datetime.fromisoformat(meeting_in.date + "T00:00:00+00:00")
            time_max = datetime.fromisoformat(meeting_in.date + "T23:59:59+00:00")
            
            for uid in all_ids:
                # fetch Nurofin local
                local_query = (
                    select(Meeting)
                    .outerjoin(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
                    .filter((Meeting.owner_id == uid) | (MeetingParticipant.user_id == uid))
                    .filter(Meeting.is_deleted == False)
                    .filter(Meeting.date == meeting_in.date)
                )
                local_result = await db.execute(local_query)
                local_meetings = local_result.scalars().all()
                for m in local_meetings:
                    busy_blocks.append({"start_time": m.start_time, "end_time": m.end_time})
                
                # fetch Google Calendar
                user_result = await db.execute(select(User).filter(User.id == uid))
                u = user_result.scalars().first()
                if u and u.google_access_token and u.google_refresh_token:
                    try:
                        g_events = fetch_calendar_events(u, time_min, time_max)
                        for item in g_events:
                            st = item['start'].get('dateTime', item['start'].get('date'))
                            et = item['end'].get('dateTime', item['end'].get('date'))
                            busy_blocks.append({"start": st, "end": et})
                    except:
                        pass
                        
            # Check if the requested time overlaps with any busy block
            conflict = False
            req_start_time = datetime.strptime(meeting_in.start_time, "%H:%M").time()
            req_end_time = datetime.strptime(meeting_in.end_time, "%H:%M").time()
            for b in busy_blocks:
                if "start_time" in b and "end_time" in b:
                    b_start = datetime.strptime(b["start_time"], "%H:%M").time()
                    b_end = datetime.strptime(b["end_time"], "%H:%M").time()
                    if req_start_time < b_end and req_end_time > b_start:
                        conflict = True
                        break
                elif "start" in b and "end" in b:
                    b_s = datetime.fromisoformat(b["start"].replace('Z', '+00:00')).time()
                    b_e = datetime.fromisoformat(b["end"].replace('Z', '+00:00')).time()
                    if req_start_time < b_e and req_end_time > b_s:
                        conflict = True
                        break
                        
            if conflict:
                suggestions = get_alternative_times(busy_blocks, meeting_in.date, duration_mins)
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "At this time there's already a meet or schedule for one of the participants. Can we schedule it at another time?",
                        "alternative_times": suggestions
                    }
                )
        except HTTPException:
            raise
        except Exception as e:
            print(f"Error checking conflicts: {e}")
            pass # ignore general parse errors and continue
    # --- End Conflict Detection ---

    meeting_data = meeting_in.dict(exclude_unset=True, exclude={"participant_ids"})
    db_meeting = Meeting(**meeting_data, owner_id=current_user.id, created_by_id=current_user.id)
    db.add(db_meeting)
    await db.flush()

    for uid in participant_ids:
        entry = MeetingParticipant(
            meeting_id=db_meeting.id,
            user_id=uid,
            status=ParticipantStatusEnum.pending,
            invited_at=int(_time.time()),
        )
        db.add(entry)
        await _create_notification(
            db, uid,
            title=f"Meeting invitation: {meeting_in.title}",
            message=f"You have been invited to '{meeting_in.title}'",
            notif_type=NotificationTypeEnum.meeting_invitation,
            link=f"/meetings?id={db_meeting.id}",
        )

    await _add_timeline(db, db_meeting.id, "meeting_created", f"Meeting '{meeting_in.title}' created", current_user.id)

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(db_meeting.id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == db_meeting.id)
    )
    db_meeting_loaded = res.scalars().first()

    return success_response(
        data=_serialize_meeting(db_meeting_loaded),
        message="Event created successfully",
    )


@router.put("/{id}", response_model=APIResponse)
async def update_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    meeting_in: MeetingUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        raise HTTPException(status_code=404, detail="Event not found")

    update_data = meeting_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(meeting, field, value)

    await _add_timeline(db, id, "meeting_updated", f"Meeting '{meeting.title}' updated", current_user.id)

    for pe in (meeting.participant_entries or []):
        if pe.user_id != current_user.id:
            await _create_notification(
                db, pe.user_id,
                title=f"Meeting updated: {meeting.title}",
                message=f"The meeting '{meeting.title}' has been updated.",
                notif_type=NotificationTypeEnum.meeting_update,
                link=f"/meetings?id={id}",
            )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(data=_serialize_meeting(meeting_loaded), message="Event updated")


@router.delete("/{id}", response_model=APIResponse)
async def delete_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Event not found")

    for pe in (meeting.participant_entries or []):
        if pe.user_id != current_user.id:
            await _create_notification(
                db, pe.user_id,
                title=f"Meeting cancelled: {meeting.title}",
                message=f"The meeting '{meeting.title}' has been cancelled.",
                notif_type=NotificationTypeEnum.meeting_cancellation,
                link=f"/meetings?id={id}",
            )

    meeting.is_deleted = True
    await db.commit()
    return success_response(message="Event deleted")


@router.post("/{id}/accept", response_model=APIResponse)
async def accept_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == id,
            MeetingParticipant.user_id == current_user.id,
        )
    )
    entry = result.scalars().first()
    if not entry:
        return error_response(message="You are not invited to this meeting")

    entry.status = ParticipantStatusEnum.accepted

    meeting_result = await db.execute(select(Meeting).filter(Meeting.id == id))
    meeting = meeting_result.scalars().first()
    if meeting and meeting.owner_id and meeting.owner_id != current_user.id:
        await _create_notification(
            db, meeting.owner_id,
            title=f"Invitation accepted: {meeting.title}",
            message=f"{current_user.full_name} accepted the invitation to '{meeting.title}'.",
            notif_type=NotificationTypeEnum.meeting_acceptance,
            link=f"/meetings?id={id}",
        )

    await _add_timeline(db, id, "participant_accepted", f"{current_user.full_name} accepted", current_user.id)
    await db.commit()
    return success_response(message="Meeting invitation accepted")


@router.post("/{id}/decline", response_model=APIResponse)
async def decline_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == id,
            MeetingParticipant.user_id == current_user.id,
        )
    )
    entry = result.scalars().first()
    if not entry:
        return error_response(message="You are not invited to this meeting")

    entry.status = ParticipantStatusEnum.declined

    meeting_result = await db.execute(select(Meeting).filter(Meeting.id == id))
    meeting = meeting_result.scalars().first()
    if meeting and meeting.owner_id and meeting.owner_id != current_user.id:
        await _create_notification(
            db, meeting.owner_id,
            title=f"Invitation declined: {meeting.title}",
            message=f"{current_user.full_name} declined the invitation to '{meeting.title}'.",
            notif_type=NotificationTypeEnum.meeting_decline,
            link=f"/meetings?id={id}",
        )

    await _add_timeline(db, id, "participant_declined", f"{current_user.full_name} declined", current_user.id)
    await db.commit()
    return success_response(message="Meeting invitation declined")


@router.post("/{id}/participants", response_model=APIResponse)
async def add_participant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    user_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Meeting).filter(Meeting.id == id, Meeting.is_deleted == False))
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    existing = await db.execute(
        select(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == id,
            MeetingParticipant.user_id == user_id,
        )
    )
    if existing.scalars().first():
        return error_response(message="User is already a participant")

    entry = MeetingParticipant(
        meeting_id=id,
        user_id=user_id,
        status=ParticipantStatusEnum.pending,
        invited_at=int(_time.time()),
    )
    db.add(entry)

    await _create_notification(
        db, user_id,
        title=f"Meeting invitation: {meeting.title}",
        message=f"You have been invited to '{meeting.title}'",
        notif_type=NotificationTypeEnum.meeting_invitation,
        link=f"/meetings?id={id}",
    )

    await _add_timeline(db, id, "participant_added", f"New participant added", current_user.id)
    await db.commit()
    return success_response(message="Participant added")


@router.post("/{id}/participants/remove", response_model=APIResponse)
async def remove_participant(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    user_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Meeting).filter(Meeting.id == id, Meeting.is_deleted == False))
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    existing = await db.execute(
        select(MeetingParticipant).filter(
            MeetingParticipant.meeting_id == id,
            MeetingParticipant.user_id == user_id,
        )
    )
    entry = existing.scalars().first()
    if not entry:
        return error_response(message="User is not a participant")

    await db.delete(entry)
    await _add_timeline(db, id, "participant_removed", f"Participant removed", current_user.id)
    await db.commit()
    return success_response(message="Participant removed")


@router.post("/{id}/mom", response_model=APIResponse)
async def upload_mom(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    mom_in: MOMUpload,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    meeting.mom_summary = mom_in.summary
    meeting.mom_uploaded_by_id = current_user.id

    await _add_timeline(db, id, "mom_uploaded", f"MOM uploaded by {current_user.full_name}", current_user.id)

    for pe in (meeting.participant_entries or []):
        if pe.user_id != current_user.id:
            await _create_notification(
                db, pe.user_id,
                title=f"MOM uploaded: {meeting.title}",
                message=f"Minutes of Meeting have been uploaded for '{meeting.title}'.",
                notif_type=NotificationTypeEnum.meeting_mom_uploaded,
                link=f"/meetings?id={id}",
            )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data=_serialize_meeting(meeting_loaded),
        message="Minutes of Meeting uploaded successfully",
    )


ANALYSIS_PROMPT = """You are an expert meeting analyst. Analyze the following Minutes of Meeting and extract structured data.
Return ONLY a valid JSON object with exactly these fields (no markdown, no code fences):
{
  "executive_summary": "A 2-3 sentence summary of the meeting",
  "decisions": ["decision 1", "decision 2"],
  "action_items": [
    {"title": "task title", "description": "detailed description", "priority": "high|medium|low", "suggested_owner": "person name or null", "deadline": "date string or null"}
  ],
  "risks": ["risk 1"],
  "blockers": ["blocker 1"],
  "followups": ["followup 1"],
  "deadlines": ["deadline description 1"],
  "important_dates": ["date description 1"]
}

If a section has no items, use an empty array [].
If a field cannot be determined, use null for strings or [] for arrays.

Minutes of Meeting:
"""


@router.post("/{id}/mom/analyze", response_model=APIResponse)
async def analyze_mom(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")
    if not meeting.mom_summary:
        return error_response(message="No MOM uploaded yet")

    ai_engine_url = os.getenv("AI_ENGINE_URL", "").strip()
    if not ai_engine_url:
        return error_response(message="AI_ENGINE_URL environment variable is not configured")
    ai_engine_url = ai_engine_url.rstrip("/") + "/api/v1/chat/analyze"
    prompt = ANALYSIS_PROMPT + meeting.mom_summary

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                ai_engine_url,
                json={
                    "message": prompt,
                    "temperature": 0.1,
                    "max_tokens": 2048,
                    "system_prompt": "You are a meeting analysis AI. Return ONLY valid JSON. No markdown, no code fences, no explanation.",
                },
            )
            resp.raise_for_status()
            ai_data = resp.json()
    except Exception as e:
        return error_response(message=f"AI engine unavailable: {str(e)[:200]}")

    raw_response = ai_data.get("response", "") or ""

    analysis = None
    try:
        start = raw_response.find("{")
        end = raw_response.rfind("}") + 1
        if start != -1 and end > start:
            analysis = json.loads(raw_response[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    if not analysis:
        fallback = {
            "executive_summary": meeting.mom_summary[:500],
            "decisions": [], "action_items": [], "risks": [], "blockers": [],
            "followups": [], "deadlines": [], "important_dates": [],
        }
        analysis = fallback

    meeting.mom_executive_summary = json.dumps(analysis.get("executive_summary"))
    meeting.mom_decisions = json.dumps(analysis.get("decisions", []))
    meeting.mom_action_items = json.dumps(analysis.get("action_items", []))
    meeting.mom_risks = json.dumps(analysis.get("risks", []))
    meeting.mom_blockers = json.dumps(analysis.get("blockers", []))
    meeting.mom_followups = json.dumps(analysis.get("followups", []))
    meeting.mom_deadlines = json.dumps(analysis.get("deadlines", []))
    meeting.mom_important_dates = json.dumps(analysis.get("important_dates", []))

    extracted_tasks_data = analysis.get("action_items", [])
    created_tasks = []
    for item in extracted_tasks_data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        extracted = MeetingExtractedTask(
            meeting_id=id,
            title=item["title"],
            description=item.get("description"),
            priority=item.get("priority", "medium"),
            suggested_owner=item.get("suggested_owner"),
            deadline=item.get("deadline"),
            status="pending",
            confidence=80,
        )
        db.add(extracted)
        await db.flush()
        created_tasks.append({
            "id": extracted.id,
            "title": extracted.title,
            "priority": extracted.priority,
            "suggested_owner": extracted.suggested_owner,
        })

    await _add_timeline(
        db, id, "mom_analyzed",
        f"AI analyzed MOM - {len(created_tasks)} tasks extracted",
        current_user.id,
        metadata={"tasks_extracted": len(created_tasks)},
    )

    for pe in (meeting.participant_entries or []):
        if pe.user_id != current_user.id:
            await _create_notification(
                db, pe.user_id,
                title=f"MOM analyzed: {meeting.title}",
                message=f"AI analysis complete for '{meeting.title}'. {len(created_tasks)} tasks extracted.",
                notif_type=NotificationTypeEnum.meeting_tasks_extracted,
                link=f"/meetings?id={id}",
            )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data={
            **_serialize_meeting(meeting_loaded),
            "extracted_tasks": created_tasks,
            "analysis": analysis,
        },
        message="MOM analysis complete",
    )


TRANSCRIPT_ANALYSIS_PROMPT = """You are an expert meeting analyst. Analyze the following meeting transcript and extract structured data.
Return ONLY a valid JSON object with exactly these fields (no markdown, no code fences):
{
  "summary": "A 2-3 sentence executive summary of the meeting",
  "key_points": ["key point 1", "key point 2"],
  "participants": ["participant name 1", "participant name 2"],
  "action_items": [
    {"title": "task title", "description": "detailed description", "priority": "high|medium|low", "suggested_owner": "person name or null", "deadline": "date string or null"}
  ],
  "decisions": ["decision 1"],
  "risks": ["risk 1"],
  "blockers": ["blocker 1"]
}

If a section has no items, use an empty array [].
If a field cannot be determined, use null for strings or [] for arrays.

Meeting Transcript:
"""


@router.post("/{id}/analyze", response_model=APIResponse)
async def analyze_meeting(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    content = meeting.transcript or meeting.mom_summary
    if not content:
        return error_response(message="No transcript or MOM content available for analysis")

    meeting.analysis_status = "processing"
    await db.commit()

    ai_engine_url = os.getenv("AI_ENGINE_URL", "").strip()
    if not ai_engine_url:
        meeting.analysis_status = "failed"
        await db.commit()
        return error_response(message="AI_ENGINE_URL environment variable is not configured")

    ai_url = ai_engine_url.rstrip("/") + "/api/v1/meeting/analyze"
    prompt = TRANSCRIPT_ANALYSIS_PROMPT + content

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                ai_url,
                json={
                    "transcript": content,
                    "meeting_id": id,
                    "meeting_title": meeting.title,
                },
            )
            resp.raise_for_status()
            ai_data = resp.json()
    except Exception:
        meeting.analysis_status = "failed"
        await db.commit()
        return error_response(message="AI engine unavailable for analysis")

    analysis_result = ai_data.get("data") or ai_data
    summary = analysis_result.get("summary", "")
    key_points = analysis_result.get("key_points", [])
    participants = analysis_result.get("participants", [])
    action_items = analysis_result.get("action_items", [])
    decisions = analysis_result.get("decisions", [])
    risks = analysis_result.get("risks", [])
    blockers = analysis_result.get("blockers", [])

    meeting.ai_summary = summary
    meeting.minutes_of_meeting = json.dumps({
        "summary": summary,
        "key_points": key_points,
        "participants": participants,
        "action_items": action_items,
        "decisions": decisions,
        "risks": risks,
        "blockers": blockers,
    })
    meeting.mom_executive_summary = json.dumps(summary)
    meeting.mom_decisions = json.dumps(decisions)
    meeting.mom_action_items = json.dumps(action_items)
    meeting.mom_risks = json.dumps(risks)
    meeting.mom_blockers = json.dumps(blockers)
    meeting.analysis_status = "completed"

    extracted_tasks_data = action_items
    created_tasks = []
    for item in extracted_tasks_data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        extracted = MeetingExtractedTask(
            meeting_id=id,
            title=item["title"],
            description=item.get("description"),
            priority=item.get("priority", "medium"),
            suggested_owner=item.get("suggested_owner"),
            deadline=item.get("deadline"),
            status="pending",
            confidence=80,
        )
        db.add(extracted)
        await db.flush()
        created_tasks.append({
            "id": extracted.id,
            "title": extracted.title,
            "priority": extracted.priority,
            "suggested_owner": extracted.suggested_owner,
        })

    await _add_timeline(
        db, id, "meeting_analyzed",
        f"AI analyzed meeting transcript - {len(created_tasks)} tasks extracted",
        current_user.id,
        metadata={"tasks_extracted": len(created_tasks)},
    )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data={
            **_serialize_meeting(meeting_loaded),
            "extracted_tasks": created_tasks,
            "analysis": {
                "summary": summary,
                "key_points": key_points,
                "participants": participants,
                "action_items": action_items,
                "decisions": decisions,
                "risks": risks,
                "blockers": blockers,
            },
        },
        message="Meeting analysis complete",
    )


@router.get("/{id}/summary", response_model=APIResponse)
async def get_meeting_summary(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting).filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    summary = meeting.ai_summary or meeting.mom_executive_summary
    if summary:
        try:
            summary = json.loads(summary) if summary.startswith('"') else summary
        except (json.JSONDecodeError, ValueError):
            pass

    return success_response(
        data={
            "meeting_id": meeting.id,
            "title": meeting.title,
            "summary": summary,
            "analysis_status": meeting.analysis_status,
        },
        message="Meeting summary fetched successfully",
    )


@router.get("/{id}/mom", response_model=APIResponse)
async def get_meeting_mom(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting).filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    mom_data = None
    if meeting.minutes_of_meeting:
        try:
            mom_data = json.loads(meeting.minutes_of_meeting)
        except (json.JSONDecodeError, ValueError):
            mom_data = {"raw": meeting.minutes_of_meeting}

    return success_response(
        data={
            "meeting_id": meeting.id,
            "title": meeting.title,
            "mom": mom_data,
            "mom_summary": meeting.mom_summary,
            "analysis_status": meeting.analysis_status,
        },
        message="Meeting MoM fetched successfully",
    )


@router.post("/{id}/transcript", response_model=APIResponse)
async def upload_transcript(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    transcript_in: TranscriptUpload,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    meeting.transcript = transcript_in.transcript
    meeting.analysis_status = "uploaded"

    await _add_timeline(db, id, "transcript_uploaded", f"Transcript uploaded by {current_user.full_name}", current_user.id)

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data=_serialize_meeting(meeting_loaded),
        message="Transcript uploaded successfully",
    )


@router.get("/{id}/timeline", response_model=APIResponse)
async def get_timeline(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingTimeline)
        .filter(MeetingTimeline.meeting_id == id, MeetingTimeline.is_deleted == False)
        .order_by(MeetingTimeline.created_at.desc())
    )
    entries = result.scalars().all()

    data = []
    for e in entries:
        user_name = None
        if e.user_id:
            user_res = await db.execute(select(User).filter(User.id == e.user_id))
            user = user_res.scalars().first()
            user_name = user.full_name if user else None
        data.append({
            "id": e.id,
            "action": e.action,
            "description": e.description,
            "user_id": e.user_id,
            "user_name": user_name,
            "metadata_json": e.metadata_json,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        })

    return success_response(data=data, message="Timeline fetched successfully")


@router.get("/{id}/extracted-tasks", response_model=APIResponse)
async def get_extracted_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingExtractedTask)
        .filter(MeetingExtractedTask.meeting_id == id, MeetingExtractedTask.is_deleted == False)
    )
    tasks = result.scalars().all()
    data = [_serialize_extracted_task(t) for t in tasks]
    return success_response(data=data, message="Extracted tasks fetched")


@router.post("/{id}/extracted-tasks", response_model=APIResponse)
async def create_extracted_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    tasks_data: list[dict],
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    meeting_result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = meeting_result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    created = []
    for td in tasks_data:
        t = MeetingExtractedTask(
            meeting_id=id,
            title=td.get("title", "Untitled"),
            description=td.get("description"),
            priority=td.get("priority", "medium"),
            suggested_owner=td.get("suggested_owner"),
            deadline=td.get("deadline"),
            dependencies=td.get("dependencies"),
            confidence=td.get("confidence", 80),
        )
        db.add(t)
        created.append(td.get("title", "Untitled"))

    await _add_timeline(
        db, id, "tasks_extracted",
        f"AI extracted {len(created)} task(s)",
        current_user.id,
        metadata={"count": len(created)},
    )

    for pe in (meeting.participant_entries or []):
        if pe.user_id != current_user.id:
            await _create_notification(
                db, pe.user_id,
                title=f"Tasks extracted: {meeting.title}",
                message=f"{len(created)} task(s) have been extracted from '{meeting.title}'.",
                notif_type=NotificationTypeEnum.meeting_tasks_extracted,
                link=f"/meetings?id={id}",
            )

    await db.commit()
    return success_response(data={"count": len(created)}, message=f"{len(created)} task(s) extracted")


@router.put("/{id}/extracted-tasks/{task_id}", response_model=APIResponse)
async def update_extracted_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    task_id: int,
    task_in: ExtractedTaskUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingExtractedTask).filter(
            MeetingExtractedTask.id == task_id,
            MeetingExtractedTask.meeting_id == id,
            MeetingExtractedTask.is_deleted == False,
        )
    )
    task = result.scalars().first()
    if not task:
        return error_response(message="Extracted task not found")

    update_data = task_in.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(task, field, value)

    await db.commit()
    return success_response(data=_serialize_extracted_task(task), message="Extracted task updated")


@router.post("/{id}/extracted-tasks/{task_id}/approve", response_model=APIResponse)
async def approve_extracted_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    task_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingExtractedTask).filter(
            MeetingExtractedTask.id == task_id,
            MeetingExtractedTask.meeting_id == id,
            MeetingExtractedTask.is_deleted == False,
        )
    )
    ext_task = result.scalars().first()
    if not ext_task:
        return error_response(message="Extracted task not found")

    real_task = Task(
        title=ext_task.title,
        description=ext_task.description,
        priority=TaskPriorityEnum(ext_task.priority) if ext_task.priority in [e.value for e in TaskPriorityEnum] else TaskPriorityEnum.medium,
        deadline=ext_task.deadline,
        source="meeting_mom",
        meeting_id=id,
        assigned_by_id=current_user.id,
    )
    db.add(real_task)
    await db.flush()

    ext_task.real_task_id = real_task.id
    ext_task.status = "approved"

    await _add_timeline(
        db, id, "task_approved",
        f"Task '{ext_task.title}' approved and created",
        current_user.id,
    )

    await db.commit()

    return success_response(
        data={"task_id": real_task.id, "title": real_task.title},
        message="Task approved and created",
    )


@router.post("/{id}/extracted-tasks/{task_id}/reject", response_model=APIResponse)
async def reject_extracted_task(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    task_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(MeetingExtractedTask).filter(
            MeetingExtractedTask.id == task_id,
            MeetingExtractedTask.meeting_id == id,
            MeetingExtractedTask.is_deleted == False,
        )
    )
    ext_task = result.scalars().first()
    if not ext_task:
        return error_response(message="Extracted task not found")

    ext_task.status = "rejected"
    await _add_timeline(db, id, "task_rejected", f"Task '{ext_task.title}' rejected", current_user.id)
    await db.commit()
    return success_response(message="Extracted task rejected")


@router.post("/{id}/extracted-tasks/bulk-approve", response_model=APIResponse)
async def bulk_approve_extracted_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    body: BulkApproveRequest,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    created = []
    for task_id in body.task_ids:
        result = await db.execute(
            select(MeetingExtractedTask).filter(
                MeetingExtractedTask.id == task_id,
                MeetingExtractedTask.meeting_id == id,
                MeetingExtractedTask.is_deleted == False,
            )
        )
        ext_task = result.scalars().first()
        if not ext_task or ext_task.status != "pending":
            continue

        real_task = Task(
            title=ext_task.title,
            description=ext_task.description,
            priority=TaskPriorityEnum(ext_task.priority) if ext_task.priority in [e.value for e in TaskPriorityEnum] else TaskPriorityEnum.medium,
            deadline=ext_task.deadline,
            source="meeting_mom",
            meeting_id=id,
            assigned_by_id=current_user.id,
        )
        db.add(real_task)
        await db.flush()
        ext_task.real_task_id = real_task.id
        ext_task.status = "approved"
        created.append(ext_task.title)

    await _add_timeline(
        db, id, "bulk_approved",
        f"{len(created)} task(s) bulk approved",
        current_user.id,
        metadata={"count": len(created)},
    )

    await db.commit()
    return success_response(data={"count": len(created)}, message=f"{len(created)} task(s) approved")


@router.post("/{id}/extracted-tasks/bulk-reject", response_model=APIResponse)
async def bulk_reject_extracted_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    body: BulkApproveRequest,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    count = 0
    for task_id in body.task_ids:
        result = await db.execute(
            select(MeetingExtractedTask).filter(
                MeetingExtractedTask.id == task_id,
                MeetingExtractedTask.meeting_id == id,
                MeetingExtractedTask.is_deleted == False,
            )
        )
        ext_task = result.scalars().first()
        if ext_task and ext_task.status == "pending":
            ext_task.status = "rejected"
            count += 1

    await _add_timeline(db, id, "bulk_rejected", f"{count} task(s) bulk rejected", current_user.id)
    await db.commit()
    return success_response(data={"count": count}, message=f"{count} task(s) rejected")


def _extract_text_from_pdf(file_bytes: bytes) -> str:
    from pypdf import PdfReader
    import io
    reader = PdfReader(io.BytesIO(file_bytes))
    pages = []
    for page in reader.pages:
        text = page.extract_text()
        if text:
            pages.append(text)
    return "\n\n".join(pages)


def _extract_text_from_docx(file_bytes: bytes) -> str:
    from docx import Document
    import io
    doc = Document(io.BytesIO(file_bytes))
    paragraphs = []
    for para in doc.paragraphs:
        if para.text.strip():
            paragraphs.append(para.text)
    return "\n\n".join(paragraphs)


def _extract_text_from_file(filename: str, file_bytes: bytes) -> str:
    lower = filename.lower()
    if lower.endswith(".pdf"):
        return _extract_text_from_pdf(file_bytes)
    elif lower.endswith(".docx"):
        return _extract_text_from_docx(file_bytes)
    elif lower.endswith((".txt", ".md", ".markdown", ".rtf")):
        return file_bytes.decode("utf-8", errors="replace")
    else:
        return file_bytes.decode("utf-8", errors="replace")


@router.post("/{id}/upload-document", response_model=APIResponse)
async def upload_document(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    file_bytes = await file.read()
    if len(file_bytes) > 10 * 1024 * 1024:
        return error_response(message="File too large (max 10MB)")

    try:
        extracted_text = _extract_text_from_file(file.filename or "document.txt", file_bytes)
    except Exception as e:
        return error_response(message=f"Failed to extract text from file: {str(e)[:200]}")

    if not extracted_text.strip():
        return error_response(message="No text content could be extracted from the file")

    meeting.transcript = extracted_text
    meeting.document_filename = file.filename
    meeting.analysis_status = "uploaded"

    await _add_timeline(
        db, id, "document_uploaded",
        f"Document '{file.filename}' uploaded by {current_user.full_name} ({len(extracted_text)} chars extracted)",
        current_user.id,
        metadata={"filename": file.filename, "chars": len(extracted_text)},
    )

    await db.commit()

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data=_serialize_meeting(meeting_loaded),
        message=f"Document uploaded and text extracted successfully ({len(extracted_text)} characters)",
    )


ENHANCED_ANALYSIS_PROMPT = """You are an expert meeting analyst. Analyze the following meeting transcript and extract structured data.
Return ONLY a valid JSON object with exactly these fields (no markdown, no code fences):
{
  "summary": "A 2-3 sentence executive summary of the meeting",
  "key_points": ["key point 1", "key point 2"],
  "participants": ["participant name 1", "participant name 2"],
  "action_items": [
    {
      "title": "task title",
      "description": "detailed description of what needs to be done",
      "priority": "high|medium|low",
      "suggested_owner": "person name mentioned in transcript or null",
      "deadline": "date string if mentioned or null",
      "dependencies": ["dependent task title if any"]
    }
  ],
  "decisions": ["decision 1 that was agreed upon"],
  "risks": ["risk or concern mentioned"],
  "blockers": ["blocker or obstacle mentioned"],
  "followups": [
    {
      "description": "what needs to be followed up",
      "assigned_to": "person name if mentioned or null",
      "deadline": "date string if mentioned or null"
    }
  ],
  "questions": [
    {
      "question": "the question that was asked",
      "asked_by": "person name if mentioned or null",
      "answer": "the answer if provided, or null"
    }
  ]
}

Important rules:
- ONLY extract action items that are EXPLICITLY assigned tasks or direct commitments
- IGNORE discussion points, general observations, opinions, or ideas that were not assigned to anyone
- A true action item must have an explicit owner or clear delegation (e.g. "John will handle this", "Sarah to send the report")
- If something was discussed but no one was assigned, it is NOT an action item
- For suggested_owner, ONLY use the person's name if explicitly mentioned as the assignee
- For priority: high = urgent/critical/blocking, medium = important but not urgent, low = nice to have
- If a section has no items, use an empty array []
- If a field cannot be determined, use null

Meeting Transcript:
"""


@router.post("/{id}/analyze-enhanced", response_model=APIResponse)
async def analyze_meeting_enhanced(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    content = meeting.transcript or meeting.mom_summary
    if not content:
        return error_response(message="No transcript or MOM content available for analysis")

    meeting.analysis_status = "processing"
    await db.commit()

    ai_engine_url = os.getenv("AI_ENGINE_URL", "").strip()
    if not ai_engine_url:
        meeting.analysis_status = "failed"
        await db.commit()
        return error_response(message="AI_ENGINE_URL environment variable is not configured")

    ai_url = ai_engine_url.rstrip("/") + "/api/v1/chat/analyze"

    try:
        async with httpx.AsyncClient(timeout=90.0) as client:
            resp = await client.post(
                ai_url,
                json={
                    "message": ENHANCED_ANALYSIS_PROMPT + content[:15000],
                    "temperature": 0.1,
                    "max_tokens": 4096,
                    "system_prompt": "You are a meeting analysis AI. Return ONLY valid JSON. No markdown, no code fences, no explanation.",
                },
            )
            resp.raise_for_status()
            ai_data = resp.json()
    except Exception:
        meeting.analysis_status = "failed"
        await db.commit()
        return error_response(message="AI engine unavailable for analysis")

    raw_response = ai_data.get("response", "") or ai_data.get("data", {}).get("response", "") or ""

    analysis = None
    try:
        start = raw_response.find("{")
        end = raw_response.rfind("}") + 1
        if start != -1 and end > start:
            analysis = json.loads(raw_response[start:end])
    except (json.JSONDecodeError, ValueError):
        pass

    if not analysis:
        meeting.analysis_status = "failed"
        await db.commit()
        return error_response(message="Failed to parse AI analysis response")

    summary = analysis.get("summary", "")
    key_points = analysis.get("key_points", [])
    participants = analysis.get("participants", [])
    action_items = analysis.get("action_items", [])
    decisions = analysis.get("decisions", [])
    risks = analysis.get("risks", [])
    blockers = analysis.get("blockers", [])
    followups = analysis.get("followups", [])
    questions = analysis.get("questions", [])

    meeting.ai_summary = summary
    meeting.minutes_of_meeting = json.dumps({
        "summary": summary,
        "key_points": key_points,
        "participants": participants,
        "action_items": action_items,
        "decisions": decisions,
        "risks": risks,
        "blockers": blockers,
        "followups": followups,
        "questions": questions,
    })
    meeting.mom_executive_summary = json.dumps(summary)
    meeting.mom_decisions = json.dumps(decisions)
    meeting.mom_action_items = json.dumps(action_items)
    meeting.mom_risks = json.dumps(risks)
    meeting.mom_blockers = json.dumps(blockers)
    meeting.mom_followups = json.dumps([f.get("description", f) if isinstance(f, dict) else f for f in followups])
    meeting.mom_questions = json.dumps([q.get("question", q) if isinstance(q, dict) else q for q in questions])
    meeting.analysis_status = "completed"

    extracted_tasks_data = action_items
    created_tasks = []
    for item in extracted_tasks_data:
        if not isinstance(item, dict) or not item.get("title"):
            continue
        extracted = MeetingExtractedTask(
            meeting_id=id,
            title=item["title"],
            description=item.get("description"),
            priority=item.get("priority", "medium"),
            suggested_owner=item.get("suggested_owner"),
            deadline=item.get("deadline"),
            dependencies=json.dumps(item.get("dependencies", [])) if item.get("dependencies") else None,
            status="pending",
            confidence=80,
        )
        db.add(extracted)
        await db.flush()
        created_tasks.append({
            "id": extracted.id,
            "title": extracted.title,
            "priority": extracted.priority,
            "suggested_owner": extracted.suggested_owner,
        })

    await _add_timeline(
        db, id, "meeting_analyzed_enhanced",
        f"Enhanced AI analysis complete - {len(created_tasks)} tasks, {len(decisions)} decisions, {len(risks)} risks, {len(followups)} followups",
        current_user.id,
        metadata={
            "tasks_extracted": len(created_tasks),
            "decisions": len(decisions),
            "risks": len(risks),
            "followups": len(followups),
            "questions": len(questions),
        },
    )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    res = await db.execute(
        select(Meeting)
        .options(
            selectinload(Meeting.participant_entries).selectinload(MeetingParticipant.user),
            selectinload(Meeting.owner),
        )
        .filter(Meeting.id == id)
    )
    meeting_loaded = res.scalars().first()

    return success_response(
        data={
            **_serialize_meeting(meeting_loaded),
            "extracted_tasks": created_tasks,
            "analysis": {
                "summary": summary,
                "key_points": key_points,
                "participants": participants,
                "action_items": action_items,
                "decisions": decisions,
                "risks": risks,
                "blockers": blockers,
                "followups": followups,
                "questions": questions,
            },
        },
        message="Enhanced meeting analysis complete",
    )


@router.post("/{id}/confirm-tasks", response_model=APIResponse)
async def confirm_and_create_tasks(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    body: TaskConfirmRequest,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    meeting_result = await db.execute(
        select(Meeting)
        .options(selectinload(Meeting.participant_entries))
        .filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = meeting_result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    users_result = await db.execute(select(User))
    all_users = {u.full_name.lower().strip(): u for u in users_result.scalars().all()}

    created = []
    for task_data in body.tasks:
        ext_result = await db.execute(
            select(MeetingExtractedTask).filter(
                MeetingExtractedTask.id == task_data.extracted_task_id,
                MeetingExtractedTask.meeting_id == id,
                MeetingExtractedTask.is_deleted == False,
            )
        )
        ext_task = ext_result.scalars().first()
        if not ext_task or ext_task.status != "pending":
            continue

        assigned_to_id = None
        if task_data.assigned_to_id:
            assigned_to_id = task_data.assigned_to_id
        elif ext_task.suggested_owner:
            owner_name = ext_task.suggested_owner.lower().strip()
            matched_user = all_users.get(owner_name)
            if not matched_user:
                for uname, u in all_users.items():
                    if owner_name in uname or uname in owner_name:
                        matched_user = u
                        break
            if matched_user:
                assigned_to_id = matched_user.id

        real_task = Task(
            title=task_data.title or ext_task.title,
            description=task_data.description or ext_task.description,
            priority=TaskPriorityEnum(task_data.priority) if task_data.priority in [e.value for e in TaskPriorityEnum] else (TaskPriorityEnum(ext_task.priority) if ext_task.priority in [e.value for e in TaskPriorityEnum] else TaskPriorityEnum.medium),
            deadline=task_data.deadline or ext_task.deadline,
            source="meeting",
            meeting_id=id,
            assigned_by_id=current_user.id,
            assigned_to_id=assigned_to_id,
        )
        db.add(real_task)
        await db.flush()

        ext_task.real_task_id = real_task.id
        ext_task.status = "approved"

        if assigned_to_id and assigned_to_id != current_user.id:
            await _create_notification(
                db, assigned_to_id,
                title=f"Task assigned from meeting: {meeting.title}",
                message=f"You have been assigned a task from '{meeting.title}': {real_task.title}",
                notif_type=NotificationTypeEnum.meeting_task_assigned,
                link=f"/tasks",
            )

        created.append({
            "task_id": real_task.id,
            "title": real_task.title,
            "assigned_to_id": assigned_to_id,
        })

    if created:
        await _add_timeline(
            db, id, "tasks_confirmed",
            f"{len(created)} task(s) confirmed and created from meeting",
            current_user.id,
            metadata={"count": len(created), "tasks": [c["title"] for c in created]},
        )

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_meeting(id)
        await db.commit()
    except Exception:
        pass

    return success_response(data={"count": len(created), "tasks": created}, message=f"{len(created)} task(s) created and assigned")


@router.get("/{id}/structured-data", response_model=APIResponse)
async def get_structured_data(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Meeting).filter(Meeting.id == id, Meeting.is_deleted == False)
    )
    meeting = result.scalars().first()
    if not meeting:
        return error_response(message="Meeting not found")

    return success_response(
        data={
            "meeting_id": meeting.id,
            "decisions": _parse_json_list(meeting.mom_decisions),
            "risks": _parse_json_list(meeting.mom_risks),
            "blockers": _parse_json_list(meeting.mom_blockers),
            "followups": _parse_json_list(meeting.mom_followups),
            "questions": _parse_json_list(meeting.mom_questions),
            "deadlines": _parse_json_list(meeting.mom_deadlines),
            "important_dates": _parse_json_list(meeting.mom_important_dates),
        },
        message="Structured data fetched successfully",
    )
