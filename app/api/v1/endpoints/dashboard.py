from typing import Any
from datetime import datetime
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.api import deps
from app.models.project import Project, ProjectStatusEnum
from app.models.task import Task, TaskPriorityEnum
from app.models.meeting import Meeting, MeetingParticipant, ParticipantStatusEnum, MeetingExtractedTask
from app.models.user import User
from app.schemas.dashboard import DashboardSummary
from app.core.responses import APIResponse, success_response

router = APIRouter()

@router.get("/summary", response_model=APIResponse)
async def get_dashboard_summary(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    # Active Projects
    res = await db.execute(select(func.count(Project.id)).filter(Project.status == ProjectStatusEnum.active, Project.is_deleted == False))
    active_projects = res.scalar() or 0

    # Completed Projects
    res = await db.execute(select(func.count(Project.id)).filter(Project.status == ProjectStatusEnum.completed, Project.is_deleted == False))
    completed_projects = res.scalar() or 0

    today_str = datetime.now().strftime('%Y-%m-%d')

    # Total Tasks
    res = await db.execute(select(func.count(Task.id)).filter(Task.is_deleted == False))
    total_tasks = res.scalar() or 0

    # Completed Tasks
    res = await db.execute(select(func.count(Task.id)).filter(Task.status == 'completed', Task.is_deleted == False))
    completed_tasks = res.scalar() or 0

    from sqlalchemy import or_
    # Today's Tasks
    res = await db.execute(
        select(func.count(Task.id))
        .filter(or_(Task.deadline == today_str, Task.scheduled_date == today_str), Task.is_deleted == False)
    )
    today_tasks = res.scalar() or 0

    # Overdue Tasks
    res = await db.execute(
        select(func.count(Task.id))
        .filter(
            or_(
                Task.deadline < today_str,
                Task.scheduled_date < today_str
            ),
            Task.status != 'completed',
            Task.is_deleted == False
        )
    )
    overdue_tasks = res.scalar() or 0

    # Today's Meetings
    res = await db.execute(select(func.count(Meeting.id)).filter(Meeting.date == today_str, Meeting.is_deleted == False))
    today_meetings = res.scalar() or 0

    # High Priority Tasks
    res = await db.execute(select(func.count(Task.id)).filter(Task.priority.in_([TaskPriorityEnum.high, TaskPriorityEnum.critical]), Task.is_deleted == False))
    high_priority_tasks = res.scalar() or 0

    # Pending Invitations (meetings where user is invited but hasn't responded)
    pending_inv = await db.execute(
        select(func.count(MeetingParticipant.id))
        .join(Meeting)
        .filter(
            MeetingParticipant.user_id == current_user.id,
            MeetingParticipant.status == ParticipantStatusEnum.pending,
            Meeting.is_deleted == False,
        )
    )
    pending_invitations = pending_inv.scalar() or 0

    # Meetings needing MOM (scheduled/completed but no MOM uploaded)
    mom_needed = await db.execute(
        select(func.count(Meeting.id))
        .filter(
            Meeting.mom_summary.is_(None),
            Meeting.status.in_(['completed', 'scheduled']),
            Meeting.is_deleted == False,
        )
    )
    meetings_needing_mom = mom_needed.scalar() or 0

    # Pending extracted task approvals
    pending_approvals = await db.execute(
        select(func.count(MeetingExtractedTask.id))
        .filter(
            MeetingExtractedTask.status == 'pending',
            MeetingExtractedTask.is_deleted == False,
        )
    )
    pending_approvals_count = pending_approvals.scalar() or 0

    # Upcoming deadlines (next 7 days)
    end_date = datetime.now() + __import__('datetime').timedelta(days=7)
    end_date_str = end_date.strftime('%Y-%m-%d')
    upcoming_deadlines = await db.execute(
        select(func.count(Task.id))
        .filter(
            or_(
                Task.deadline.between(today_str, end_date_str),
                Task.scheduled_date.between(today_str, end_date_str)
            ),
            Task.status != 'completed',
            Task.is_deleted == False,
        )
    )
    upcoming_deadlines_count = upcoming_deadlines.scalar() or 0

    data = {
        "activeProjects": active_projects,
        "completedProjects": completed_projects,
        "totalTasks": total_tasks,
        "completedTasks": completed_tasks,
        "todayTasks": today_tasks,
        "overdueTasks": overdue_tasks,
        "todayMeetings": today_meetings,
        "highPriorityTasks": high_priority_tasks,
        "pendingInvitations": pending_invitations,
        "meetingsNeedingMOM": meetings_needing_mom,
        "pendingApprovals": pending_approvals_count,
        "upcomingDeadlines": upcoming_deadlines_count,
    }

    return success_response(data=data, message="Dashboard summary fetched successfully")
