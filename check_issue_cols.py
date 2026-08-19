import asyncio
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy import inspect
from app.core.config import settings
import sys

async def main():
    engine = create_async_engine(settings.SQLALCHEMY_DATABASE_URI)
    async with engine.connect() as conn:
        def get_cols(connection):
            insp = inspect(connection)
            return insp.get_columns('issue')
        
        cols = await conn.run_sync(get_cols)
        for col in cols:
            print(f"{col['name']}: {col['type']}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
