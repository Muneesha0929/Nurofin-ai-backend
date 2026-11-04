"""My meetings: Google and Outlook events come back in one shape, with times and join links right.

The calendar APIs are replaced by sample payloads in the shapes they really return.
Run: python -m pytest tests/test_my_meetings.py -q
"""
from datetime import datetime, timedelta, timezone

import app.services.google_calendar as gc
import app.services.outlook_calendar as oc
from app.api.v1.endpoints import my_meetings as mm

IST = timezone(timedelta(hours=5, minutes=30))
START = datetime(2026, 11, 4, tzinfo=IST)
END = START + timedelta(days=14)


def test_google_events(monkeypatch):
    monkeypatch.setattr(gc, "fetch_calendar_events", lambda u, a, b: [
        {"id": "g1", "summary": "Client call", "start": {"dateTime": "2026-11-04T15:00:00+05:30"}, "end": {"dateTime": "2026-11-04T15:30:00+05:30"},
         "hangoutLink": "https://meet.google.com/abc", "htmlLink": "https://calendar.google.com/e/1"},
        {"id": "g2", "summary": "Holiday", "start": {"date": "2026-11-05"}, "end": {"date": "2026-11-06"}},
        {"id": "g3", "summary": "Cancelled one", "status": "cancelled", "start": {"dateTime": "2026-11-04T10:00:00Z"}, "end": {"dateTime": "2026-11-04T11:00:00Z"}},
        {"id": "g4", "summary": "Zoom review", "start": {"dateTime": "2026-11-06T09:30:00Z"}, "end": {"dateTime": "2026-11-06T10:00:00Z"},
         "conferenceData": {"entryPoints": [{"entryPointType": "video", "uri": "https://zoom.us/j/1"}]}},
    ])
    out = mm.google_meetings(object(), START, END)
    assert [m["title"] for m in out] == ["Client call", "Holiday", "Zoom review"]
    assert out[0]["join_url"] == "https://meet.google.com/abc" and out[0]["start"].startswith("2026-11-04T15:00")
    assert out[1]["all_day"] is True
    assert out[2]["join_url"] == "https://zoom.us/j/1" and datetime.fromisoformat(out[2]["start"]).astimezone(IST).strftime("%H:%M") == "15:00"


def test_outlook_events_are_utc_and_get_join_links(monkeypatch):
    monkeypatch.setattr(oc, "fetch_calendar_events", lambda u, a, b, extra_fields="": [
        {"id": "AAMk" + "x" * 40, "subject": "Board meeting", "start": {"dateTime": "2026-11-04T09:30:00.0000000", "timeZone": "UTC"},
         "end": {"dateTime": "2026-11-04T10:30:00.0000000", "timeZone": "UTC"}, "onlineMeeting": {"joinUrl": "https://teams.microsoft.com/l/1"},
         "location": {"displayName": "Room 2"}, "webLink": "https://outlook.office.com/e/1", "showAs": "busy"},
        {"id": "b", "subject": "Cancelled", "isCancelled": True, "start": {"dateTime": "2026-11-04T09:30:00"}, "end": {"dateTime": "2026-11-04T10:00:00"}},
        {"id": "c", "subject": "Free slot", "showAs": "free", "start": {"dateTime": "2026-11-04T11:00:00"}, "end": {"dateTime": "2026-11-04T12:00:00"}},
    ])
    out = mm.outlook_meetings(object(), START, END)
    assert len(out) == 1
    b = out[0]
    assert b["title"] == "Board meeting" and b["join_url"] == "https://teams.microsoft.com/l/1" and b["location"] == "Room 2"
    assert datetime.fromisoformat(b["start"]).astimezone(IST).strftime("%H:%M") == "15:00"   # 09:30 UTC is 3 pm in India


def test_local_meeting_times_read_as_india_time():
    s = mm._local("2026-11-04", "15:00", mm.IST)
    assert s.isoformat() == "2026-11-04T15:00:00+05:30"
    assert mm._local("2026-11-04", None, mm.IST).hour == 0
    assert mm._local("not a date", "10:00", mm.IST) is None
