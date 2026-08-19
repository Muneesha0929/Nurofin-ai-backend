import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from datetime import datetime
from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.user import User
from app.models.issue import Issue, IssueStatusEnum, IssueAssignmentStatusEnum
from app.models.notification import Notification, NotificationTypeEnum

async def seed_test_issue():
    async with SessionLocal() as db:
        # Find User by email
        stmt = select(User).filter(User.email == "muneesha09@gmail.com")
        result = await db.execute(stmt)
        user = result.scalars().first()
        
        if not user:
            print("User Nebisha not found!")
            return
            
        from sqlalchemy import text
        import json
        
        # Raw SQL to bypass SQLAlchemy unmigrated model columns
        issue_sql = text("""
            INSERT INTO issue (title, description, category, priority, status, assignment_status, assignment_timestamp, assigned_user_id, reported_by_id, created_at, updated_at, is_deleted)
            VALUES (:title, :desc, :cat, :pri, :status, :assign_status, :assign_time, :user_id, :user_id, :now, :now, false)
            RETURNING id
        """)
        
        result_issue = await db.execute(issue_sql, {
            "title": "Test Trial Issue for Accept/Decline Flow",
            "desc": "This is an automated test issue to check the accept/decline UI.",
            "cat": "Testing",
            "pri": "medium",
            "status": "open",
            "assign_status": "pending_acceptance",
            "assign_time": datetime.utcnow(),
            "user_id": user.id,
            "now": datetime.utcnow()
        })
        new_issue_id = result_issue.scalar()
        
        notif_sql = text("""
            INSERT INTO notification (title, message, type, is_read, created_at, link, user_id, updated_at, is_deleted)
            VALUES (:title, :msg, :type, false, :now, :link, :user_id, :now, false)
        """)
        await db.execute(notif_sql, {
            "title": "New issue assigned to you (Action Required)",
            "msg": "You have been assigned a test issue. Please accept or decline.",
            "type": "task_assigned",
            "now": datetime.utcnow(),
            "link": f"/issues?id={new_issue_id}",
            "user_id": user.id
        })
        
        await db.commit()
        print(f"Successfully created test issue #{new_issue_id} assigned to {user.full_name}!")

if __name__ == "__main__":
    asyncio.run(seed_test_issue())
