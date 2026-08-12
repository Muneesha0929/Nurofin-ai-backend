import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.document import DocumentUserAccess
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        try:
            print("Checking if DocumentUserAccess exists...")
            result = await db.execute(select(DocumentUserAccess).limit(1))
            docs = result.scalars().all()
            print("Success! Table exists.")
        except Exception as e:
            print(f"Error: {e}")

if __name__ == "__main__":
    asyncio.run(main())
