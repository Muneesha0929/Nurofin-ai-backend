import asyncio
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker
from sqlalchemy import select
from app.models.task import Task
import os
from dotenv import load_dotenv

load_dotenv()
engine = create_async_engine(os.getenv("DATABASE_URL").replace("postgresql://", "postgresql+asyncpg://"))
async_session = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

async def test():
    async with async_session() as db:
        res = await db.execute(select(Task).limit(10).order_by(Task.id.desc()))
        tasks = res.scalars().all()
        for t in tasks:
            print(f"Task {t.id}: title='{t.title}', deadline='{t.deadline}', scheduled_date='{t.scheduled_date}', is_deleted={t.is_deleted}")

asyncio.run(test())
