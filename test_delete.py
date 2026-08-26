import asyncio
from app.db.session import SessionLocal
from app.models.task import Task
from sqlalchemy import select

async def test_del():
    async with SessionLocal() as db:
        res = await db.execute(select(Task).limit(1))
        task = res.scalars().first()
        if not task:
            print("No tasks found")
            return
        
        print(f"Task {task.id}: is_deleted={task.is_deleted}")
        task.is_deleted = True
        await db.commit()
        
        res = await db.execute(select(Task).filter(Task.id == task.id))
        task2 = res.scalars().first()
        print(f"Task {task.id}: is_deleted={task2.is_deleted}")

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test_del())
