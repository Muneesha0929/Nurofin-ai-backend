"""My meetings: everything on the signed-in person's calendar for the next days, in one list.

Merges three sources so the Overview, the sidebar badge and the meeting reminder all agree:
- meetings created in Chief of Staff that the person owns, created or is invited to,
- their Google Calendar, if connected,
- their Outlook calendar, if connected (it was connected but never read before this).

Times come back as ISO datetimes with an offset. Chief of Staff meetings store a local date and
"HH:MM", read as India time unless the meeting has its own timezone. A calendar that fails to load
does not fail the list: it is reported in `problems` so the page can say "Reconnect Outlook".
Results are cached per person for a short while, because the Overview, the sidebar and the
reminder all ask for them and the calendar APIs are slow.
"""
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.meeting import Meeting, MeetingParticipant
from app.models.user import User

router = APIRouter()

IST = timezone(timedelta(hours=5, minutes=30))
CACHE_SECONDS = 120
_cache: Dict[tuple, tuple] = {}


def _tz(name: Optional[str]):
    if not name:
        return IST
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return IST


def _local(d: Optional[str], t: Optional[str], tz) -> Optional[datetime]:
    if not d:
        return None
    try:
        day = date.fromisoformat(str(d)[:10])
    except ValueError:
        return None
    hh, mm = 0, 0
    if t:
        try:
            parts = str(t).split(":")
            hh, mm = int(parts[0]), int(parts[1]) if len(parts) > 1 else 0
        except (ValueError, IndexError):
            pass
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=tz)


def _parse_iso(v: Optional[str], tz_default=timezone.utc) -> Optional[datetime]:
    if not v:
        return None
    try:
        if len(v) == 10:  # all-day: a date only
            d = date.fromisoformat(v)
            return datetime(d.year, d.month, d.day, tzinfo=IST)
        dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=tz_default)
    except ValueError:
        return None


async def nurofin_meetings(db: AsyncSession, user: User, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    invited = select(MeetingParticipant.meeting_id).filter(MeetingParticipant.user_id == user.id)
    q = select(Meeting).filter(
        Meeting.is_deleted == False,
        or_(Meeting.owner_id == user.id, Meeting.created_by_id == user.id, Meeting.id.in_(invited)),
        Meeting.date >= (start - timedelta(days=1)).date().isoformat(),
        Meeting.date <= end.date().isoformat(),
    )
    out = []
    for m in (await db.execute(q)).scalars().all():
        status = m.status.value if hasattr(m.status, "value") else (m.status or "scheduled")
        if status == "cancelled":
            continue
        tz = _tz(m.timezone)
        s = _local(m.date, m.start_time, tz)
        e = _local(m.date, m.end_time, tz) or (s + timedelta(minutes=30) if s else None)
        if not s or e < start or s > end:
            continue
        out.append({"id": f"cos-{m.id}", "meeting_id": m.id, "source": "nurofin", "title": m.title,
                    "start": s.isoformat(), "end": e.isoformat(), "all_day": not m.start_time,
                    "join_url": m.meeting_link or None, "location": m.location or None, "open_url": f"/meetings?id={m.id}"})
    return out


def google_meetings(user: User, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    from app.services.google_calendar import fetch_calendar_events
    out = []
    for item in fetch_calendar_events(user, start, end):
        if item.get("status") == "cancelled":
            continue
        s = _parse_iso(item.get("start", {}).get("dateTime") or item.get("start", {}).get("date"))
        e = _parse_iso(item.get("end", {}).get("dateTime") or item.get("end", {}).get("date"))
        if not s:
            continue
        video = next((p.get("uri") for p in (item.get("conferenceData") or {}).get("entryPoints", []) if p.get("entryPointType") == "video"), None)
        out.append({"id": f"g-{item.get('id')}", "source": "google", "title": item.get("summary") or "Busy",
                    "start": s.isoformat(), "end": (e or s + timedelta(minutes=30)).isoformat(),
                    "all_day": "date" in item.get("start", {}) and "dateTime" not in item.get("start", {}),
                    "join_url": item.get("hangoutLink") or video, "location": item.get("location"), "open_url": item.get("htmlLink")})
    return out


def outlook_meetings(user: User, start: datetime, end: datetime) -> List[Dict[str, Any]]:
    from app.services.outlook_calendar import fetch_calendar_events as fetch_outlook
    out = []
    for item in fetch_outlook(user, start, end, extra_fields="isAllDay,isCancelled,isOnlineMeeting,onlineMeeting,onlineMeetingUrl"):
        if item.get("isCancelled") or item.get("showAs") == "free":
            continue
        # asked for UTC with the Prefer header; Graph returns no offset, so add it
        s = _parse_iso((item.get("start") or {}).get("dateTime"), timezone.utc)
        e = _parse_iso((item.get("end") or {}).get("dateTime"), timezone.utc)
        if not s:
            continue
        join = ((item.get("onlineMeeting") or {}).get("joinUrl")) or item.get("onlineMeetingUrl")
        out.append({"id": f"o-{item.get('id', '')[-24:]}", "source": "outlook", "title": item.get("subject") or "Busy",
                    "start": s.isoformat(), "end": (e or s + timedelta(minutes=30)).isoformat(), "all_day": bool(item.get("isAllDay")),
                    "join_url": join, "location": (item.get("location") or {}).get("displayName") or None, "open_url": item.get("webLink")})
    return out


@router.get("", response_model=APIResponse)
async def my_meetings(
    days: int = Query(14, ge=1, le=60),
    fresh: bool = False,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    key = (current_user.id, days)
    hit = _cache.get(key)
    if hit and not fresh and time.time() - hit[0] < CACHE_SECONDS:
        return success_response(data=hit[1])

    now = datetime.now(IST)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end = start + timedelta(days=days)
    items = await nurofin_meetings(db, current_user, start, end)
    problems = []
    for name, connected, fn in (("Google Calendar", current_user.google_refresh_token, google_meetings),
                                ("Outlook", current_user.outlook_refresh_token, outlook_meetings)):
        if not connected:
            continue
        try:
            items += fn(current_user, start, end)
        except Exception as e:  # one calendar failing must not hide the others
            problems.append({"calendar": name, "message": f"{name} could not be read. Reconnect it from the Operations Calendar.", "detail": str(e)[:200]})
    await db.commit()  # refreshed access tokens are saved on the user

    # the same meeting from two places (made here and synced to Google) shows once, ours first
    seen, merged = set(), []
    for it in sorted(items, key=lambda x: (x["start"], {"nurofin": 0, "google": 1, "outlook": 2}[x["source"]])):
        k = (it["title"].strip().lower(), it["start"][:16])
        if k in seen:
            continue
        seen.add(k)
        merged.append(it)

    data = {
        "now": now.isoformat(),
        "today": start.date().isoformat(),
        "meetings": merged,
        "connected": {"google": bool(current_user.google_refresh_token), "outlook": bool(current_user.outlook_refresh_token)},
        "problems": problems,
    }
    _cache[key] = (time.time(), data)
    return success_response(data=data)
