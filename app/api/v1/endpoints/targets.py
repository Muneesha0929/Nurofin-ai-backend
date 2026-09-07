from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from typing import List, Any
from datetime import datetime

from app.api import deps
from app.models.user import User
from app.models.target import Target
from app.models.target_permission import TargetPermission
from app.schemas.target import (
    TargetCreate,
    TargetUpdate,
    TargetResponse,
    TargetScoreUpdate,
    TargetPermissionCreate,
    TargetPermissionResponse
)

router = APIRouter()

def is_ceo_or_admin(user: User) -> bool:
    return user.role in ["ceo", "super_admin", "CEO"] or user.id == 1

async def can_score_user(db: AsyncSession, current_user: User, target_user_id: int) -> bool:
    if is_ceo_or_admin(current_user):
        return True
    result = await db.execute(
        select(TargetPermission).filter(
            TargetPermission.grantee_id == current_user.id,
            TargetPermission.target_user_id == target_user_id,
            TargetPermission.can_score == True
        )
    )
    return result.scalars().first() is not None

async def can_add_target(db: AsyncSession, current_user: User, target_user_id: int) -> bool:
    if is_ceo_or_admin(current_user):
        return True
    if current_user.id == target_user_id:
        return True
    result = await db.execute(
        select(TargetPermission).filter(
            TargetPermission.grantee_id == current_user.id,
            TargetPermission.target_user_id == target_user_id,
            TargetPermission.can_add_targets == True
        )
    )
    return result.scalars().first() is not None


@router.post("", response_model=TargetResponse)
async def create_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_in: TargetCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    has_permission = await can_add_target(db, current_user, target_in.user_id)
    if not has_permission:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    target = Target(
        title=target_in.title,
        description=target_in.description,
        month=target_in.month,
        is_global=target_in.is_global,
        user_id=target_in.user_id,
        created_by_id=current_user.id
    )
    db.add(target)
    await db.commit()
    await db.refresh(target)
    
    target.score = None
    return target


@router.get("/my", response_model=List[TargetResponse])
async def read_my_targets(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).filter(Target.user_id == current_user.id).offset(skip).limit(limit))
    targets = result.scalars().all()
    # Mask scores for self
    for t in targets:
        t.score = None
        t.scored_by_id = None
    return targets


@router.get("/user/{user_id}", response_model=List[TargetResponse])
async def read_user_targets(
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    has_permission = await can_score_user(db, current_user, user_id)
    if not has_permission:
        if current_user.id == user_id:
            return await read_my_targets(db, skip, limit, current_user)
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    result = await db.execute(select(Target).filter(Target.user_id == user_id).offset(skip).limit(limit))
    return result.scalars().all()


@router.patch("/{target_id}", response_model=TargetResponse)
async def update_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    target_in: TargetUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    if target.user_id != current_user.id and not is_ceo_or_admin(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if target_in.title is not None:
        target.title = target_in.title
    if target_in.description is not None:
        target.description = target_in.description
    if target_in.is_completed is not None:
        target.is_completed = target_in.is_completed
        if target.is_completed:
            target.completed_at = datetime.utcnow()
        else:
            target.completed_at = None

    db.add(target)
    await db.commit()
    await db.refresh(target)
    
    if not is_ceo_or_admin(current_user):
        target.score = None
        
    return target


@router.patch("/{target_id}/score", response_model=TargetResponse)
async def score_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    score_in: TargetScoreUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    has_permission = await can_score_user(db, current_user, target.user_id)
    if not has_permission:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    target.score = score_in.score
    target.scored_by_id = current_user.id
    db.add(target)
    await db.commit()
    await db.refresh(target)
    return target


@router.post("/permissions", response_model=TargetPermissionResponse)
async def create_permission(
    *,
    db: AsyncSession = Depends(deps.get_db),
    perm_in: TargetPermissionCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not is_ceo_or_admin(current_user):
        raise HTTPException(status_code=403, detail="Only CEO/Admin can delegate permissions")
        
    perm = TargetPermission(
        grantee_id=perm_in.grantee_id,
        target_user_id=perm_in.target_user_id,
        can_score=perm_in.can_score,
        can_add_targets=perm_in.can_add_targets,
        granted_by_id=current_user.id
    )
    db.add(perm)
    await db.commit()
    await db.refresh(perm)
    return perm


@router.get("/permissions", response_model=List[TargetPermissionResponse])
async def read_permissions(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if is_ceo_or_admin(current_user):
        result = await db.execute(select(TargetPermission).offset(skip).limit(limit))
    else:
        result = await db.execute(select(TargetPermission).filter(TargetPermission.grantee_id == current_user.id).offset(skip).limit(limit))
    return result.scalars().all()
