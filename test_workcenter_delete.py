import asyncio
from app.db.session import SessionLocal
from app.api.v1.endpoints.workcenter import delete_task
from app.models.user import User

async def run():
    async with SessionLocal() as db:
        user = User(id=1)
        # Try to delete task 45
        try:
            res = await delete_task(db=db, task_id=45, current_user=user)
            print("Response:", res)
        except Exception as e:
            print("Error:", e)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
