from typing import Any, Optional
from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload

from app.api import deps
from app.models.user import User, RoleEnum
from app.models.deleted_user import DeletedUser
from app.models.department import Department
from app.models.role import Role
from app.schemas.user import (
    UserCreate, 
    UserUpdate, 
    DepartmentCreate, 
    RoleCreate
)
from app.core.security import get_password_hash
from app.core.responses import APIResponse, success_response, error_response

router = APIRouter()

# ----------------- User Management Endpoints -----------------

@router.get("", response_model=APIResponse)
async def read_users(
    db: AsyncSession = Depends(deps.get_db),
    role: Optional[str] = None,
    department: Optional[str] = None,
    skip: int = 0,
    limit: int = 100,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    query = select(User).filter(User.is_deleted == False)
    if role:
        query = query.filter(User.role == role)
    if department:
        query = query.filter(User.department == department)
    
    result = await db.execute(query.offset(skip).limit(limit))
    users = result.scalars().all()
    
    # Serialize with all employee details
    data = [
        {
            "id": u.id, 
            "email": u.email, 
            "username": u.username,
            "full_name": u.full_name, 
            "role": u.role, 
            "department": u.department,
            "phone": u.phone,
            "github": u.github,
            "linkedin": u.linkedin,
            "profile_picture": u.profile_picture,
            "is_active": u.is_active
        } 
        for u in users
    ]
    return success_response(data=data, message="Users fetched successfully")

@router.post("", response_model=APIResponse)
async def create_user(
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_in: UserCreate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    # Check if user exists
    result = await db.execute(select(User).filter(User.email == user_in.email))
    if result.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="The user with this email already exists in the system.",
        )
        
    result_username = await db.execute(select(User).filter(User.username == user_in.username))
    if result_username.scalars().first():
        raise HTTPException(
            status_code=400,
            detail="The user with this username already exists in the system.",
        )
        
    
    user_data = user_in.dict(exclude={"password"})
    
    # Map frontend custom roles to database enum
    valid_roles = ["super_admin", "admin", "ceo", "manager", "team_lead", "employee"]
    if user_data.get("role") and user_data["role"].lower() not in valid_roles:
        user_data["role"] = "employee"
    elif user_data.get("role"):
        user_data["role"] = user_data["role"].lower()
        
    hashed_password = get_password_hash(user_in.password)
    
    db_user = User(**user_data, hashed_password=hashed_password)
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    return success_response(
        data={"id": db_user.id, "email": db_user.email, "role": db_user.role, "department": db_user.department},
        message="User created successfully"
    )

@router.put("/{user_id}", response_model=APIResponse)
async def update_user(
    user_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_in: UserUpdate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if (current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1) and current_user.id != user_id:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO or the user themselves."
        )
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == False))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")
        
    update_data = user_in.dict(exclude_unset=True)
    
    # Non-CEO users cannot modify administrative fields like role, department, or active status
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        update_data.pop("role", None)
        update_data.pop("department", None)
        update_data.pop("is_active", None)
        
    if "password" in update_data and update_data["password"]:
        hashed_password = get_password_hash(update_data["password"])
        db_user.hashed_password = hashed_password
        del update_data["password"]
        
    # Map frontend custom roles to database enum
    valid_roles = ["super_admin", "admin", "ceo", "manager", "team_lead", "employee"]
    if "role" in update_data and update_data["role"]:
        if update_data["role"].lower() not in valid_roles:
            update_data["role"] = "employee"
        else:
            update_data["role"] = update_data["role"].lower()

    for field, val in update_data.items():
        setattr(db_user, field, val)
        
    db.add(db_user)
    await db.commit()
    await db.refresh(db_user)
    
    return success_response(
        data={"id": db_user.id, "email": db_user.email, "role": db_user.role, "is_active": db_user.is_active},
        message="User updated successfully"
    )

