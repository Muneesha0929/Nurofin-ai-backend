import asyncio
from app.db.session import SessionLocal
from app.api.v1.endpoints.planner import get_user_schedule
from app.models.user import User

async def test():
    async with SessionLocal() as db:
        user = User(id=3) # Dummy user
        try:
            res = await get_user_schedule(target_user_id=3, start_date="2026-07-27", end_date="2026-09-25", db=db, current_user=user)
            print("Success")
        except Exception as e:
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
