import asyncio
import os
import json
import httpx
from sqlalchemy.future import select
from app.main import app
from app.api import deps
from app.models.user import User
from app.models.meeting import Meeting
from app.db.session import SessionLocal

async def mock_get_current_user():
    async with SessionLocal() as db:
        res = await db.execute(select(User).limit(1))
        user = res.scalars().first()
        if not user:
            user = User(email="test_user@nurofin.com", full_name="Test User", hashed_password="pw")
            db.add(user)
            await db.commit()
            await db.refresh(user)
        return user

app.dependency_overrides[deps.get_current_user] = mock_get_current_user

# Mock httpx.AsyncClient.post globally to avoid hitting the real AI_ENGINE_URL
class MockResponse:
    def __init__(self, json_data, status_code=200):
        self.json_data = json_data
        self.status_code = status_code

    def json(self):
        return self.json_data
        
    def raise_for_status(self):
        if self.status_code >= 400:
            raise Exception(f"HTTP Error {self.status_code}")

original_post = httpx.AsyncClient.post

async def mock_post(self, url, *args, **kwargs):
    print(f"MOCK_POST CALLED WITH URL: {url}")
    if "mock-ai-engine" in str(url):
        print(" -> Returning MockResponse")
        return MockResponse({
            "response": json.dumps({
                "summary": "This is a mocked summary",
                "key_points": ["Point 1"],
                "participants": ["Test User"],
                "action_items": [
                    {
                        "title": "Mocked Action Item",
                        "description": "Mocked desc",
                        "priority": "high",
                        "suggested_owner": "Test User",
                        "deadline": "2026-12-31",
                        "dependencies": []
                    }
                ],
                "decisions": ["Mocked Decision"],
                "risks": ["Mocked Risk"],
                "blockers": [],
                "followups": [{"description": "Mocked Followup", "assigned_to": "Test User", "deadline": "2026-12-31"}],
                "questions": []
            })
        })
    return await original_post(self, url, *args, **kwargs)

httpx.AsyncClient.post = mock_post

async def run_tests():
    print("Starting automated tests for Meeting Intelligence...")
    
    # Set dummy AI engine URL so backend doesn't complain
    os.environ["AI_ENGINE_URL"] = "http://mock-ai-engine"

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        # 1. Create meeting
        print("1. Creating meeting...")
        res = await client.post("/api/v1/meetings", json={
            "title": "Automated Test Meeting",
            "date": "2026-10-10",
            "start_time": "10:00",
            "end_time": "11:00",
            "notes": "Testing the pipeline"
        })
        if res.status_code != 200:
            print("Failed to create meeting:", res.json())
            return
        
        meeting = res.json()["data"]
        meeting_id = meeting["id"]
        print(f"   -> Success! Created meeting ID: {meeting_id}")
        
        # 2. Upload MOM
        print("2. Uploading MOM...")
        res = await client.post(f"/api/v1/meetings/{meeting_id}/mom", json={
            "summary": "This is the transcript of the automated test meeting."
        })
        if res.status_code != 200:
            print("Failed to upload MOM:", res.json())
            return
        print("   -> Success! MOM uploaded.")
        
        # 3. Analyze meeting
        print("3. Analyzing meeting using AI...")
        res = await client.post(f"/api/v1/meetings/{meeting_id}/analyze-enhanced")
        if res.status_code != 200:
            print("Failed to analyze meeting:", res.json())
            return
        
        json_resp = res.json()
        if not json_resp.get("data"):
            print("Failed to analyze meeting (no data):", json_resp)
            return
            
        analysis = json_resp["data"]
        extracted_tasks = analysis.get("extracted_tasks", [])
        if not extracted_tasks or len(extracted_tasks) == 0:
            print("Failed: No tasks were extracted.")
            return
            
        print("   -> Success! Analysis completed.")
        print(f"   -> Found {len(extracted_tasks)} action items.")
        
        task_id = extracted_tasks[0]["id"]
        print(f"   -> Task ID to confirm: {task_id}")
        
        # 4. Confirm tasks
        print("4. Confirming tasks to map them to real system tasks...")
        res = await client.post(f"/api/v1/meetings/{meeting_id}/confirm-tasks", json={
            "tasks": [{
                "extracted_task_id": task_id
            }]
        })
        if res.status_code != 200:
            print("Failed to confirm tasks:", res.json())
            return
            
        confirmed = res.json()["data"]
        if confirmed.get("count", 0) != 1:
            print("Failed: Tasks were not created.")
            return
        print("   -> Success! Tasks confirmed and assigned.")
        
        # 5. Check structured data
        print("5. Verifying follow-ups and structured data extraction...")
        res = await client.get(f"/api/v1/meetings/{meeting_id}/structured-data")
        if res.status_code != 200:
            print("Failed to get structured data:", res.json())
            return
            
        structured = res.json()["data"]
        if not structured.get("followups") or len(structured["followups"]) == 0:
            print("Failed: Follow-ups were not saved.")
            return
            
        print("   -> Success! Follow-ups verified.")

    print("\n[SUCCESS] All automated integration tests for Meeting Intelligence passed successfully!")

if __name__ == "__main__":
    asyncio.run(run_tests())
