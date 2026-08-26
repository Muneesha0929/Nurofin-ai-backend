import asyncio
from datetime import datetime, timedelta
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.issue import Issue, IssueStatusEnum, IssueAssignmentStatusEnum
from app.models.user import User

async def run():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        yesterday = datetime.now() - timedelta(days=1)
        
        new_issue = Issue(
            title="Fix login page UI (Mock Issue)",
            description="Testing yesterday's overview for resolved issues.",
            category="Frontend",
            priority="High",
            status=IssueStatusEnum.resolved,
            deadline=yesterday,
            assignment_status=IssueAssignmentStatusEnum.accepted,
            assigned_user_id=user.id,
            reported_by_id=user.id
        )
        db.add(new_issue)
        await db.commit()
        print("Mock issue created with deadline:", yesterday.strftime('%Y-%m-%d'))

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
