from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from datetime import datetime, timedelta, timezone
import os

from app.api import deps
from app.models.user import User
from app.models.meeting import Meeting, MeetingParticipant
from app.models.task import Task
from app.core.responses import APIResponse, success_response, error_response
from app.services.google_calendar import get_google_auth_url, exchange_code_for_tokens, fetch_calendar_events

router = APIRouter()


@router.get("/users", response_model=APIResponse)
async def get_planner_users(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Get all active users for the planner sidebar."""
    result = await db.execute(
        select(User).filter(User.is_active == True, User.is_deleted == False)
    )
    users = result.scalars().all()
    return success_response(
        data=[
            {
                "id": u.id,
                "full_name": u.full_name,
                "email": u.email,
                "role": u.role,
                "department": u.department,
                "profile_picture": u.profile_picture,
                "google_connected": bool(u.google_access_token),
            }
            for u in users
        ],
        message="Users fetched successfully"
    )


@router.get("/google/login", response_model=APIResponse)
async def login_google(
    redirect_uri: str = Query(default=None),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Get the Google OAuth login URL for the current user."""
    if not redirect_uri:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        redirect_uri = f"{frontend_url}/planner/google/callback"
    auth_url = get_google_auth_url(redirect_uri)
    return success_response(data={"auth_url": auth_url}, message="Google Auth URL generated")


@router.post("/google/callback", response_model=APIResponse)
async def google_callback(
    code: str = Query(...),
    redirect_uri: str = Query(default=None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Exchange code for tokens and save them to the user."""
    if not redirect_uri:
        frontend_url = os.getenv("FRONTEND_URL", "http://localhost:3000")
        redirect_uri = f"{frontend_url}/planner/google/callback"
    try:
        tokens = exchange_code_for_tokens(code, redirect_uri)

        current_user.google_access_token = tokens["access_token"]
        current_user.google_refresh_token = tokens["refresh_token"]
        current_user.google_token_expires_at = tokens["expires_at"]

        await db.commit()
        await db.refresh(current_user)
        return success_response(data=None, message="Google Calendar connected successfully")
    except Exception as e:
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=400, detail=f"Failed to connect Google Calendar: {str(e)}")


@router.post("/google/disconnect", response_model=APIResponse)
async def disconnect_google(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Disconnect Google Calendar from the current user."""
    current_user.google_access_token = None
    current_user.google_refresh_token = None
    current_user.google_token_expires_at = None
    await db.commit()
    return success_response(data=None, message="Google Calendar disconnected")


@router.get("/schedule/{target_user_id}", response_model=APIResponse)
async def get_user_schedule(
    target_user_id: int,
    start_date: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    end_date: Optional[str] = Query(default=None, description="YYYY-MM-DD"),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Get a read-only schedule for the target user (local meetings + Google calendar)."""

    result = await db.execute(select(User).filter(User.id == target_user_id))
    target_user = result.scalars().first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if start_date:
        time_min = datetime.fromisoformat(start_date + "T00:00:00+00:00")
    else:
        time_min = datetime.now(timezone.utc)

    if end_date:
        time_max = datetime.fromisoformat(end_date + "T23:59:59+00:00")
    else:
        time_max = time_min + timedelta(days=7)

    schedule = []

    local_meetings_query = (
        select(Meeting)
        .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
        .filter(MeetingParticipant.user_id == target_user.id)
        .filter(Meeting.is_deleted == False)
    )
    if start_date:
        local_meetings_query = local_meetings_query.filter(Meeting.date >= start_date)
    if end_date:
        local_meetings_query = local_meetings_query.filter(Meeting.date <= end_date)

    local_meetings_result = await db.execute(local_meetings_query)
    local_meetings = local_meetings_result.scalars().all()

    for m in local_meetings:
        schedule.append({
            "source": "nurofin",
            "title": m.title,
            "description": m.description or "",
            "date": m.date,
            "start_time": m.start_time,
            "end_time": m.end_time,
            "type": m.type.value if m.type else "meeting",
            "status": m.status.value if m.status else "scheduled",
            "read_only": True
        })

    # Fetch Tasks for the target user
    tasks_query = (
        select(Task)
        .filter(Task.assigned_to_id == target_user.id)
        .filter(Task.status != "completed")
    )
    if start_date:
        tasks_query = tasks_query.filter(Task.deadline >= start_date)
    if end_date:
        tasks_query = tasks_query.filter(Task.deadline <= end_date)

    tasks_result = await db.execute(tasks_query)
    tasks = tasks_result.scalars().all()

    for t in tasks:
        task_date = t.scheduled_date or t.deadline
        schedule.append({
            "id": t.id,
            "source": "nurofin_task",
            "title": t.title,
            "description": t.description or "",
            "date": task_date,
            "start_time": t.scheduled_start_time or "09:00",
            "end_time": t.scheduled_end_time or "10:00",
            "type": "task",
            "status": t.status.value if t.status else "todo",
            "read_only": False
        })

    if target_user.google_access_token and target_user.google_refresh_token:
        try:
            google_events = fetch_calendar_events(target_user, time_min, time_max)
            for item in google_events:
                start = item['start'].get('dateTime', item['start'].get('date'))
                end = item['end'].get('dateTime', item['end'].get('date'))
                schedule.append({
                    "source": "google_calendar",
                    "title": item.get('summary', 'Busy'),
                    "description": item.get('description', ''),
                    "start": start,
                    "end": end,
                    "type": "google_event",
                    "status": "scheduled",
                    "read_only": True
                })
        except Exception as e:
            print(f"Failed to fetch Google Calendar for user {target_user_id}: {e}")

    def get_sort_key(x):
        dt = x.get("start") or x.get("date")
        if isinstance(dt, datetime):
            return dt.isoformat()
        if hasattr(dt, 'isoformat'): # date object
            return dt.isoformat()
        return str(dt) if dt else ""
        
    schedule.sort(key=get_sort_key, reverse=False)

    return success_response(
        data={
            "user": {
                "id": target_user.id,
                "full_name": target_user.full_name,
                "google_connected": bool(target_user.google_access_token),
            },
            "schedule": schedule
        },
        message="Schedule fetched successfully"
    )


@router.get("/check-availability", response_model=APIResponse)
async def check_availability(
    user_ids: str = Query(description="Comma-separated user IDs"),
    date: str = Query(description="YYYY-MM-DD"),
    start_time: Optional[str] = Query(default=None),
    end_time: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Check availability of multiple users for a given date. Used for AI scheduling."""
    ids = [int(uid.strip()) for uid in user_ids.split(",") if uid.strip()]
    time_min = datetime.fromisoformat(date + "T00:00:00+00:00")
    time_max = datetime.fromisoformat(date + "T23:59:59+00:00")

    busy_blocks = []

    for uid in ids:
        user_result = await db.execute(select(User).filter(User.id == uid))
        user = user_result.scalars().first()
        if not user:
            continue

        local_query = (
            select(Meeting)
            .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
            .filter(MeetingParticipant.user_id == uid)
            .filter(Meeting.is_deleted == False)
            .filter(Meeting.date == date)
        )
        local_result = await db.execute(local_query)
        local_meetings = local_result.scalars().all()

        for m in local_meetings:
            busy_blocks.append({
                "user_id": uid,
                "user_name": user.full_name,
                "source": "nurofin",
                "title": m.title,
                "start_time": m.start_time,
                "end_time": m.end_time,
            })

        if user.google_access_token and user.google_refresh_token:
            try:
                google_events = fetch_calendar_events(user, time_min, time_max)
                for item in google_events:
                    start_dt = item['start'].get('dateTime', item['start'].get('date'))
                    end_dt = item['end'].get('dateTime', item['end'].get('date'))
                    busy_blocks.append({
                        "user_id": uid,
                        "user_name": user.full_name,
                        "source": "google_calendar",
                        "title": item.get('summary', 'Busy'),
                        "start": start_dt,
                        "end": end_dt,
                    })
            except Exception:
                pass

    return success_response(
        data={"date": date, "busy_blocks": busy_blocks},
        message="Availability checked successfully"
    )
