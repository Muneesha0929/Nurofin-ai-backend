"""Server-to-server intake from Build-Track ("Report a problem"). Contract sections 2 and 6.

Not a user-facing endpoint: no login, instead an HMAC-SHA256 signature over the raw request body made with a
shared secret (BUILDTRACK_INTEGRATION_SECRET), plus a 5-minute timestamp window against replays.
"""
import base64
import binascii
import hashlib
import hmac
import logging
import re
import time
from datetime import datetime, timezone
from typing import Annotated, List, Literal, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError, field_validator
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.issue import Issue, IssueTypeEnum, IssuePriorityEnum
from app.models.issue_external import CosIssueExternal
from app.models.notification import Notification, NotificationTypeEnum
from app.models.project import project_members
from app.models.user import User
from app.services.issue_assignment import auto_assign_fields
from app.services.issue_external import (
    discard_screenshot, resolve_buildtrack_project_id, store_screenshot,
)

logger = logging.getLogger(__name__)
router = APIRouter()

MAX_BODY_BYTES = 4 * 1024 * 1024          # 2 MB image as base64 is ~2.8 MB, plus the rest of the report
MAX_SCREENSHOT_BYTES = 2 * 1024 * 1024
TIMESTAMP_WINDOW_SECONDS = 300
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
JPEG_MAGIC = b"\xff\xd8\xff"


def _fail(status: int, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"success": False, "message": message})


# --- payload ------------------------------------------------------------------------------------

Str = Annotated[str, StringConstraints(strip_whitespace=True)]


class _Screen(BaseModel):
    model_config = ConfigDict(extra="ignore")
    route: str = Field("", max_length=300)
    name: str = Field("", max_length=120)


class _Crumb(BaseModel):
    model_config = ConfigDict(extra="ignore")
    at: str = Field("", max_length=40)
    kind: str = Field("", max_length=20)
    label: str = Field("", max_length=120)
    detail: Optional[str] = Field(None, max_length=200)


class _Device(BaseModel):
    model_config = ConfigDict(extra="ignore")
    platform: Optional[str] = Field(None, max_length=20)
    osVersion: Optional[str] = Field(None, max_length=80)
    model: Optional[str] = Field(None, max_length=120)
    appVersion: Optional[str] = Field(None, max_length=40)
    buildNumber: Optional[str] = Field(None, max_length=40)
    locale: Optional[str] = Field(None, max_length=40)
    userAgent: Optional[str] = Field(None, max_length=500)
    viewport: Optional[str] = Field(None, max_length=40)


