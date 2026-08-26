import asyncio
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.user import User
from app.api.v1.endpoints.tasks import read_tasks
from fastapi.encoders import jsonable_encoder
import json

async def run():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        res = await read_tasks(db=db, current_user=user)
        json_data = jsonable_encoder(res.data)
        for t in json_data:
            if "Update Planner" in t['title']:
                print(json.dumps(t, indent=2))
                
if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
