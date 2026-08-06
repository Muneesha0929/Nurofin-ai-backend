import asyncio
import sys
import aiohttp

if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

async def test_workcenter():
    token = ""
    # We will log in to get a token
    async with aiohttp.ClientSession() as session:
        # Login
        data = {'username': 'muneesha09@gmail.com', 'password': 'Muneesha09@'}
        async with session.post('http://127.0.0.1:8000/api/v1/auth/login', data=data) as resp:
            if resp.status != 200:
                print("Login failed", await resp.text())
                return
            res_json = await resp.json()
            token = res_json['access_token']
        
        headers = {'Authorization': f'Bearer {token}'}
        
        endpoints = [
            '/api/v1/workcenter/tasks',
            '/api/v1/workcenter/summary',
            '/api/v1/workcenter/insights',
            '/api/v1/workcenter/quarters'
        ]
        
        for ep in endpoints:
            async with session.get(f'http://127.0.0.1:8000{ep}', headers=headers) as r:
                print(f"GET {ep} -> {r.status}")
                if r.status != 200:
                    print(await r.text())

if __name__ == '__main__':
    asyncio.run(test_workcenter())
