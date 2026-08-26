import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text
import sys

async def run():
    async with SessionLocal() as db:
        queries = [
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS actual_completion_date VARCHAR;",
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS extended_time FLOAT;",
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS pushed_to_next_day BOOLEAN DEFAULT FALSE;",
            "ALTER TABLE task ADD COLUMN IF NOT EXISTS idempotency_key VARCHAR;"
        ]
        for q in queries:
            try:
                await db.execute(text(q))
                print("Executed via SA:", q)
            except Exception as e:
                print("Error via SA:", e)
        await db.commit()

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
