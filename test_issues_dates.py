import asyncio
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.issue import Issue

async def run():
    async with SessionLocal() as db:
        issues = (await db.execute(select(Issue))).scalars().all()
        for i in issues:
            print(f"Issue {i.id}: status={i.status}, deadline={i.deadline}, created_at={i.created_at}")

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
