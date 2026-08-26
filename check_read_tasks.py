import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.task import Task
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        q = select(Task.id).where(Task.is_deleted == False, Task.parent_id == None)
        tasks = (await db.execute(q)).scalars().all()
        print("Tasks in read_tasks:", len(tasks))
        print("Is 49 in read_tasks?", 49 in tasks)

asyncio.run(main())
