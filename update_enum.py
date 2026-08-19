import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from app.core.config import settings

async def main():
    # Use isolation_level="AUTOCOMMIT" because ALTER TYPE cannot run inside a transaction block in Postgres
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI, isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        for val in ["issue_assigned", "issue_followup", "issue_status_changed", "performance_reviewed"]:
            try:
                print(f"Adding {val}...")
                await conn.execute(text(f"ALTER TYPE notificationtypeenum ADD VALUE '{val}';"))
                print(f"Added {val}")
            except Exception as e:
                print(f"Skipped {val}: {e}")

if __name__ == "__main__":
    asyncio.run(main())
