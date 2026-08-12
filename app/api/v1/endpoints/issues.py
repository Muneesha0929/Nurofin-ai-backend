from typing import Any, Optional
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import func, or_

from app.api import deps
from app.models.issue import Issue, IssueStatusEnum
from app.models.issue_followup import IssueFollowup
from app.models.project import Project
from app.models.user import User
from app.models.notification import Notification, NotificationTypeEnum
from app.schemas.issue import IssueCreate, IssueUpdate, IssueFollowupCreate
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()

CEO_ROLES = ("ceo", "admin", "super_admin")


def _role(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else (user.role or "employee")


def _fmt_date(d) -> Optional[str]:
    if d is None:
        return None
    if hasattr(d, 'isoformat'):
        return d.isoformat()
    return str(d)


def _user_brief(u: Optional[User]) -> Optional[dict]:
    if not u:
        return None
    return {
        "id": u.id,
        "name": u.full_name,
        "avatar": u.profile_picture,
        "role": u.role,
    }


def _serialize_issue(db: AsyncSession, issue: Issue) -> dict:
    followup_count = len(issue.followups or [])
    return {
        "id": issue.id,
        "title": issue.title,
        "description": issue.description,
        "category": issue.category,
        "priority": issue.priority.value if hasattr(issue.priority, "value") else issue.priority,
        "status": issue.status.value if hasattr(issue.status, "value") else issue.status,
        "deadline": issue.deadline,
        "attachments": issue.attachments or [],
        "project_id": issue.project_id,
        "project": {
            "id": issue.project.id,
            "name": issue.project.name,
        } if issue.project else None,
        "assigned_user_id": issue.assigned_user_id,
        "assigned_user": _user_brief(issue.assigned_user),
        "reported_by_id": issue.reported_by_id,
        "reported_by": _user_brief(issue.reported_by),
        "followup_count": followup_count,
        "created_at": _fmt_date(issue.created_at),
        "updated_at": _fmt_date(issue.updated_at),
    }


async def _serialize_followup(f: IssueFollowup) -> dict:
    return {
        "id": f.id,
        "issue_id": f.issue_id,
        "user_id": f.user_id,
        "user_name": f.user.full_name if f.user else None,
        "user_avatar": f.user.profile_picture if f.user else None,
        "message": f.message,
        "created_at": _fmt_date(f.created_at),
    }


async def _load_issue(db: AsyncSession, issue_id: int) -> Optional[Issue]:
    result = await db.execute(
        select(Issue)
        .options(
            selectinload(Issue.project),
            selectinload(Issue.assigned_user),
            selectinload(Issue.reported_by),
            selectinload(Issue.followups).selectinload(IssueFollowup.user),
        )
        .filter(Issue.id == issue_id, Issue.is_deleted == False)
    )
    return result.scalars().first()


@router.get("", response_model=APIResponse)
async def read_issues(
    db: AsyncSession = Depends(deps.get_db),
    status: Optional[str] = None,
    priority: Optional[str] = None,
    project_id: Optional[int] = None,
    assigned_user_id: Optional[int] = None,
    reported_by_id: Optional[int] = None,
    mine: Optional[bool] = None,
    search: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    q = select(Issue).options(
        selectinload(Issue.project),
        selectinload(Issue.assigned_user),
        selectinload(Issue.reported_by),
        selectinload(Issue.followups).selectinload(IssueFollowup.user),
    ).filter(Issue.is_deleted == False)

    # Non-privileged users only see issues they reported or were assigned
    if _role(current_user) not in CEO_ROLES:
        q = q.filter(
            or_(
                Issue.reported_by_id == current_user.id,
                Issue.assigned_user_id == current_user.id,
            )
        )

    if status:
        q = q.filter(Issue.status == status)
    if priority:
        q = q.filter(Issue.priority == priority)
    if project_id:
        q = q.filter(Issue.project_id == project_id)
    if assigned_user_id:
        q = q.filter(Issue.assigned_user_id == assigned_user_id)
    if reported_by_id:
        q = q.filter(Issue.reported_by_id == reported_by_id)
    if mine:
        q = q.filter(Issue.reported_by_id == current_user.id)
    if search:
        q = q.filter(or_(Issue.title.ilike(f"%{search}%"), Issue.description.ilike(f"%{search}%")))

    result = await db.execute(q.order_by(Issue.created_at.desc()).offset(skip).limit(limit))
    issues = result.scalars().all()
    data = [_serialize_issue(db, issue) for issue in issues]

    count_q = select(func.count()).select_from(Issue).filter(Issue.is_deleted == False)
    if _role(current_user) not in CEO_ROLES:
        count_q = count_q.filter(
            or_(
                Issue.reported_by_id == current_user.id,
                Issue.assigned_user_id == current_user.id,
            )
        )
    total = (await db.execute(count_q)).scalar() or 0

    return success_response(data={"issues": data, "total": total}, message="Issues fetched successfully")


@router.post("", response_model=APIResponse)
async def create_issue(
    *,
    db: AsyncSession = Depends(deps.get_db),
    issue_in: IssueCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    data = issue_in.dict(exclude_unset=True)

    if data.get("assigned_user_id"):
        assignee = (await db.execute(select(User).filter(User.id == data["assigned_user_id"], User.is_deleted == False))).scalars().first()
        if not assignee:
            return error_response(message="Assigned user not found")
    if data.get("project_id"):
        project = (await db.execute(select(Project).filter(Project.id == data["project_id"], Project.is_deleted == False))).scalars().first()
        if not project:
            return error_response(message="Project not found")

    db_issue = Issue(**data, reported_by_id=current_user.id)
    db.add(db_issue)
    await db.flush()

    if data.get("assigned_user_id"):
        db.add(Notification(
            title="New issue assigned to you",
            message=f"{current_user.full_name or 'Someone'} reported: {data.get('title', 'Issue')}",
            type=NotificationTypeEnum.issue_assigned,
            user_id=data["assigned_user_id"],
            link=f"/issues?id={db_issue.id}",
        ))

    await db.commit()
    loaded = await _load_issue(db, db_issue.id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue reported successfully")


@router.get("/{issue_id}", response_model=APIResponse)
async def read_issue(
    issue_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    if _role(current_user) not in CEO_ROLES and issue.reported_by_id != current_user.id and issue.assigned_user_id != current_user.id:
        return error_response(message="You do not have access to this issue")

    data = _serialize_issue(db, issue)
    data["followups"] = [
        await _serialize_followup(f) for f in (issue.followups or [])
    ]
    return success_response(data=data, message="Issue fetched successfully")


@router.put("/{issue_id}", response_model=APIResponse)
async def update_issue(
    issue_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    issue_in: IssueUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")

    if _role(current_user) not in CEO_ROLES and issue.reported_by_id != current_user.id and issue.assigned_user_id != current_user.id:
        return error_response(message="Only the reporter, assignee, or CEO can update this issue")

    update_data = issue_in.dict(exclude_unset=True)

    old_status = issue.status.value if hasattr(issue.status, "value") else issue.status
    old_assignee = issue.assigned_user_id

    if update_data.get("assigned_user_id"):
        assignee = (await db.execute(select(User).filter(User.id == update_data["assigned_user_id"], User.is_deleted == False))).scalars().first()
        if not assignee:
            return error_response(message="Assigned user not found")
    if update_data.get("project_id"):
        project = (await db.execute(select(Project).filter(Project.id == update_data["project_id"], Project.is_deleted == False))).scalars().first()
        if not project:
            return error_response(message="Project not found")

    for field, value in update_data.items():
        setattr(issue, field, value)
    await db.flush()

    # Notify assignee when reassigned
    if update_data.get("assigned_user_id") and update_data["assigned_user_id"] != old_assignee:
        db.add(Notification(
            title="Issue assigned to you",
            message=f"{current_user.full_name or 'Someone'} assigned: {issue.title}",
            type=NotificationTypeEnum.issue_assigned,
            user_id=update_data["assigned_user_id"],
            link=f"/issues?id={issue.id}",
        ))

    # Notify reporter when assignee changes status
    new_status = issue.status.value if hasattr(issue.status, "value") else issue.status
    if new_status != old_status and issue.reported_by_id and issue.reported_by_id != current_user.id:
        db.add(Notification(
            title="Issue status updated",
            message=f"Your reported issue '{issue.title}' is now {new_status.replace('_', ' ')}.",
            type=NotificationTypeEnum.issue_status_changed,
            user_id=issue.reported_by_id,
            link=f"/issues?id={issue.id}",
        ))

    await db.commit()
    loaded = await _load_issue(db, issue_id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue updated successfully")


@router.delete("/{issue_id}", response_model=APIResponse)
async def delete_issue(
    issue_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if _role(current_user) not in CEO_ROLES:
        return error_response(message="Only the CEO or admin can delete issues")
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    issue.is_deleted = True
    await db.commit()
    return success_response(message="Issue deleted")


@router.put("/{issue_id}/status", response_model=APIResponse)
async def update_issue_status(
    issue_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    status: str,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    if _role(current_user) not in CEO_ROLES and issue.reported_by_id != current_user.id and issue.assigned_user_id != current_user.id:
        return error_response(message="You cannot update this issue")

    old_status = issue.status.value if hasattr(issue.status, "value") else issue.status
    if status not in [s.value for s in IssueStatusEnum]:
        return error_response(message="Invalid status")
    issue.status = status
    await db.flush()

    if old_status != status and issue.reported_by_id and issue.reported_by_id != current_user.id:
        db.add(Notification(
            title="Issue status updated",
            message=f"Your reported issue '{issue.title}' is now {status.replace('_', ' ')}.",
            type=NotificationTypeEnum.issue_status_changed,
            user_id=issue.reported_by_id,
            link=f"/issues?id={issue.id}",
        ))
    if old_status != status and issue.assigned_user_id and issue.assigned_user_id != current_user.id and issue.assigned_user_id != issue.reported_by_id:
        db.add(Notification(
            title="Issue status updated",
            message=f"Issue '{issue.title}' is now {status.replace('_', ' ')}.",
            type=NotificationTypeEnum.issue_status_changed,
            user_id=issue.assigned_user_id,
            link=f"/issues?id={issue.id}",
        ))

    await db.commit()
    loaded = await _load_issue(db, issue_id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue status updated")


@router.get("/{issue_id}/followups", response_model=APIResponse)
async def read_followups(
    issue_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    if _role(current_user) not in CEO_ROLES and issue.reported_by_id != current_user.id and issue.assigned_user_id != current_user.id:
        return error_response(message="You do not have access to this issue")

    result = await db.execute(
        select(IssueFollowup)
        .options(selectinload(IssueFollowup.user))
        .filter(IssueFollowup.issue_id == issue_id, IssueFollowup.is_deleted == False)
        .order_by(IssueFollowup.created_at.asc())
    )
    data = [await _serialize_followup(f) for f in result.scalars().all()]
    return success_response(data=data, message="Followups fetched successfully")


@router.post("/{issue_id}/followups", response_model=APIResponse)
async def create_followup(
    issue_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    followup_in: IssueFollowupCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    if _role(current_user) not in CEO_ROLES and issue.reported_by_id != current_user.id and issue.assigned_user_id != current_user.id:
        return error_response(message="You do not have access to this issue")

    db_followup = IssueFollowup(issue_id=issue_id, user_id=current_user.id, message=followup_in.message)
    db.add(db_followup)
    await db.flush()

    # Notify the other party (assignee <-> reporter)
    if issue.reported_by_id and issue.reported_by_id != current_user.id:
        db.add(Notification(
            title="New follow-up on your reported issue",
            message=f"{current_user.full_name or 'Someone'} commented on '{issue.title}'",
            type=NotificationTypeEnum.issue_followup,
            user_id=issue.reported_by_id,
            link=f"/issues?id={issue.id}",
        ))
    if issue.assigned_user_id and issue.assigned_user_id != current_user.id and issue.assigned_user_id != issue.reported_by_id:
        db.add(Notification(
            title="New follow-up on issue",
            message=f"{current_user.full_name or 'Someone'} commented on '{issue.title}'",
            type=NotificationTypeEnum.issue_followup,
            user_id=issue.assigned_user_id,
            link=f"/issues?id={issue.id}",
        ))

    await db.commit()
    await db.refresh(db_followup)
    return success_response(data=await _serialize_followup(db_followup), message="Follow-up added successfully")
