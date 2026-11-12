"""Details + screenshot of an issue that came from Build-Track. Same visibility as the issue itself
(CEO/admin, reporter, assignee, members of the issue's project). The screenshot is streamed through the API
behind the normal sign-in; there is no public or presigned link."""
from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.responses import APIResponse, success_response
from app.models.issue import Issue
from app.models.issue_external import CosIssueExternal
from app.models.user import User
from app.services.issue_external import can_view_issue, external_detail, read_screenshot

router = APIRouter()


async def _visible_external(db: AsyncSession, issue_id: int, user: User) -> CosIssueExternal:
    issue = (await db.execute(select(Issue).where(Issue.id == issue_id, Issue.is_deleted == False))).scalars().first()
    ext = (await db.execute(select(CosIssueExternal).where(CosIssueExternal.issue_id == issue_id))).scalars().first()
    if not issue or not ext:
        raise HTTPException(status_code=404, detail="Not found")
    if not await can_view_issue(db, user, issue):
        raise HTTPException(status_code=403, detail="You do not have access to this issue")
    return ext


@router.get("/{issue_id}/external", response_model=APIResponse)
async def read_issue_external(issue_id: int, db: AsyncSession = Depends(deps.get_db),
                              current_user: User = Depends(deps.get_current_user)):
    ext = await _visible_external(db, issue_id, current_user)
    return success_response(data=external_detail(ext), message="Fetched")


@router.get("/{issue_id}/external/screenshot")
async def read_issue_external_screenshot(issue_id: int, db: AsyncSession = Depends(deps.get_db),
                                         current_user: User = Depends(deps.get_current_user)):
    ext = await _visible_external(db, issue_id, current_user)
    if not ext.screenshot_key:
        raise HTTPException(status_code=404, detail="No screenshot")
    body = await read_screenshot(ext.screenshot_key)
    if body is None:
        raise HTTPException(status_code=404, detail="The screenshot is missing on the server")
    return Response(content=body, media_type=ext.screenshot_mime or "image/png",
                    headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff",
                             "Content-Disposition": 'inline; filename="screenshot"'})
