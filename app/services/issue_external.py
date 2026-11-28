"""Issues that were raised from outside the Issue Center (today: Build-Track "Report a problem").

Visibility, serialising and screenshot storage for the cos_issue_external side-table.
"""
import asyncio
import io
import logging
import os
import re
from typing import Optional

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.issue import Issue
from app.models.issue_external import CosIssueExternal
from app.models.project import Project, project_members
from app.models.user import User

logger = logging.getLogger(__name__)

PRIVILEGED_ROLES = ("ceo", "admin", "super_admin")


def _role(user: User) -> str:
    return (user.role.value if hasattr(user.role, "value") else (user.role or "employee"))


def _is_admin(user: Optional[User]) -> bool:
    if not user:
        return False
    r = _role(user).lower()
    return r in PRIVILEGED_ROLES or user.id == 1 or getattr(user, "is_superuser", False)


def build_track_member_clause(user_id: int):
    """SQL clause: a Build-Track-sourced issue in a project this user is a member of."""
    return and_(
        Issue.id.in_(select(CosIssueExternal.issue_id)),
        Issue.project_id.in_(select(project_members.c.project_id).where(project_members.c.user_id == user_id)),
    )


async def can_view_issue(db: AsyncSession, user: User, issue: Issue) -> bool:
    """Same rule as the Issue Center (CEO/admin, reporter, assignee) plus: members of the project
    a Build-Track-sourced issue belongs to can see it."""
    if _is_admin(user) or issue.reported_by_id == user.id or issue.assigned_user_id == user.id:
        return True
    if not issue.project_id:
        return False
    has_external = (await db.execute(select(CosIssueExternal.id).where(CosIssueExternal.issue_id == issue.id))).first()
    if not has_external:
        return False
    member = (await db.execute(
        select(project_members.c.user_id).where(
            project_members.c.project_id == issue.project_id, project_members.c.user_id == user.id)
    )).first()
    return bool(member)


async def resolve_buildtrack_project_id(db: AsyncSession) -> Optional[int]:
    """BUILDTRACK_ISSUE_PROJECT_ID if it points at a live project, else the first live project named like Build-Track."""
    raw = (settings.BUILDTRACK_ISSUE_PROJECT_ID or "").strip()
    if raw:
        try:
            pid = int(raw)
            if (await db.execute(select(Project.id).where(Project.id == pid, Project.is_deleted == False))).first():
                return pid
            logger.warning("BUILDTRACK_ISSUE_PROJECT_ID=%s is not a live project; falling back to name match", raw)
        except ValueError:
            logger.warning("BUILDTRACK_ISSUE_PROJECT_ID=%r is not a number; falling back to name match", raw)
    rows = (await db.execute(select(Project.id, Project.name).where(Project.is_deleted == False).order_by(Project.id))).all()
    for pid, name in rows:
        if name and re.search(r"build.?track", name, re.I):
            return pid
    return None


def _iso_utc(d) -> Optional[str]:
    # Stored as naive UTC; say so explicitly so the browser converts it correctly.
    return (d.isoformat() + "Z") if d else None


def external_brief(ext: Optional[CosIssueExternal]) -> Optional[dict]:
    """The small shape the issue list needs for a badge. No contact details."""
    if not ext:
        return None
    rep = ext.reporter or {}
    return {
        "source": ext.source,
        "external_id": ext.external_id,
        "reporter_name": rep.get("name"),
        "company": rep.get("company"),
        "screen_name": (ext.screen or {}).get("name"),
    }


def external_detail(ext: CosIssueExternal) -> dict:
    return {
        "source": ext.source,
        "external_id": ext.external_id,
        "client_report_id": ext.client_report_id,
        "reporter": ext.reporter or {},
        "screen": ext.screen or {},
        "breadcrumbs": ext.breadcrumbs or [],
        "device": ext.device or {},
        "project": ext.project,
        "occurred_at": _iso_utc(ext.occurred_at),
        "received_at": _iso_utc(ext.received_at),
        "has_screenshot": bool(ext.screenshot_key),
        "last_callback_status": ext.last_callback_status,
        "last_callback_at": _iso_utc(ext.last_callback_at),
    }


# --- screenshot storage (private S3, local folder when S3 is not configured) ---------------------

async def store_screenshot(raw: bytes, external_id: str, mime: str) -> str:
    from app.services.s3 import upload_file_to_s3
    ext = "png" if mime == "image/png" else "jpg"
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", external_id)[:60]
    return await asyncio.to_thread(upload_file_to_s3, io.BytesIO(raw), f"buildtrack-{safe}.{ext}", mime)


async def read_screenshot(key: str) -> Optional[bytes]:
    from app.services.s3 import UPLOAD_DIR, get_s3_client

    def _read() -> Optional[bytes]:
        if key.startswith("local/"):
            path = os.path.join(UPLOAD_DIR, os.path.basename(key[len("local/"):]))
            if not os.path.exists(path):
                return None
            with open(path, "rb") as fh:
                return fh.read()
        client = get_s3_client()
        if not client:
            return None
        return client.get_object(Bucket=settings.AWS_S3_BUCKET_NAME, Key=key)["Body"].read()

    return await asyncio.to_thread(_read)


async def discard_screenshot(key: Optional[str]) -> None:
    if not key:
        return
    from app.services.s3 import delete_file_from_s3
    try:
        await asyncio.to_thread(delete_file_from_s3, key)
    except Exception:  # best effort cleanup
        logger.warning("could not delete orphan screenshot %s", key)
