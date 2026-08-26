import asyncio
from app.db.session import SessionLocal
from app.core.security import create_access_token
import urllib.request
import urllib.error
import json

async def test():
    token = create_access_token("3")
    url = "http://localhost:8000/api/v1/planner/schedule/3?start_date=2026-07-27&end_date=2026-09-25"
    try:
        req = urllib.request.Request(url, headers={'Authorization': f'Bearer {token}'})
        with urllib.request.urlopen(req) as response:
            print(response.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(e.code)
        print(e.read().decode('utf-8'))
    except Exception as e:
        print(e)

if __name__ == "__main__":
    import sys
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(test())
