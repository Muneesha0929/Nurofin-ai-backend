import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings
import sys

async def main():
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        try:
            await conn.execute(text("ALTER TABLE issue ADD COLUMN reported_by_id INTEGER"))
            print("Added reported_by_id")
        except Exception as e:
            print(f"Skipped reported_by_id: {e}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
