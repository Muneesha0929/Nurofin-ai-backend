import asyncio
import logging
from datetime import datetime, timedelta
from sqlalchemy.future import select
from sqlalchemy import func
from app.db.session import SessionLocal
from app.models.issue import Issue, IssueStatusEnum, IssueAssignmentStatusEnum
from app.models.user import User
from app.models.project import project_members
from app.models.notification import Notification, NotificationTypeEnum

logger = logging.getLogger(__name__)

async def process_issue_timeouts():
    """Background task to automatically decline issues that have been pending for > 1 hour."""
    while True:
        try:
            await asyncio.sleep(300) # Check every 5 minutes
            
            async with SessionLocal() as db:
                timeout_threshold = datetime.utcnow() - timedelta(hours=1)
                
                # Find all pending issues older than 1 hour
                stmt = select(Issue).filter(
                    Issue.assignment_status == IssueAssignmentStatusEnum.pending_acceptance,
                    Issue.assignment_timestamp < timeout_threshold,
                    Issue.assigned_user_id.isnot(None),
                    Issue.is_deleted == False
                )
                result = await db.execute(stmt)
                issues = result.scalars().all()
                
                for issue in issues:
                    logger.info(f"Issue {issue.id} assignment timed out for user {issue.assigned_user_id}")
                    
                    # Add to declined list
                    declined_list = list(issue.declined_by_users) if issue.declined_by_users else []
                    if issue.assigned_user_id not in declined_list:
                        declined_list.append(issue.assigned_user_id)
                    issue.declined_by_users = declined_list
                    
                    old_assignee_id = issue.assigned_user_id
                    
                    # Auto-assign logic
                    next_assignee = None
                    if issue.project_id:
                        members_stmt = select(User).join(project_members, project_members.c.user_id == User.id).filter(
                            project_members.c.project_id == issue.project_id, 
                            User.is_deleted == False,
                            User.id.notin_(declined_list)
                        )
                        project_members = (await db.execute(members_stmt)).scalars().all()
                        
                        if project_members:
                            member_ids = [m.id for m in project_members]
                            issue_counts = await db.execute(
                                select(Issue.assigned_user_id, func.count(Issue.id))
                                .filter(Issue.assigned_user_id.in_(member_ids), Issue.status.in_([IssueStatusEnum.open, IssueStatusEnum.in_progress]))
                                .group_by(Issue.assigned_user_id)
                            )
                            counts_dict = {row[0]: row[1] for row in issue_counts}
                            project_members.sort(key=lambda m: counts_dict.get(m.id, 0))
                            next_assignee = project_members[0]
                            
                    if next_assignee:
                        issue.assigned_user_id = next_assignee.id
                        issue.assignment_timestamp = datetime.utcnow()
                        
                        db.add(Notification(
                            title="New issue assigned to you (Action Required)",
                            message=f"You have been auto-assigned: {issue.title}. Please accept or decline within 1 hour.",
                            type=NotificationTypeEnum.task_assigned,
                            user_id=next_assignee.id,
                            link=f"/issues?id={issue.id}",
                        ))
                    else:
                        issue.assigned_user_id = None
                        issue.assignment_status = None
                        
                    # Notify the old assignee that they missed it
                    db.add(Notification(
                        title="Issue Assignment Timed Out",
                        message=f"You did not respond to '{issue.title}' within 1 hour. It has been reassigned.",
                        type=NotificationTypeEnum.system,
                        user_id=old_assignee_id,
                        link=f"/issues?id={issue.id}",
                    ))
                    
                if issues:
                    await db.commit()
                    
        except Exception as e:
            logger.error(f"Error in process_issue_timeouts: {e}")
            await asyncio.sleep(60)
