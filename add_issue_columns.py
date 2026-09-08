import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text

async def main():
    async with SessionLocal() as db:
        queries = [
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS scheduled_start_time VARCHAR;",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS scheduled_end_time VARCHAR;",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS extended_time FLOAT;",
            "ALTER TABLE issue ADD COLUMN IF NOT EXISTS pushed_to_next_day BOOLEAN DEFAULT FALSE;"
        ]
        for q in queries:
            try:
                await db.execute(text(q))
                print(f"Executed: {q}")
            except Exception as e:
                print(f"Failed: {q} - {e}")
        await db.commit()

import platform
if platform.system() == 'Windows':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
asyncio.run(main())
