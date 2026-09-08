import asyncio
from app.db.session import SessionLocal
from app.api.v1.endpoints.workcenter import _serialize_task
from app.models.task import Task
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        res = await db.execute(select(Task).where(Task.parent_id == None, Task.is_deleted == False))
        tasks = res.scalars().all()
        for t in tasks:
            print(f"Serializing {t.id} - {t.title}")
            try:
                await _serialize_task(db, t, load_subtasks=True, cache={'users':{}, 'projects':{}})
            except Exception as e:
                print(f"Error on {t.id}: {e}")

asyncio.run(main())
