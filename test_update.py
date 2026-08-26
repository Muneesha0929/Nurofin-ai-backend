import asyncio
from app.db.session import SessionLocal
from sqlalchemy.future import select
from app.models.task import Task
from datetime import datetime

async def test():
    async with SessionLocal() as db:
        task = (await db.execute(select(Task).limit(1))).scalars().first()
        if task:
            task.actual_completion_date = "2026-08-26"
            try:
                await db.commit()
                print("Success!")
            except Exception as e:
                print("Error:", type(e).__name__, e)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
