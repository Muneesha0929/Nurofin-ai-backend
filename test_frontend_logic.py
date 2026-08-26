import asyncio
from datetime import datetime, timedelta
from sqlalchemy.future import select
from app.db.session import SessionLocal
from app.models.user import User
from app.api.v1.endpoints.tasks import read_tasks
from fastapi.encoders import jsonable_encoder
import json

async def run():
    async with SessionLocal() as db:
        user = (await db.execute(select(User).limit(1))).scalars().first()
        res = await read_tasks(db=db, current_user=user)
        json_data = jsonable_encoder(res.data)
        
        today = datetime.now()
        yesterday = today - timedelta(days=1)
        today_str = today.strftime('%Y-%m-%d')
        yesterday_str = yesterday.strftime('%Y-%m-%d')
        
        pending = []
        completed = []
        
        for t in json_data:
            t_date = t.get('scheduled_date')
            if not t_date:
                dd = t.get('deadline')
                t_date = dd.split('T')[0] if dd else None
                
            status = t.get('status')
            
            if status != 'completed' and t_date and t_date < today_str:
                pending.append(t['title'])
                
            actual_comp = t.get('actual_completion_date')
            comp_date = actual_comp.split('T')[0] if actual_comp else t_date
            if status == 'completed' and comp_date == yesterday_str:
                completed.append(t['title'])
                
        print(f"Pending tasks before today ({len(pending)}):")
        for p in pending: print(" -", p)
        
        print(f"\nCompleted tasks exactly yesterday ({len(completed)}):")
        for c in completed: print(" -", c)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run())
