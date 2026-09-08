import asyncio
from app.db.session import SessionLocal
from app.models.task import Task
from app.models.user import User
from sqlalchemy import select
from app.api.v1.endpoints.tasks import read_tasks
import sys

# Patch psycopg event loop issue for Windows
import platform
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def main():
    try:
        async with SessionLocal() as db:
            current_user = (await db.execute(select(User).where(User.id == 3))).scalars().first()
            if not current_user:
                print("No user 3")
                return
            await read_tasks(db=db, current_user=current_user)
            print("SUCCESS read_tasks")
    except Exception as e:
        print(f"FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
