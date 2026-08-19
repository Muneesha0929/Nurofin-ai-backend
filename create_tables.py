import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from app.db.session import engine
from app.db.base_class import Base
from app.models.issue import Issue
from app.models.issue_followup import IssueFollowup

async def main():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    print("Tables created")

asyncio.run(main())
