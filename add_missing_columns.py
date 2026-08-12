import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from sqlalchemy import text

async def main():
    async with SessionLocal() as db:
        queries = [
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS salary FLOAT DEFAULT 0.0;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS performance_score FLOAT DEFAULT 0.0;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS google_access_token VARCHAR;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS google_refresh_token VARCHAR;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS google_token_expires_at TIMESTAMP WITHOUT TIME ZONE;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS created_at TIMESTAMP WITHOUT TIME ZONE;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP WITHOUT TIME ZONE;',
            'ALTER TABLE "user" ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;',
            'ALTER TABLE document ADD COLUMN IF NOT EXISTS is_deleted BOOLEAN DEFAULT FALSE;'
        ]
        
        try:
            for q in queries:
                await db.execute(text(q))
            await db.commit()
            print("Successfully added missing database columns to user and document tables.")
        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
