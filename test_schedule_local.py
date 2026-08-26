import asyncio
from app.db.session import SessionLocal
from sqlalchemy.future import select
from app.models.user import User
from app.models.meeting import Meeting, MeetingParticipant
from datetime import datetime, timezone, timedelta

async def test():
    async with SessionLocal() as db:
        target_user_id = 3
        start_date = "2026-07-27"
        end_date = "2026-09-25"
        
        result = await db.execute(select(User).filter(User.id == target_user_id))
        target_user = result.scalars().first()
        if not target_user:
            print("User not found")
            return
            
        time_min = datetime.fromisoformat(start_date + "T00:00:00+00:00")
        time_max = datetime.fromisoformat(end_date + "T23:59:59+00:00")
        
        schedule = []
        
        local_meetings_query = (
            select(Meeting)
            .join(MeetingParticipant, MeetingParticipant.meeting_id == Meeting.id)
            .filter(MeetingParticipant.user_id == target_user.id)
            .filter(Meeting.is_deleted == False)
        )
        if start_date:
            local_meetings_query = local_meetings_query.filter(Meeting.date >= start_date)
        if end_date:
            local_meetings_query = local_meetings_query.filter(Meeting.date <= end_date)
            
        local_meetings_result = await db.execute(local_meetings_query)
        local_meetings = local_meetings_result.scalars().all()
        print("Got local meetings:", len(local_meetings))
        
        for m in local_meetings:
            print(m)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
