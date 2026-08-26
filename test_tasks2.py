import asyncio
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.user import User
from app.api.v1.endpoints.tasks import read_tasks

async def run():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        res = await read_tasks(db=db, current_user=user)
        for t in res.data:
            if "Infrastreuture" in t['title'] or "Update Planner" in t['title']:
                print("Found:", t['title'], t['status'], t.get('scheduled_date'), t.get('deadline'))

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