@router.delete("/{user_id}", response_model=APIResponse)
async def delete_user(
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    result = await db.execute(select(User).filter(User.id == user_id, User.is_deleted == False))
    db_user = result.scalars().first()
    if not db_user:
        raise HTTPException(status_code=404, detail="User not found")

    # Move the user record into deleted_user table
    archived = DeletedUser(
        original_user_id = db_user.id,
        full_name        = db_user.full_name,
        username         = db_user.username,
        email            = db_user.email,
        hashed_password  = db_user.hashed_password,
        role             = db_user.role,
        department       = db_user.department,
        github           = db_user.github,
        linkedin         = db_user.linkedin,
        phone            = db_user.phone,
        profile_picture  = db_user.profile_picture,
        is_active        = db_user.is_active,
        created_at       = db_user.created_at,
        updated_at       = db_user.updated_at,
        deleted_at       = datetime.utcnow()
    )
    db.add(archived)

    # Soft-delete from the main user table
    db_user.is_deleted = True
    db.add(db_user)
    await db.commit()
    return success_response(message="User archived and removed successfully")


@router.get("/deleted", response_model=APIResponse)
async def get_deleted_users(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    """Return all previously deleted (archived) employees from the deleted_user table."""
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    result = await db.execute(select(DeletedUser).order_by(DeletedUser.deleted_at.desc()))
    deleted_users = result.scalars().all()

    data = [
        {
            "id":               u.id,
            "original_user_id": u.original_user_id,
            "full_name":        u.full_name,
            "username":         u.username,
            "email":            u.email,
            "role":             u.role,
            "department":       u.department,
            "github":           u.github,
            "linkedin":         u.linkedin,
            "phone":            u.phone,
            "profile_picture":  u.profile_picture,
            "is_active":        u.is_active,
            "created_at":       u.created_at.isoformat() if u.created_at else None,
            "deleted_at":       u.deleted_at.isoformat() if u.deleted_at else None,
        }
        for u in deleted_users
    ]
    return success_response(data=data, message="Deleted users fetched successfully")


# ----------------- Departments & Roles Endpoints -----------------

@router.get("/departments", response_model=APIResponse)
async def list_departments_and_roles(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    result = await db.execute(
        select(Department).options(selectinload(Department.roles)).filter(Department.is_deleted == False)
    )
    departments = result.scalars().all()
    
    data = []
    for dept in departments:
        roles_data = []
        for role in dept.roles:
            if not role.is_deleted:
                roles_data.append({
                    "id": role.id,
                    "name": role.name,
                    "is_custom": role.is_custom,
                    "permissions": role.permissions or []
                })
        data.append({
            "id": dept.id,
            "name": dept.name,
            "is_custom": dept.is_custom,
            "roles": roles_data
        })
        
    return success_response(data=data, message="Departments and roles fetched successfully")

@router.post("/departments", response_model=APIResponse)
async def create_department(
    *,
    db: AsyncSession = Depends(deps.get_db),
    dept_in: DepartmentCreate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    # Check if department exists
    result = await db.execute(select(Department).filter(Department.name == dept_in.name, Department.is_deleted == False))
    if result.scalars().first():
        raise HTTPException(status_code=400, detail="Department already exists")
        
    db_dept = Department(name=dept_in.name, is_custom=True)
    db.add(db_dept)
    await db.commit()
    await db.refresh(db_dept)
    
    return success_response(
        data={"id": db_dept.id, "name": db_dept.name, "is_custom": db_dept.is_custom},
        message="Department created successfully"
    )

@router.post("/departments/{dept_id}/roles", response_model=APIResponse)
async def create_role_under_department(
    dept_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    role_in: RoleCreate,
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    # Check if department exists
    result = await db.execute(select(Department).filter(Department.id == dept_id, Department.is_deleted == False))
    dept = result.scalars().first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
        
    # Check if role exists in this department
    role_res = await db.execute(
        select(Role).filter(Role.name == role_in.name, Role.department_id == dept_id, Role.is_deleted == False)
    )
    if role_res.scalars().first():
        raise HTTPException(status_code=400, detail="Role already exists in this department")
        
    db_role = Role(
        name=role_in.name,
        permissions=role_in.permissions,
        is_custom=True,
        department_id=dept_id
    )
    db.add(db_role)
    await db.commit()
    await db.refresh(db_role)
    
    return success_response(
        data={
            "id": db_role.id,
            "name": db_role.name,
            "is_custom": db_role.is_custom,
            "permissions": db_role.permissions,
            "department_id": db_role.department_id
        },
        message="Role created successfully"
    )

@router.put("/departments/{dept_id}/roles/{role_id}", response_model=APIResponse)
async def update_role_permissions(
    dept_id: int,
    role_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    permissions: list[str],
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    result = await db.execute(
        select(Role).filter(Role.id == role_id, Role.department_id == dept_id, Role.is_deleted == False)
    )
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found or doesn't belong to this department")
        
    role.permissions = permissions
    db.add(role)
    await db.commit()
    await db.refresh(role)
    
    return success_response(
        data={
            "id": role.id,
            "name": role.name,
            "permissions": role.permissions,
            "department_id": role.department_id
        },
        message="Role permissions updated successfully"
    )

@router.delete("/departments/{dept_id}", response_model=APIResponse)
async def delete_department(
    dept_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    result = await db.execute(select(Department).filter(Department.id == dept_id, Department.is_deleted == False))
    dept = result.scalars().first()
    if not dept:
        raise HTTPException(status_code=404, detail="Department not found")
        
    dept.is_deleted = True
    db.add(dept)
    
    # Soft delete all roles under this department
    roles_res = await db.execute(select(Role).filter(Role.department_id == dept_id, Role.is_deleted == False))
    for r in roles_res.scalars().all():
        r.is_deleted = True
        db.add(r)
        
    await db.commit()
    return success_response(message="Department and all its roles soft-deleted successfully")

@router.delete("/departments/{dept_id}/roles/{role_id}", response_model=APIResponse)
async def delete_role(
    dept_id: int,
    role_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    if current_user.role != "CEO" and current_user.role != "ceo" and current_user.id != 1:
        raise HTTPException(
            status_code=403,
            detail="Operation restricted to the CEO only."
        )
    result = await db.execute(
        select(Role).filter(Role.id == role_id, Role.department_id == dept_id, Role.is_deleted == False)
    )
    role = result.scalars().first()
    if not role:
        raise HTTPException(status_code=404, detail="Role not found or doesn't belong to this department")
        
    role.is_deleted = True
    db.add(role)
    await db.commit()
    return success_response(message="Role soft-deleted successfully")


from app.models.meeting import Meeting, MeetingParticipant, ParticipantStatusEnum
from app.models.task import Task
from sqlalchemy import or_, and_

@router.get("/{user_id}/availability", response_model=APIResponse)
async def check_availability(
    user_id: int,
    date: str,
    start_time: str,
    end_time: str,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user)
) -> Any:
    # 1. Check Meetings
    meetings_res = await db.execute(
        select(Meeting).join(MeetingParticipant).filter(
            MeetingParticipant.user_id == user_id,
            MeetingParticipant.status != ParticipantStatusEnum.declined,
            Meeting.date == date,
            Meeting.is_deleted == False,
            or_(
                and_(Meeting.start_time <= start_time, Meeting.end_time > start_time),
                and_(Meeting.start_time < end_time, Meeting.end_time >= end_time),
                and_(Meeting.start_time >= start_time, Meeting.end_time <= end_time)
            )
        )
    )
    conflicting_meetings = meetings_res.scalars().all()
    
    # 2. Check Tasks Time Blocks
    tasks_res = await db.execute(
        select(Task).filter(
            Task.assigned_to_id == user_id,
            Task.scheduled_date == date,
            or_(
                and_(Task.scheduled_start_time <= start_time, Task.scheduled_end_time > start_time),
                and_(Task.scheduled_start_time < end_time, Task.scheduled_end_time >= end_time),
                and_(Task.scheduled_start_time >= start_time, Task.scheduled_end_time <= end_time)
            )
        )
    )
    conflicting_tasks = tasks_res.scalars().all()
    
    is_busy = len(conflicting_meetings) > 0 or len(conflicting_tasks) > 0
    
    reasons = []
    if conflicting_meetings:
        reasons.append("busy with meetings")
    if conflicting_tasks:
        reasons.append("busy working on a task")
        
    return success_response(
        data={"is_busy": is_busy, "reasons": reasons},
        message="Availability checked successfully"
    )
