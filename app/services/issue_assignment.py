"""Auto-assignment for new issues, shared by the Issue Center form and the Build-Track intake."""
from datetime import datetime
from typing import Optional

from sqlalchemy import func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.issue import Issue, IssueStatusEnum, IssueAssignmentStatusEnum
from app.models.user import User

CEO_ROLES = ("ceo", "admin", "super_admin")


def _is_ceo_or_admin(user: User) -> bool:
    r = (user.role.value if hasattr(user.role, "value") else (user.role or "")).lower()
    return r in CEO_ROLES or user.id == 1


async def auto_assign_fields(db: AsyncSession, project_id: Optional[int] = None) -> dict:
    """Pick the eligible member (strictly excluding the CEO) with the fewest open / in-progress issues.

    Returns the Issue fields to set (assigned_user_id, assignment_status, assignment_timestamp),
    or {} when there is nobody to assign to.
    """
    candidates = []
    if project_id:
        from app.models.project import project_members
        stmt = select(User).join(project_members, project_members.c.user_id == User.id).filter(
            project_members.c.project_id == project_id,
            User.is_deleted == False
        )
        candidates = [m for m in (await db.execute(stmt)).scalars().all() if not _is_ceo_or_admin(m)]

    # Fallback to all non-CEO company users if no project or no eligible project members
    if not candidates:
        all_users_stmt = select(User).filter(User.is_deleted == False)
        candidates = [m for m in (await db.execute(all_users_stmt)).scalars().all() if not _is_ceo_or_admin(m)]

    if not candidates:
        return {}

    # Simple free-time check: assign to the person with the fewest open issues
    candidate_ids = [m.id for m in candidates]
    issue_counts = await db.execute(
        select(Issue.assigned_user_id, func.count(Issue.id))
        .filter(Issue.assigned_user_id.in_(candidate_ids), Issue.status.in_([IssueStatusEnum.open, IssueStatusEnum.in_progress]))
        .group_by(Issue.assigned_user_id)
    )
    counts_dict = {row[0]: row[1] for row in issue_counts}

    # Sort candidates by number of active issues
    candidates.sort(key=lambda m: counts_dict.get(m.id, 0))
    best_assignee = candidates[0]
    return {
        "assigned_user_id": best_assignee.id,
        "assignment_status": IssueAssignmentStatusEnum.pending_acceptance,
        "assignment_timestamp": datetime.utcnow(),
    }
