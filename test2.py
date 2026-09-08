import asyncio
from app.db.session import SessionLocal
from app.api.v1.endpoints.workcenter import _serialize_task
from app.models.task import Task
from sqlalchemy import select
import sys

# Patch psycopg event loop issue for Windows
import platform
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    try:
        async with SessionLocal() as db:
            res = await db.execute(select(Task).where(Task.parent_id == None, Task.is_deleted == False))
            tasks = res.scalars().all()
            for t in tasks:
                print(f"Serializing {t.id} - {t.title}")
                await _serialize_task(db, t, load_subtasks=True)
            print("SUCCESS")
    except Exception as e:
        print(f"FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
