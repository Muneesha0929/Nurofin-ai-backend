import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
from sqlalchemy import text
from app.db.session import SessionLocal

async def add_columns():
    async with SessionLocal() as db:
        try:
            await db.execute(text("ALTER TABLE \"user\" ADD COLUMN outlook_access_token VARCHAR"))
            print("Added outlook_access_token")
        except Exception as e:
            print(f"Skipped outlook_access_token: {e}")
            
        try:
            await db.execute(text("ALTER TABLE \"user\" ADD COLUMN outlook_refresh_token VARCHAR"))
            print("Added outlook_refresh_token")
        except Exception as e:
            print(f"Skipped outlook_refresh_token: {e}")
            
        try:
            await db.execute(text("ALTER TABLE \"user\" ADD COLUMN outlook_token_expires_at TIMESTAMP WITHOUT TIME ZONE"))
            print("Added outlook_token_expires_at")
        except Exception as e:
            print(f"Skipped outlook_token_expires_at: {e}")
            
        await db.commit()

if __name__ == "__main__":
    asyncio.run(add_columns())
