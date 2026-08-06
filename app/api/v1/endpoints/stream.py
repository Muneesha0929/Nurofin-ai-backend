from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from fastapi.concurrency import run_in_threadpool
import os
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from dotenv import load_dotenv
from stream_chat import StreamChat

from app.api import deps
from app.models.user import User
from app.core.responses import APIResponse, success_response, error_response

load_dotenv(".env")

router = APIRouter()

@router.get("/token", response_model=APIResponse)
async def get_stream_token(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db)
) -> Any:
    """
    Generate a Stream Chat token for the current user and sync all users.
    """
    api_key = os.getenv("STREAM_API_KEY")
    api_secret = os.getenv("STREAM_API_SECRET")

    if not api_key or not api_secret:
        raise HTTPException(
            status_code=500, 
            detail="Stream API credentials not configured on the server."
        )

    try:
        # Initialize Stream Chat Client as sync
        server_client = StreamChat(api_key=api_key, api_secret=api_secret)
        
        # Fetch all users from the DB so Stream knows they exist before channels are created
        result = await db.execute(select(User).filter(User.is_deleted == False))
        all_users = result.scalars().all()
        
        users_data = []
        for u in all_users:
            u_data = {
                "id": str(u.id),
                "name": u.full_name,
                "role": "user", # Standard stream chat user role
            }
            if u.profile_picture and len(u.profile_picture) < 4000:
                u_data["image"] = u.profile_picture
            users_data.append(u_data)
            
        # Run the synchronous upsert_users in a threadpool so it doesn't block the async event loop
        await run_in_threadpool(server_client.upsert_users, users_data)

        # Generate a secure token
        user_id = str(current_user.id)
        token = server_client.create_token(user_id)
        
        return success_response(
            data={"token": token, "user_id": user_id},
            message="Token generated successfully"
        )
    except Exception as e:
        print(f"Error generating Stream token: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to generate Stream token: {str(e)}")
