import asyncio
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.task import Task
from app.models.issue import Issue

async def run():
    async with SessionLocal() as db:
        tasks = (await db.execute(select(Task))).scalars().all()
        print("Tasks with Issue in title:")
        for t in tasks:
            if "Issue" in (t.title or ""):
                print(f"Task ID: {t.id}, Title: {t.title}, Status: {t.status}")
                
        issues = (await db.execute(select(Issue))).scalars().all()
        print("\nAll Issues:")
        for i in issues:
            print(f"Issue ID: {i.id}, Title: {i.title}, Status: {i.status}")

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
