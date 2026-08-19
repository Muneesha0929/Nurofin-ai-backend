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
        "assignment_status": issue.assignment_status.value if hasattr(issue.assignment_status, "value") else issue.assignment_status,
        "assignment_timestamp": _fmt_date(issue.assignment_timestamp),
        "issue_type": issue.issue_type.value if hasattr(issue.issue_type, "value") else issue.issue_type,
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
    from datetime import datetime
    from app.models.issue import IssueAssignmentStatusEnum

    data = issue_in.dict(exclude_unset=True)

    if data.get("assigned_user_id"):
        assignee = (await db.execute(select(User).filter(User.id == data["assigned_user_id"], User.is_deleted == False))).scalars().first()
        if not assignee:
            return error_response(message="Assigned user not found")
    if data.get("project_id"):
        project = (await db.execute(select(Project).filter(Project.id == data["project_id"], Project.is_deleted == False))).scalars().first()
        if not project:
            return error_response(message="Project not found")

    # Auto-assignment logic
    if not data.get("assigned_user_id") and data.get("project_id"):
        # Find all users in the project excluding the CEO
        from app.models.project import project_members
        stmt = select(User).join(project_members, project_members.c.user_id == User.id).filter(
            project_members.c.project_id == data["project_id"], 
            User.is_deleted == False
        )
        proj_members = [m for m in (await db.execute(stmt)).scalars().all() if m.role != "ceo" and getattr(m.role, "value", m.role) != "ceo"]
        
        if proj_members:
            # Simple free-time check: assign to the person with the fewest open issues
            member_ids = [m.id for m in proj_members]
            issue_counts = await db.execute(
                select(Issue.assigned_user_id, func.count(Issue.id))
                .filter(Issue.assigned_user_id.in_(member_ids), Issue.status.in_([IssueStatusEnum.open, IssueStatusEnum.in_progress]))
                .group_by(Issue.assigned_user_id)
            )
            counts_dict = {row[0]: row[1] for row in issue_counts}
            
            # Sort members by number of active issues
            proj_members.sort(key=lambda m: counts_dict.get(m.id, 0))
            best_assignee = proj_members[0]
            
            data["assigned_user_id"] = best_assignee.id
            data["assignment_status"] = IssueAssignmentStatusEnum.pending_acceptance
            data["assignment_timestamp"] = datetime.utcnow()
    # If assigned to anyone (either explicitly or via auto-assign), set to pending acceptance
    if data.get("assigned_user_id") and not data.get("assignment_status"):
        data["assignment_status"] = IssueAssignmentStatusEnum.pending_acceptance
        data["assignment_timestamp"] = datetime.utcnow()

    db_issue = Issue(**data, reported_by_id=current_user.id)
    db.add(db_issue)
    await db.flush()

    if data.get("assigned_user_id"):
        db.add(Notification(
            title="New issue assigned to you (Action Required)",
            message=f"{current_user.full_name or 'Someone'} reported: {data.get('title', 'Issue')}. Please accept or decline within 1 hour.",
            type=NotificationTypeEnum.task_assigned,
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
            type=NotificationTypeEnum.task_assigned,
            user_id=update_data["assigned_user_id"],
            link=f"/issues?id={issue.id}",
        ))

    # Notify reporter when assignee changes status
    new_status = issue.status.value if hasattr(issue.status, "value") else issue.status
    if new_status != old_status and issue.reported_by_id and issue.reported_by_id != current_user.id:
        db.add(Notification(
            title="Issue status updated",
            message=f"Your reported issue '{issue.title}' is now {new_status.replace('_', ' ')}.",
            type=NotificationTypeEnum.project_update,
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

    if old_status != status:
        if issue.reported_by_id and issue.reported_by_id != current_user.id:
            db.add(Notification(
                title="Issue status updated",
                message=f"Your reported issue '{issue.title}' is now {status.replace('_', ' ')}.",
                type=NotificationTypeEnum.project_update,
                user_id=issue.reported_by_id,
                link=f"/issues?id={issue.id}",
            ))
            # If completed/resolved, we also simulate sending an email to the reporter/client
            if status in [IssueStatusEnum.resolved.value, IssueStatusEnum.closed.value]:
                import logging
                logger = logging.getLogger(__name__)
                logger.info(f"Sending completion email to client (User ID: {issue.reported_by_id}) for Issue {issue.id}")
                # TODO: Integrate actual email service here (e.g. Amazon SES, SendGrid)

        if issue.assigned_user_id and issue.assigned_user_id != current_user.id and issue.assigned_user_id != issue.reported_by_id:
            db.add(Notification(
                title="Issue status updated",
                message=f"Issue '{issue.title}' is now {status.replace('_', ' ')}.",
                type=NotificationTypeEnum.project_update,
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
            type=NotificationTypeEnum.project_update,
            user_id=issue.reported_by_id,
            link=f"/issues?id={issue.id}",
        ))
    if issue.assigned_user_id and issue.assigned_user_id != current_user.id and issue.assigned_user_id != issue.reported_by_id:
        db.add(Notification(
            title="New follow-up on issue",
            message=f"{current_user.full_name or 'Someone'} commented on '{issue.title}'",
            type=NotificationTypeEnum.project_update,
            user_id=issue.assigned_user_id,
            link=f"/issues?id={issue.id}",
        ))

    await db.commit()
    loaded_followup = await db.get(IssueFollowup, db_followup.id)
    return success_response(
        data=await _serialize_followup(loaded_followup),
        message="Follow-up added successfully"
    )

@router.post("/{issue_id}/accept", response_model=APIResponse)
async def accept_issue(
    issue_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    from app.models.issue import IssueAssignmentStatusEnum
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    
    if issue.assigned_user_id != current_user.id:
        return error_response(message="Only the assigned user can accept this issue")
        
    if issue.assignment_status == IssueAssignmentStatusEnum.accepted:
        return error_response(message="Issue is already accepted")
        
    issue.assignment_status = IssueAssignmentStatusEnum.accepted
    issue.status = IssueStatusEnum.in_progress
    
    # Notify team members/project members that the user is now working on it
    from app.models.project import project_members
    if issue.project_id:
        stmt = select(User.id).join(project_members, project_members.c.user_id == User.id).filter(project_members.c.project_id == issue.project_id)
        team_members = (await db.execute(stmt)).scalars().all()
        for member_id in team_members:
            if member_id != current_user.id:
                db.add(Notification(
                    title="Issue Accepted",
                    message=f"{current_user.full_name} is now working on '{issue.title}'",
                    type=NotificationTypeEnum.task_assigned, # Reusing type for now
                    user_id=member_id,
                    link=f"/issues?id={issue.id}",
                ))
    
    await db.commit()
    loaded = await _load_issue(db, issue_id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue accepted successfully")


@router.post("/{issue_id}/decline", response_model=APIResponse)
async def decline_issue(
    issue_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    from app.models.issue import IssueAssignmentStatusEnum
    from datetime import datetime
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
    
    if issue.assigned_user_id != current_user.id:
        return error_response(message="Only the assigned user can decline this issue")
        
    # Add to declined_by_users list
    declined_list = list(issue.declined_by_users) if issue.declined_by_users else []
    if current_user.id not in declined_list:
        declined_list.append(current_user.id)
    issue.declined_by_users = declined_list
    
    # Auto-assign to next person logic
    next_assignee = None
    if issue.project_id:
        from app.models.project import project_members
        from app.models.user import RoleEnum
        stmt = select(User).join(project_members, project_members.c.user_id == User.id).filter(
            project_members.c.project_id == issue.project_id, 
            User.is_deleted == False,
            User.id.notin_(declined_list)
        )
        proj_members = [m for m in (await db.execute(stmt)).scalars().all() if m.role != "ceo" and getattr(m.role, "value", m.role) != "ceo"]
        
        if proj_members:
            member_ids = [m.id for m in proj_members]
            issue_counts = await db.execute(
                select(Issue.assigned_user_id, func.count(Issue.id))
                .filter(Issue.assigned_user_id.in_(member_ids), Issue.status.in_([IssueStatusEnum.open, IssueStatusEnum.in_progress]))
                .group_by(Issue.assigned_user_id)
            )
            counts_dict = {row[0]: row[1] for row in issue_counts}
            proj_members.sort(key=lambda m: counts_dict.get(m.id, 0))
            next_assignee = proj_members[0]
            
    if next_assignee:
        issue.assigned_user_id = next_assignee.id
        issue.assignment_status = IssueAssignmentStatusEnum.accepted
        issue.status = IssueStatusEnum.in_progress
        
        db.add(Notification(
            title="Issue Reassigned to You",
            message=f"{current_user.full_name or 'Someone'} declined: {issue.title}. You have been automatically assigned to work on it.",
            type=NotificationTypeEnum.task_assigned,
            user_id=next_assignee.id,
            link=f"/issues?id={issue.id}",
        ))
        
        # Notify project team members
        if issue.project_id:
            from app.models.project import project_members as proj_mem_table
            stmt = select(User.id).join(proj_mem_table, proj_mem_table.c.user_id == User.id).filter(proj_mem_table.c.project_id == issue.project_id)
            team_members = (await db.execute(stmt)).scalars().all()
            for member_id in team_members:
                if member_id != current_user.id and member_id != next_assignee.id:
                    db.add(Notification(
                        title="Issue Declined and Reassigned",
                        message=f"{current_user.full_name} declined '{issue.title}'. It was reassigned to {next_assignee.full_name}.",
                        type=NotificationTypeEnum.project_update,
                        user_id=member_id,
                        link=f"/issues?id={issue.id}",
                    ))
    else:
        # No one left to assign
        issue.assigned_user_id = None
        issue.assignment_status = None
        # Could notify admin here
        
    await db.commit()
    loaded = await _load_issue(db, issue_id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue declined. It has been routed to the next available teammate.")


from pydantic import BaseModel
class IssueTransfer(BaseModel):
    user_id: int
    reason: Optional[str] = None

@router.post("/{issue_id}/transfer", response_model=APIResponse)
async def transfer_issue(
    issue_id: int,
    transfer_in: IssueTransfer,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    from app.models.issue import IssueAssignmentStatusEnum
    from datetime import datetime
    
    issue = await _load_issue(db, issue_id)
    if not issue:
        return error_response(message="Issue not found")
        
    if issue.assigned_user_id != current_user.id and _role(current_user) not in CEO_ROLES:
        return error_response(message="Only the assignee or admin can transfer this issue")
        
    target_user = (await db.execute(select(User).filter(User.id == transfer_in.user_id, User.is_deleted == False))).scalars().first()
    if not target_user:
        return error_response(message="Target user not found")
        
    issue.assigned_user_id = target_user.id
    issue.assignment_status = IssueAssignmentStatusEnum.pending_acceptance
    issue.assignment_timestamp = datetime.utcnow()
    
    # Notify target user
    db.add(Notification(
        title="Issue Transferred to You",
        message=f"{current_user.full_name} transferred '{issue.title}' to you.",
        type=NotificationTypeEnum.task_assigned,
        user_id=target_user.id,
        link=f"/issues?id={issue.id}",
    ))
    
    # Notify project team members
    if issue.project_id:
        from app.models.project import project_members
        stmt = select(User.id).join(project_members, project_members.c.user_id == User.id).filter(project_members.c.project_id == issue.project_id)
        team_members = (await db.execute(stmt)).scalars().all()
        for member_id in team_members:
            if member_id != current_user.id and member_id != target_user.id:
                db.add(Notification(
                    title="Issue Transferred",
                    message=f"{current_user.full_name} transferred '{issue.title}' to {target_user.full_name}.",
                    type=NotificationTypeEnum.project_update,
                    user_id=member_id,
                    link=f"/issues?id={issue.id}",
                ))
    
    # Create an internal followup noting the transfer
    db_followup = IssueFollowup(
        issue_id=issue.id, 
        user_id=current_user.id, 
        message=f"System: Transferred issue to {target_user.full_name}. Reason: {transfer_in.reason or 'None provided'}"
    )
    db.add(db_followup)
    
    await db.commit()
    loaded = await _load_issue(db, issue_id)
    return success_response(data=_serialize_issue(db, loaded), message="Issue transferred successfully")
