import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.document import Document, DocumentUserAccess
from app.models.project import Project
from app.models.task import Task
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        try:
            print("Fetching documents...")
            result = await db.execute(select(Document).filter(Document.is_deleted == False))
            docs = result.scalars().all()
            print(f"Success! Found {len(docs)} documents.")
            
            print("Fetching tasks...")
            result = await db.execute(select(Task))
            tasks = result.scalars().all()
            print(f"Success! Found {len(tasks)} tasks.")
            
            print("Fetching projects...")
            result = await db.execute(select(Project))
            projects = result.scalars().all()
            print(f"Success! Found {len(projects)} projects.")
        except Exception as e:
            print(f"Error querying DB: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
