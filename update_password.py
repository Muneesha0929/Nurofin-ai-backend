import asyncio
import sys

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from sqlalchemy import select
from app.models.user import User
from app.core.security import get_password_hash

async def update_password():
    async with SessionLocal() as session:
        result = await session.execute(select(User).where(User.email == "muneesha09@gmail.com"))
        user = result.scalar_one_or_none()
        if user:
            user.hashed_password = get_password_hash("Muneesha09@")
            await session.commit()
            print("Password updated successfully.")
        else:
            print("User not found.")

if __name__ == '__main__':
    asyncio.run(update_password())
