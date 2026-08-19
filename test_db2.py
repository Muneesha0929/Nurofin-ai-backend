import asyncio
from app.db.session import SessionLocal
from app.models.issue import Issue
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from app.models.issue_followup import IssueFollowup
from app.api.v1.endpoints.issues import _serialize_issue
async def run():
    db = SessionLocal()
    q = select(Issue).options(selectinload(Issue.project), selectinload(Issue.assigned_user), selectinload(Issue.reported_by), selectinload(Issue.followups).selectinload(IssueFollowup.user))
    res = await db.execute(q)
    issues = res.scalars().all()
    for issue in issues:
        try:
            _serialize_issue(db, issue)
        except Exception as e:
            print('Error on issue', issue.id, e)
    print('Done checking issues')
    await db.close()
asyncio.run(run())
