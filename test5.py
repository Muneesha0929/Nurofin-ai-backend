import asyncio
import json
from app.db.session import SessionLocal
from app.models.task import Task
from app.models.user import User
from sqlalchemy import select
from app.api.v1.endpoints.workcenter import read_tasks
import sys
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
            res = await read_tasks(db=db, current_user=current_user, page=1, page_size=200)
            data_dict = res.body.decode('utf-8')
            # Check if JSON serializable
            json.loads(data_dict)
            print("SUCCESS read_tasks JSON")
    except Exception as e:
        print(f"FAILED WITH EXCEPTION: {e}")
        import traceback
        traceback.print_exc()

asyncio.run(main())
