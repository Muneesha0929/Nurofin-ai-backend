import asyncio
from app.db.session import SessionLocal
from sqlalchemy import text

async def add_column():
    async with SessionLocal() as db:
        try:
            await db.execute(text("ALTER TABLE task ADD COLUMN idempotency_key VARCHAR UNIQUE;"))
            await db.execute(text("CREATE INDEX ix_task_idempotency_key ON task (idempotency_key);"))
            await db.commit()
            print("Added idempotency_key column.")
        except Exception as e:
            print(f"Error adding column: {e}")

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(add_column())
