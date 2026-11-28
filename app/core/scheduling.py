from datetime import datetime
from typing import Optional, Tuple
from sqlalchemy import or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload


def parse_time(time_str: str) -> datetime.time:
    """Parses a time string in HH:MM format."""
    return datetime.strptime(time_str, "%H:%M").time()


def is_overlapping(start1: str, end1: str, start2: str, end2: str) -> bool:
    """
    Checks if two time intervals overlap.
    Standard exclusive-end condition: start1 < end2 AND end1 > start2.
    Assumes all inputs are "HH:MM" strings.
    """
    if not start1 or not end1 or not start2 or not end2:
        return False

    s1 = parse_time(start1)
    e1 = parse_time(end1)
    s2 = parse_time(start2)
    e2 = parse_time(end2)

    return s1 < e2 and e1 > s2


def format_conflict(conflict_type: str, item_id: int, source: str, title: str, start_time: str, end_time: str, mask_title: bool = False):
    return {
        "type": conflict_type,
        "id": item_id,
        "source": source,
        "title": "Busy" if mask_title else title,
        "start_time": start_time,
        "end_time": end_time,
    }


STANDARD_ONE_HOUR_SLOTS: Tuple[Tuple[str, str], ...] = (
    ("09:00", "10:00"),
    ("10:00", "11:00"),
    ("11:00", "12:00"),
    ("12:00", "13:00"),
    ("13:00", "14:00"),
    ("14:00", "15:00"),
    ("15:00", "16:00"),
    ("16:00", "17:00"),
    ("17:00", "18:00"),
)


def _normalize_hhmm(raw: Optional[str]) -> Optional[str]:
    if not raw:
        return None
    s = str(raw).strip()
    if len(s) >= 5 and ":" in s:
        hhmm = s[:5]
        try:
            parse_time(hhmm)
            return hhmm
        except ValueError:
            return None
    return None


async def find_next_free_slot(
    db: AsyncSession,
    user_id: Optional[int],
    target_date: Optional[str],
) -> Tuple[str, str]:
    """
    Find the first non-overlapping 1-hour slot (09:00..18:00) on target_date
    for user_id, skipping both active scheduled tasks and non-cancelled meetings.
    """
    if not user_id or not target_date:
        return ("09:00", "10:00")

    day_str = str(target_date)[:10]
    from app.models.task import Task, TaskStatusEnum
    from app.models.meeting import Meeting, MeetingStatusEnum

    occupied: list[Tuple[str, str]] = []

    task_rows = (
        await db.execute(
            select(Task).filter(
                Task.is_deleted == False,
                Task.assigned_to_id == user_id,
                Task.status != TaskStatusEnum.completed,
                or_(Task.scheduled_date == day_str, Task.deadline == day_str),
            )
        )
    ).scalars().all()
    for t in task_rows:
        st = _normalize_hhmm(t.scheduled_start_time)
        et = _normalize_hhmm(t.scheduled_end_time)
        if st and not et:
            h, m = map(int, st.split(":"))
            et = f"{min(h + 1, 23):02d}:{m:02d}"
        if st and et:
            occupied.append((st, et))

    meeting_rows = (
        await db.execute(
            select(Meeting)
            .options(selectinload(Meeting.participant_entries))
            .filter(
                Meeting.is_deleted == False,
                Meeting.date == day_str,
                Meeting.status.notin_([MeetingStatusEnum.cancelled, MeetingStatusEnum.completed]),
            )
        )
    ).scalars().all()
    for m in meeting_rows:
        is_member = m.owner_id == user_id or any(
            p.user_id == user_id and (getattr(p.status, "value", p.status) != "declined")
            for p in (m.participant_entries or [])
        )
        if not is_member:
            continue
        st = _normalize_hhmm(getattr(m, "start_time", None))
        et = _normalize_hhmm(getattr(m, "end_time", None))
        if st and not et:
            h, mm = map(int, st.split(":"))
            et = f"{min(h + 1, 23):02d}:{mm:02d}"
        if st and et:
            occupied.append((st, et))

    for slot_start, slot_end in STANDARD_ONE_HOUR_SLOTS:
        if not any(is_overlapping(slot_start, slot_end, occ_s, occ_e) for occ_s, occ_e in occupied):
            return (slot_start, slot_end)

    return ("09:00", "10:00")
