from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_
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


from sqlalchemy.orm import selectinload
from app.models.target_score import TargetScore

def get_user_role_str(user: User) -> str:
    if not user:
        return ""
    role = getattr(user, 'role', '')
    if hasattr(role, 'value'):
        return str(role.value).lower()
    return str(role).lower()

def is_ceo_or_admin(user: User) -> bool:
    if user.id == 1:
        return True
    return get_user_role_str(user) in ["ceo", "super_admin", "admin"]

def can_manage_all_targets(user: User) -> bool:
    if is_ceo_or_admin(user):
        return True
    return get_user_role_str(user) in ["team_lead", "lead"]

async def can_score_user(db: AsyncSession, current_user: User, target_user_id: int) -> bool:
    if can_manage_all_targets(current_user) or current_user.id == target_user_id:
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
    if can_manage_all_targets(current_user):
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

def prepare_target_response(target: Target, current_user: User, scores: List[TargetScore] = None):
    target_scores = scores if scores is not None else (getattr(target, 'scores', []) or [])
    score_count = len(target_scores)
    average_score = sum([float(s.score) for s in target_scores]) / score_count if score_count > 0 else None
    
    current_user_id = getattr(current_user, 'id', None)
    my_score = None
    for s in target_scores:
        if s.reviewer_id == current_user_id:
            my_score = float(s.score)
            
    reviewer_scores = [
        {"reviewer_id": s.reviewer_id, "score": float(s.score)}
        for s in target_scores
    ]

    data = {
        "id": target.id,
        "title": target.title,
        "description": target.description,
        "user_id": target.user_id,
        "created_by_id": target.created_by_id,
        "month": target.month,
        "is_global": target.is_global,
        "is_completed": target.is_completed,
        "completed_at": target.completed_at,
        "average_score": average_score,
        "score_count": score_count,
        "my_score": my_score,
        "reviewer_scores": reviewer_scores
    }
    return data

@router.post("", response_model=Any)
async def create_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_in: TargetCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    is_eval_target = (target_in.title or "").strip().startswith("[Eval]")
    has_permission = is_eval_target or await can_add_target(db, current_user, target_in.user_id)
    if not has_permission:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    if target_in.is_global and not is_ceo_or_admin(current_user):
        raise HTTPException(status_code=403, detail="Only CEO/Admin can create global targets")
        
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
    
    result = await db.execute(select(Target).options(selectinload(Target.scores)).filter(Target.id == target.id))
    target_with_scores = result.scalars().first()
    return prepare_target_response(target_with_scores, current_user)

@router.get("/my", response_model=Any)
async def read_my_targets(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Target).options(selectinload(Target.scores))
        .filter(or_(Target.user_id == current_user.id, Target.is_global == True))
        .offset(skip).limit(limit)
    )
    targets = result.scalars().all()
    return [prepare_target_response(t, current_user) for t in targets]

@router.get("/user/{user_id}", response_model=Any)
async def read_user_targets(
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Target).options(selectinload(Target.scores))
        .filter(or_(Target.user_id == user_id, Target.is_global == True))
        .offset(skip).limit(limit)
    )
    return [prepare_target_response(t, current_user) for t in result.scalars().all()]

@router.patch("/{target_id}", response_model=Any)
async def update_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    target_in: TargetUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).options(selectinload(Target.scores)).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    if target.user_id != current_user.id and not can_manage_all_targets(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")

    if target_in.title is not None:
        target.title = target_in.title
    if target_in.description is not None:
        target.description = target_in.description
    if target_in.month is not None:
        target.month = target_in.month
    if target_in.is_completed is not None:
        target.is_completed = target_in.is_completed
        if target.is_completed:
            target.completed_at = datetime.utcnow()
        else:
            target.completed_at = None

    db.add(target)
    await db.commit()
    await db.refresh(target)
    
    return prepare_target_response(target, current_user)

@router.put("/{target_id}/scores/me", response_model=Any)
async def score_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    score_in: TargetScoreUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).options(selectinload(Target.scores)).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    is_eval_target = (target.title or "").strip().startswith("[Eval]")
    has_permission = (
        target.is_global
        or current_user.id == target.user_id
        or can_manage_all_targets(current_user)
        or is_eval_target
        or await can_score_user(db, current_user, target.user_id)
    )
    if not has_permission:
        raise HTTPException(status_code=403, detail="Not enough permissions")

    score_val = max(0.0, min(100.0, float(score_in.score)))

    existing_score = None
    for s in target.scores:
        if s.reviewer_id == current_user.id:
            existing_score = s
            break
            
    if existing_score:
        existing_score.score = score_val
        db.add(existing_score)
    else:
        new_score = TargetScore(
            target_id=target.id,
            reviewer_id=current_user.id,
            score=score_val
        )
        db.add(new_score)
        
    # Maintain legacy fields for compatibility
    target.score = score_val
    target.scored_by_id = current_user.id
    db.add(target)
    
    await db.commit()
    
    scores_res = await db.execute(select(TargetScore).filter(TargetScore.target_id == target_id, TargetScore.is_deleted == False))
    all_scores = scores_res.scalars().all()
    
    return prepare_target_response(target, current_user, scores=all_scores)
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


@router.delete("/{target_id}", response_model=dict)
async def delete_target(
    *,
    db: AsyncSession = Depends(deps.get_db),
    target_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Target).filter(Target.id == target_id))
    target = result.scalars().first()
    if not target:
        raise HTTPException(status_code=404, detail="Target not found")
        
    if target.created_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions to delete this target")

    await db.delete(target)
    await db.commit()
    return {"success": True}


@router.delete("/permissions/{permission_id}", response_model=dict)
async def delete_permission(
    *,
    db: AsyncSession = Depends(deps.get_db),
    permission_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if not is_ceo_or_admin(current_user):
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    result = await db.execute(select(TargetPermission).filter(TargetPermission.id == permission_id))
    permission = result.scalars().first()
    if not permission:
        raise HTTPException(status_code=404, detail="Permission not found")
        
    await db.delete(permission)
    await db.commit()
    return {"success": True}
