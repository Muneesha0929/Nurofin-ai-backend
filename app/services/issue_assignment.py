"""Auto-assignment for new issues, shared by the Issue Center form and the Build-Track intake."""
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.issue import Issue, IssueStatusEnum, IssueAssignmentStatusEnum
from app.models.user import User


async def auto_assign_fields(db: AsyncSession, project_id: Optional[int]) -> dict:
    """Pick the project member (never the CEO) with the fewest open / in-progress issues.

    Returns the Issue fields to set (assigned_user_id, assignment_status, assignment_timestamp),
    or {} when there is no project or nobody to assign to. Behaviour is exactly what create_issue
    always did inline; it only lives here so a second caller does not copy it.
    """
    if not project_id:
        return {}
    from app.models.project import project_members
    stmt = select(User).join(project_members, project_members.c.user_id == User.id).filter(
        project_members.c.project_id == project_id,
        User.is_deleted == False
    )
    proj_members = [m for m in (await db.execute(stmt)).scalars().all() if m.role != "ceo" and getattr(m.role, "value", m.role) != "ceo"]
    if not proj_members:
        return {}

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
    return {
        "assigned_user_id": best_assignee.id,
        "assignment_status": IssueAssignmentStatusEnum.pending_acceptance,
        "assignment_timestamp": datetime.utcnow(),
    }
