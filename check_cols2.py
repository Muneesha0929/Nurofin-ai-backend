import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text
import sys

async def check():
    async with SessionLocal() as db:
        res = await db.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name = 'task';"))
        cols = [r[0] for r in res.fetchall()]
        print("SQLAlchemy sees:", cols)
        
        # Test direct query
        try:
            await db.execute(text("SELECT extended_time FROM task LIMIT 1"))
            print("Direct query for extended_time worked!")
        except Exception as e:
            print("Direct query failed:", e)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(check())
