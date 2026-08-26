import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.task import Task
from app.models.user import User
from app.api.v1.endpoints.workcenter import update_task, delete_task
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).filter(User.id == 2))).scalars().first()
        tasks = (await db.execute(select(Task).filter(Task.status != 'completed', Task.deadline < '2026-08-25'))).scalars().all()
        
        for t in tasks:
            try:
                res = await delete_task(db=db, current_user=user, task_id=t.id)
                print(f"Task {t.id} delete: {res}")
            except Exception as e:
                print(f"Task {t.id} delete failed: {e}")
                
            # Rollback to not actually delete it for the user
            await db.rollback()

asyncio.run(main())
