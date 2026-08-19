import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings

async def main():
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        cols = [
            "ALTER TABLE issue ADD COLUMN deadline VARCHAR",
            "ALTER TABLE issue ADD COLUMN assignment_status VARCHAR",
            "ALTER TABLE issue ADD COLUMN assignment_timestamp TIMESTAMP",
            "ALTER TABLE issue ADD COLUMN declined_by_users JSON DEFAULT '[]'::json"
        ]
        for col in cols:
            try:
                await conn.execute(text(col))
                print(f"Executed: {col}")
            except Exception as e:
                print(f"Error on {col}: {e}")

asyncio.run(main())
