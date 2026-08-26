import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text
import sys

async def run_migration():
    async with SessionLocal() as db:
        try:
            # Check if actual_completion_date exists, if not add it
            try:
                await db.execute(text("ALTER TABLE task ADD COLUMN actual_completion_date VARCHAR;"))
                print("Added actual_completion_date")
            except Exception as e:
                print("actual_completion_date might already exist:", e)
                await db.rollback()
                
            try:
                await db.execute(text("ALTER TABLE task ADD COLUMN extended_time FLOAT;"))
                print("Added extended_time")
            except Exception as e:
                print("extended_time might already exist:", e)
                await db.rollback()
                
            try:
                await db.execute(text("ALTER TABLE task ADD COLUMN pushed_to_next_day BOOLEAN DEFAULT FALSE;"))
                print("Added pushed_to_next_day")
            except Exception as e:
                print("pushed_to_next_day might already exist:", e)
                await db.rollback()
                
            try:
                await db.execute(text("ALTER TABLE task ADD COLUMN idempotency_key VARCHAR;"))
                print("Added idempotency_key")
            except Exception as e:
                print("idempotency_key might already exist:", e)
                await db.rollback()
                
            await db.commit()
            print("Migration successful.")
        except Exception as e:
            print("Fatal error:", e)

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_migration())
