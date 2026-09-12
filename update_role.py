from sqlalchemy import select
from app.db.session import SessionLocal
from app.models.user import User
import asyncio

async def main():
    async with SessionLocal() as db:
        result = await db.execute(select(User).filter(User.username == "Pranesh" or User.name == "Pranesh" or User.email.ilike("%pranesh%")))
        users = result.scalars().all()
        for u in users:
            print(f"Found user: {u.name} (id: {u.id}, role: {u.role})")
            if u.name == "Pranesh":
                u.role = "team_lead"
                db.add(u)
                await db.commit()
                print("Updated Pranesh to team_lead")

asyncio.run(main())
