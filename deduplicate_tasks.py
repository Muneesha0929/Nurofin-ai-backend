import asyncio
from app.db.session import SessionLocal
from app.models.task import Task
from sqlalchemy import select, delete

async def deduplicate():
    async with SessionLocal() as db:
        result = await db.execute(select(Task).order_by(Task.id.asc()))
        tasks = result.scalars().all()
        
        seen = {}
        to_delete = []
        
        for task in tasks:
            key = (task.title, task.status, task.priority, task.deadline, task.scheduled_date)
            if key in seen:
                to_delete.append(task.id)
            else:
                seen[key] = task.id
                
        if to_delete:
            await db.execute(delete(Task).where(Task.id.in_(to_delete)))
            await db.commit()
            print(f"Deleted {len(to_delete)} duplicate tasks.")
        else:
            print("No duplicate tasks found.")

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(deduplicate())
