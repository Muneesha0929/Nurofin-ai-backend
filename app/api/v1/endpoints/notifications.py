from typing import Any
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import update

from app.api import deps
from app.models.notification import Notification
from app.models.user import User
from app.schemas.notification import NotificationCreate, Notification as NotificationSchema, NotificationUpdate
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()

@router.get("", response_model=APIResponse)
async def read_notifications(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    result = await db.execute(
        select(Notification)
        .filter(Notification.user_id == current_user.id, Notification.is_deleted == False)
        .order_by(Notification.created_at.desc())
        .offset(skip).limit(limit)
    )
    notifications = result.scalars().all()
    
    data = [NotificationSchema.from_orm(n).dict() for n in notifications]
    return success_response(data=data, message="Notifications fetched successfully")

@router.post("", response_model=APIResponse)
async def create_notification(
    *,
    db: AsyncSession = Depends(deps.get_db),
    notif_in: NotificationCreate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    notif_data = notif_in.dict(exclude_unset=True)
    notif_data.pop("user_id", None)
    db_notif = Notification(**notif_data, user_id=current_user.id)
    db.add(db_notif)
    await db.commit()
    await db.refresh(db_notif)
    
    return success_response(
        data=NotificationSchema.from_orm(db_notif).dict(),
        message="Notification created successfully"
    )

@router.put("/read-all", response_model=APIResponse)
async def mark_all_as_read(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    await db.execute(
        update(Notification)
        .filter(Notification.user_id == current_user.id, Notification.is_read == False)
        .values(is_read=True)
    )
    await db.commit()
    return success_response(message="All notifications marked as read")

@router.delete("/clear-all", response_model=APIResponse)
async def clear_all_notifications(
    *,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    await db.execute(
        update(Notification)
        .filter(Notification.user_id == current_user.id)
        .values(is_deleted=True, is_read=True)
    )
    await db.commit()
    return success_response(message="All notifications cleared")

@router.put("/{id}/read", response_model=APIResponse)
async def mark_as_read(
    *,
    db: AsyncSession = Depends(deps.get_db),
    id: int,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    result = await db.execute(select(Notification).filter(Notification.id == id, Notification.user_id == current_user.id))
    notif = result.scalars().first()
    if not notif:
        return error_response(message="Notification not found")
        
    notif.is_read = True
    await db.commit()
    return success_response(message="Notification marked as read")
