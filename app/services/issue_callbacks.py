"""Tell Build-Track when one of its issues changes status (contract section 3).

Fired after the DB commit, in a background task, with a few attempts and short backoff. Best effort:
a failure is recorded on the cos_issue_external row and logged, it never affects the request that changed the status.
"""
import asyncio
import hashlib
import hmac
import json
import logging
import time
from datetime import datetime
from typing import Optional

import httpx
from sqlalchemy import select

from app.core.config import settings
from app.db.session import SessionLocal
from app.models.issue import Issue
from app.models.issue_external import CosIssueExternal
from app.models.issue_followup import IssueFollowup

logger = logging.getLogger(__name__)

ATTEMPTS = 3
BACKOFF_SECONDS = (1.0, 4.0)   # wait before attempt 2 and 3
TIMEOUT_SECONDS = 10.0
_running: set = set()


def sign(secret: str, timestamp: str, body: bytes) -> str:
    return hmac.new(secret.encode("utf-8"), timestamp.encode("ascii") + b"." + body, hashlib.sha256).hexdigest()


def status_value(s) -> Optional[str]:
    return s.value if hasattr(s, "value") else s


def schedule_status_callback(issue_id: int, old_status, new_status) -> None:
    """Call right after the commit that changed the status. No-op when nothing changed or the integration is off."""
    if status_value(old_status) == status_value(new_status):
        return
    if not settings.BUILDTRACK_INTEGRATION_SECRET:
        return
    try:
        task = asyncio.get_running_loop().create_task(_deliver(issue_id))
    except RuntimeError:   # no running loop (script usage)
        return
    _running.add(task)     # keep a reference so the task is not garbage-collected mid-flight
    task.add_done_callback(_running.discard)


async def _build_payload(issue_id: int):
    async with SessionLocal() as db:
        ext = (await db.execute(select(CosIssueExternal).where(CosIssueExternal.issue_id == issue_id))).scalars().first()
        if not ext or not ext.callback_url:
            return None, None
        issue = (await db.execute(select(Issue).where(Issue.id == issue_id))).scalars().first()
        if not issue:
            return None, None
        notes = (await db.execute(
            select(IssueFollowup.message)
            .where(IssueFollowup.issue_id == issue_id, IssueFollowup.is_deleted == False)
            .order_by(IssueFollowup.created_at.desc(), IssueFollowup.id.desc()).limit(10)
        )).scalars().all()
        note = next((m.strip() for m in notes if m and m.strip() and not m.startswith("System:")), None)
        payload = {
            "external_id": ext.external_id,
            "issue_id": issue.id,
            "status": status_value(issue.status) or "open",
            "note": note[:1000] if note else None,
            "updated_at": (issue.updated_at or datetime.utcnow()).isoformat() + "Z",
        }
        return payload, ext.callback_url


async def _record(issue_id: int, result: str) -> None:
    try:
        async with SessionLocal() as db:
            ext = (await db.execute(select(CosIssueExternal).where(CosIssueExternal.issue_id == issue_id))).scalars().first()
            if ext:
                ext.last_callback_status = result[:300]
                ext.last_callback_at = datetime.utcnow()
                await db.commit()
    except Exception:
        logger.exception("could not record callback result for issue %s", issue_id)


async def _deliver(issue_id: int) -> None:
    try:
        payload, url = await _build_payload(issue_id)
        if not payload:
            return
        secret = settings.BUILDTRACK_INTEGRATION_SECRET
        body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        result = "failed: not attempted"
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS, follow_redirects=False) as client:
            for attempt in range(ATTEMPTS):
                if attempt:
                    await asyncio.sleep(BACKOFF_SECONDS[min(attempt - 1, len(BACKOFF_SECONDS) - 1)])
                ts = str(int(time.time()))   # fresh timestamp per attempt so a retry is never "stale"
                try:
                    r = await client.post(url, content=body, headers={
                        "Content-Type": "application/json",
                        "X-BT-Timestamp": ts,
                        "X-BT-Signature": sign(secret, ts, body),
                    })
                    if 200 <= r.status_code < 300:
                        result = f"ok: {payload['status']} (HTTP {r.status_code})"
                        break
                    result = f"failed: HTTP {r.status_code}"
                except Exception as e:   # network / timeout
                    result = f"failed: {type(e).__name__}"
        if not result.startswith("ok"):
            logger.warning("Build-Track status callback for issue %s %s", issue_id, result)
        await _record(issue_id, result)
    except Exception:
        logger.exception("Build-Track status callback crashed for issue %s", issue_id)