class _Reporter(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = Field(None, max_length=80)
    name: Optional[str] = Field(None, max_length=200)
    email: Optional[str] = Field(None, max_length=200)
    phone: Optional[str] = Field(None, max_length=60)
    role: Optional[str] = Field(None, max_length=80)
    company: Optional[str] = Field(None, max_length=200)


class _Project(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: Optional[str] = Field(None, max_length=80)
    name: Optional[str] = Field(None, max_length=200)


class _Screenshot(BaseModel):
    model_config = ConfigDict(extra="ignore")
    mime: Literal["image/png", "image/jpeg"]
    base64: str = Field(..., max_length=int(MAX_SCREENSHOT_BYTES * 4 / 3) + 1024)


class BuildTrackIssueIn(BaseModel):
    model_config = ConfigDict(extra="ignore")
    external_id: Str = Field(..., min_length=1, max_length=80)
    client_report_id: Optional[str] = Field(None, max_length=80)
    source: Literal["mobile", "web"]
    description: Str = Field(..., min_length=1, max_length=2000)
    trying_to: Optional[str] = Field(None, max_length=500)
    screen: _Screen = _Screen()
    breadcrumbs: List[_Crumb] = Field(default_factory=list, max_length=30)
    device: _Device = _Device()
    reporter: _Reporter = _Reporter()
    project: Optional[_Project] = None
    occurred_at: Optional[datetime] = None
    received_at: Optional[datetime] = None
    screenshot: Optional[_Screenshot] = None
    callback_url: Optional[str] = Field(None, max_length=1000)

    @field_validator("screen", "device", "reporter", mode="before")
    @classmethod
    def _none_is_empty(cls, v):
        return {} if v is None else v

    @field_validator("callback_url")
    @classmethod
    def _callback_url(cls, v):
        if v is not None and not re.match(r"^https?://", v):
            raise ValueError("callback_url must be an http(s) URL")
        return v


def _decode_screenshot(shot: _Screenshot) -> bytes:
    try:
        raw = base64.b64decode(re.sub(r"\s+", "", shot.base64), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("screenshot.base64 is not valid base64")
    if not raw:
        raise ValueError("screenshot is empty")
    if len(raw) > MAX_SCREENSHOT_BYTES:
        raise ValueError("screenshot is larger than 2 MB")
    is_png = raw.startswith(PNG_MAGIC)
    is_jpeg = raw.startswith(JPEG_MAGIC)
    if shot.mime == "image/png" and not is_png or shot.mime == "image/jpeg" and not is_jpeg:
        raise ValueError("screenshot content does not match its declared mime type (png/jpeg only)")
    return raw


def _naive_utc(d: Optional[datetime]) -> Optional[datetime]:
    if d is None:
        return None
    return d.astimezone(timezone.utc).replace(tzinfo=None) if d.tzinfo else d


# --- auth ---------------------------------------------------------------------------------------

def _verify_signature(secret: str, timestamp: str, signature: str, body: bytes) -> bool:
    expected = hmac.new(secret.encode("utf-8"), timestamp.encode("ascii", "ignore") + b"." + body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected.encode("ascii"), (signature or "").strip().lower().encode("utf-8", "ignore"))


# --- notifications ------------------------------------------------------------------------------

def _alert_text(payload: BuildTrackIssueIn) -> str:
    rep = payload.reporter
    who = (rep.name or "").strip() or "Someone"
    bits = [b for b in ((rep.company or "").strip(), (rep.role or "").strip()) if b]
    ctx = f" ({', '.join(bits)})" if bits else ""
    screen = (payload.screen.name or payload.screen.route or "").strip() or "an unknown screen"
    return f"{who}{ctx} reported a problem on {screen} in Build-Track — please rectify. {payload.external_id}"


async def _add_notifications(db: AsyncSession, issue: Issue, payload: BuildTrackIssueIn, assignee_id: Optional[int]) -> None:
    link = f"/issues?id={issue.id}"
    notified = set()
    if assignee_id:
        db.add(Notification(
            title="New issue assigned to you (Action Required)",
            message=f"{(payload.reporter.name or '').strip() or 'Someone'} reported: {issue.title}. Please accept or decline within 1 hour.",
            type=NotificationTypeEnum.task_assigned,
            user_id=assignee_id,
            link=link,
        ))
        notified.add(assignee_id)

    # The tech team (members of the Build-Track project) gets the alert. The Director / super admins are
    # only alerted as a fallback when no project or no members are set up, so reports never go unseen
    # but Sir's bell isn't flooded with every report.
    recipients = {}
    if issue.project_id:
        members = (await db.execute(
            select(User).join(project_members, project_members.c.user_id == User.id)
            .where(project_members.c.project_id == issue.project_id, User.is_deleted == False)
        )).scalars().all()
        for u in members:
            recipients[u.id] = u
    if not recipients and not notified:
        leaders = (await db.execute(select(User).where(User.is_deleted == False))).scalars().all()
        for u in leaders:
            role = (u.role.value if hasattr(u.role, "value") else (u.role or "")).lower()
            if role in ("ceo", "super_admin"):
                recipients[u.id] = u

    text = _alert_text(payload)
    for uid, u in recipients.items():
        if uid in notified or getattr(u, "is_active", True) is False:
            continue
        notified.add(uid)
        db.add(Notification(
            title="Build-Track: problem reported",
            message=text,
            type=NotificationTypeEnum.project_update,
            user_id=uid,
            link=link,
        ))


# --- endpoint -----------------------------------------------------------------------------------

@router.post("/build-track/issues")
async def build_track_issue_intake(request: Request):
    secret = settings.BUILDTRACK_INTEGRATION_SECRET
    if not secret:
        return _fail(503, "Build-Track integration is not configured")

    ts = request.headers.get("x-bt-timestamp", "")
    sig = request.headers.get("x-bt-signature", "")
    if not ts or not sig:
        return _fail(401, "Missing signature")
    try:
        ts_int = int(ts)
    except ValueError:
        return _fail(401, "Invalid signature")
    if abs(time.time() - ts_int) > TIMESTAMP_WINDOW_SECONDS:
        return _fail(401, "Invalid signature")

    try:
        declared = int(request.headers.get("content-length") or 0)
    except ValueError:
        declared = 0
    if declared > MAX_BODY_BYTES:
        return _fail(413, "Request body is too large")
    body = await request.body()            # the raw bytes: the signature is over exactly these
    if not _verify_signature(secret, ts, sig, body):
        return _fail(401, "Invalid signature")
    if len(body) > MAX_BODY_BYTES:
        return _fail(413, "Request body is too large")

    try:
        payload = BuildTrackIssueIn.model_validate_json(body)
        shot_bytes = _decode_screenshot(payload.screenshot) if payload.screenshot else None
    except ValidationError as e:
        err = e.errors()[0]
        loc = ".".join(str(p) for p in err.get("loc", ()))
        return _fail(400, f"{loc}: {err.get('msg', 'invalid')}" if loc else str(err.get("msg", "invalid payload")))
    except ValueError as e:
        return _fail(400, str(e))

    async with SessionLocal() as db:
        existing = (await db.execute(
            select(CosIssueExternal).where(CosIssueExternal.external_id == payload.external_id))).scalars().first()
        if existing:
            return JSONResponse(status_code=200, content={"success": True, "data": {"issue_id": existing.issue_id, "duplicate": True}})

        screenshot_key = None
        if shot_bytes:
            try:
                screenshot_key = await store_screenshot(shot_bytes, payload.external_id, payload.screenshot.mime)
            except Exception:
                logger.exception("could not store Build-Track screenshot for %s", payload.external_id)
                return _fail(503, "Could not store the screenshot; try again")

        try:
            description = payload.description
            if payload.trying_to and payload.trying_to.strip():
                description += f"\n\nTrying to: {payload.trying_to.strip()}"
            title = "Build-Track: " + re.sub(r"\s+", " ", payload.description)[:80]

            project_id = await resolve_buildtrack_project_id(db)
            fields = await auto_assign_fields(db, project_id)
            issue = Issue(
                title=title, description=description, category="Build-Track",
                issue_type=IssueTypeEnum.bug, priority=IssuePriorityEnum.medium,
                project_id=project_id, reported_by_id=None, **fields,
            )
            db.add(issue)
            await db.flush()

            received = _naive_utc(payload.received_at) or datetime.utcnow()
            db.add(CosIssueExternal(
                issue_id=issue.id, source="build_track", external_id=payload.external_id,
                client_report_id=payload.client_report_id,
                reporter=payload.reporter.model_dump(), screen=payload.screen.model_dump(),
                breadcrumbs=[c.model_dump() for c in payload.breadcrumbs],
                device=payload.device.model_dump(exclude_none=True),
                project=payload.project.model_dump() if payload.project else None,
                occurred_at=_naive_utc(payload.occurred_at) or received, received_at=received,
                screenshot_key=screenshot_key, screenshot_mime=payload.screenshot.mime if payload.screenshot else None,
                callback_url=payload.callback_url,
            ))
            await _add_notifications(db, issue, payload, fields.get("assigned_user_id"))
            await db.commit()
            return JSONResponse(status_code=201, content={"success": True, "data": {"issue_id": issue.id, "duplicate": False}})
        except IntegrityError:
            # Two deliveries of the same report raced: the other one won; answer with its issue.
            await db.rollback()
            await discard_screenshot(screenshot_key)
            existing = (await db.execute(
                select(CosIssueExternal).where(CosIssueExternal.external_id == payload.external_id))).scalars().first()
            if existing:
                return JSONResponse(status_code=200, content={"success": True, "data": {"issue_id": existing.issue_id, "duplicate": True}})
            logger.exception("Build-Track intake integrity error for %s", payload.external_id)
            return _fail(500, "Could not save the report")
        except Exception:
            await db.rollback()
            await discard_screenshot(screenshot_key)
            logger.exception("Build-Track intake failed for %s", payload.external_id)
            return _fail(500, "Could not save the report")
