import asyncio
import sys
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from app.db.session import SessionLocal
from app.models.user import User
from sqlalchemy import select

async def main():
    async with SessionLocal() as db:
        try:
            print("Fetching user for login...")
            result = await db.execute(select(User).filter(User.email == "muneesha09@gmail.com"))
            user = result.scalars().first()
            if user:
                print(f"Success! Found user {user.email}.")
            else:
                print("User not found.")
        except Exception as e:
            print(f"Error querying User DB: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
