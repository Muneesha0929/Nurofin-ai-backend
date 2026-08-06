import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from sqlalchemy import select, text
from app.models.user import User

async def check_db():
    async with SessionLocal() as session:
        # Check database connection info
        result = await session.execute(text("SELECT inet_server_addr(), current_database();"))
        db_info = result.fetchone()
        
        # Check user
        result = await session.execute(select(User).where(User.email == "muneesha09@gmail.com"))
        user = result.scalar_one_or_none()
        
        print(f"Database server address: {db_info[0]}")
        print(f"Database name: {db_info[1]}")
        
        if user:
            print(f"User ID: {user.id}")
            print(f"User email: {user.email}")
            print(f"Password Hash (first 10 chars): {user.hashed_password[:10]}...")
        else:
            print("User not found in this database.")

if __name__ == '__main__':
    asyncio.run(check_db())
