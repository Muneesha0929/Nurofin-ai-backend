import asyncio
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.api import deps
from app.core.config import settings
from app.core.live_events import subscribe_live_events, unsubscribe_live_events
from app.models.user import User

router = APIRouter()


async def _auth_user_from_token(db: AsyncSession, token: Optional[str]) -> User:
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing authentication token")
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        user = (await db.execute(select(User).filter(User.id == int(user_id), User.is_deleted == False))).scalars().first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@router.get("/live")
async def live_events_stream(
    request: Request,
    token: Optional[str] = Query(None),
    db: AsyncSession = Depends(deps.get_db),
):
    """Server-Sent Events stream notifying active clients whenever tasks, follow-ups, objectives, or issues change."""
    auth_header = request.headers.get("Authorization") or ""
    bearer_token = auth_header.replace("Bearer ", "").strip() if auth_header.startswith("Bearer ") else None
    await _auth_user_from_token(db, token or bearer_token)

    queue = subscribe_live_events()

    async def event_generator():
        try:
            yield "event: connected\ndata: {\"status\":\"ok\"}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=25.0)
                    yield f"event: live\ndata: {msg}\n\n"
                except asyncio.TimeoutError:
                    yield ": ping\n\n"
        finally:
            unsubscribe_live_events(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
