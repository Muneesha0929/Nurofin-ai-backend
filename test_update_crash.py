import asyncio
from app.db.session import SessionLocal
from sqlalchemy.future import select
from app.models.task import Task
from app.api.v1.endpoints.tasks import update_task
from app.schemas.task import TaskUpdate
from app.models.user import User

async def test():
    async with SessionLocal() as db:
        user = User(id=3)
        # Find any task
        result = await db.execute(select(Task).limit(1))
        task = result.scalars().first()
        if not task:
            print("No tasks found")
            return
            
        task_update = TaskUpdate(
            status="completed",
            actual_completion_date="2026-08-26"
        )
        
        try:
            res = await update_task(db=db, id=task.id, task_in=task_update, current_user=user)
            print("Success:", res)
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
