import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import text
from dotenv import load_dotenv
import os

load_dotenv()
DATABASE_URL = os.getenv('DATABASE_URL')
# Patch DATABASE_URL for asyncpg if needed
if DATABASE_URL.startswith('postgresql://'):
    DATABASE_URL = DATABASE_URL.replace('postgresql://', 'postgresql+asyncpg://', 1)

async def patch():
    engine = create_async_engine(DATABASE_URL)
    async with engine.begin() as conn:
        try:
            await conn.execute(text('ALTER TABLE "user" ADD COLUMN salary FLOAT DEFAULT NULL'))
            print('Added salary')
        except Exception as e: print(e)
        
        try:
            await conn.execute(text('ALTER TABLE "user" ADD COLUMN performance_score FLOAT DEFAULT NULL'))
            print('Added performance_score')
        except Exception as e: print(e)

asyncio.run(patch())
