from typing import Any, List, Optional
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form, Query
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy.orm import selectinload
from sqlalchemy import or_, and_
import secrets
import string

from app.api import deps
from app.core.responses import APIResponse, success_response, error_response
from app.models.document import Document, DocumentUserAccess
from app.schemas.document import (
    Document as DocumentSchema,
    DocumentCreate,
    DocumentWithPasscode,
    DocumentUpdate,
    DocumentUserAccessCreate,
)
from app.models.user import User
from app.models.project import Project
from app.models.task import Task
from app.services.s3 import upload_file_to_s3, get_presigned_url, delete_file_from_s3, UPLOAD_DIR
import os

router = APIRouter()

CEO_ROLES = ("ceo", "admin", "super_admin")


def _role(user: User) -> str:
    return user.role.value if hasattr(user.role, "value") else (user.role or "employee")


def _is_ceo(user: User) -> bool:
    return _role(user) in CEO_ROLES


def generate_passcode(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


async def _serialize_document(db: AsyncSession, doc: Document) -> dict:
    project_name = None
    if doc.project_id:
        proj = await db.execute(select(Project).filter(Project.id == doc.project_id))
        p = proj.scalars().first()
        project_name = p.name if p else None

    task_title = None
    if doc.task_id:
        tsk = await db.execute(select(Task).filter(Task.id == doc.task_id))
        t = tsk.scalars().first()
        task_title = t.title if t else None

    uploader_name = None
    if doc.uploaded_by_id:
        u = await db.execute(select(User).filter(User.id == doc.uploaded_by_id))
        ub = u.scalars().first()
        uploader_name = ub.full_name if ub else None

    allowed_user_ids = []
    access_links = await db.execute(
        select(DocumentUserAccess).filter(DocumentUserAccess.document_id == doc.id)
    )
    for al in access_links.scalars().all():
        allowed_user_ids.append(al.user_id)

    return {
        "id": doc.id,
        "title": doc.title,
        "s3_key": doc.s3_key,
        "url": doc.url,
        "access_type": doc.access_type,
        "passcode": doc.passcode,
        "project_id": doc.project_id,
        "project_name": project_name,
        "task_id": doc.task_id,
        "task_title": task_title,
        "uploaded_by_id": doc.uploaded_by_id,
        "uploader_name": uploader_name,
        "allowed_user_ids": allowed_user_ids,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def _can_manage(doc: Document, user: User) -> bool:
    return _is_ceo(user) or doc.uploaded_by_id == user.id


@router.post("", response_model=APIResponse)
async def create_document(
    *,
    db: AsyncSession = Depends(deps.get_db),
    title: str = Form(...),
    project_id: Optional[int] = Form(None),
    task_id: Optional[int] = Form(None),
    access_type: str = Form("code"),
    allowed_users: Optional[str] = Form(None),
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    if access_type not in ["code", "access"]:
        return error_response(message="Invalid access_type. Use 'code' or 'access'.")

    s3_key = upload_file_to_s3(file.file, file.filename, file.content_type)
    url = f"s3://{s3_key}"

    passcode = None
    if access_type == "code":
        passcode = generate_passcode()

    try:
        doc = Document(
            title=title,
            s3_key=s3_key,
            url=url,
            project_id=project_id,
            task_id=task_id,
            access_type=access_type,
            passcode=passcode,
            uploaded_by_id=current_user.id,
        )
        db.add(doc)
        await db.flush()

        # For access-based, grant access to specified users
        if access_type == "access" and allowed_users:
            try:
                import json
                user_ids = json.loads(allowed_users)
                for uid in user_ids:
                    db.add(DocumentUserAccess(document_id=doc.id, user_id=int(uid)))
            except (json.JSONDecodeError, ValueError):
                pass

        await db.commit()
        await db.refresh(doc)

        data = await _serialize_document(db, doc)
        return success_response(data=data, message="Document uploaded successfully")
    except Exception as e:
        import traceback
        with open("document_upload_error.log", "w") as f:
            f.write(traceback.format_exc())
        return error_response(message=f"Database Error: {str(e)}")


@router.get("", response_model=APIResponse)
async def read_documents(
    db: AsyncSession = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    project_id: Optional[int] = None,
    task_id: Optional[int] = None,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    # Show documents the user uploaded OR has explicit access to OR is CEO
    access_subquery = select(DocumentUserAccess.document_id).filter(
        DocumentUserAccess.user_id == current_user.id
    )

    query = select(Document).filter(Document.is_deleted == False)

    if not _is_ceo(current_user):
        query = query.filter(
            or_(
                Document.uploaded_by_id == current_user.id,
                Document.id.in_(access_subquery),
            )
        )

    if project_id:
        query = query.filter(Document.project_id == project_id)
    if task_id:
        query = query.filter(Document.task_id == task_id)

    result = await db.execute(query.order_by(Document.created_at.desc()).offset(skip).limit(limit))
    docs = result.scalars().all()

    data = [await _serialize_document(db, d) for d in docs]
    return success_response(data=data, message="Documents fetched successfully")


@router.get("/{document_id}", response_model=APIResponse)
async def get_document_detail(
    document_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    has_access = False
    if _is_ceo(current_user) or doc.uploaded_by_id == current_user.id:
        has_access = True
    elif doc.access_type == "access":
        access = await db.execute(
            select(DocumentUserAccess).filter(
                DocumentUserAccess.document_id == document_id,
                DocumentUserAccess.user_id == current_user.id,
            )
        )
        if access.scalars().first():
            has_access = True

    if not has_access:
        return error_response(message="You do not have access to this document")

    data = await _serialize_document(db, doc)
    return success_response(data=data, message="Document fetched successfully")


@router.get("/{document_id}/url", response_model=APIResponse)
async def get_document_url(
    document_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    passcode: Optional[str] = Query(None),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    has_access = False
    if _is_ceo(current_user) or doc.uploaded_by_id == current_user.id:
        has_access = True
    elif doc.access_type == "code":
        if passcode and passcode == doc.passcode:
            has_access = True
        else:
            return error_response(message="Passcode required", data={"requires_passcode": True})
    else:  # access based
        access = await db.execute(
            select(DocumentUserAccess).filter(
                DocumentUserAccess.document_id == document_id,
                DocumentUserAccess.user_id == current_user.id,
            )
        )
        if access.scalars().first():
            has_access = True
        else:
            return error_response(message="You do not have access to this document")

    if not has_access:
        return error_response(message="You do not have access to this document")

    presigned_url = get_presigned_url(doc.s3_key)
    return success_response(data={"url": presigned_url, "title": doc.title}, message="Presigned URL generated")


@router.post("/{document_id}/grant-access", response_model=APIResponse)
async def grant_access(
    document_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    user_ids: List[int],
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    if not _can_manage(doc, current_user):
        return error_response(message="Only the uploader or CEO can grant access")

    if doc.access_type != "access":
        return error_response(message="This document uses passcode protection, not access-based")

    added = []
    for uid in user_ids:
        existing = await db.execute(
            select(DocumentUserAccess).filter(
                DocumentUserAccess.document_id == document_id,
                DocumentUserAccess.user_id == uid,
            )
        )
        if not existing.scalars().first():
            db.add(DocumentUserAccess(document_id=document_id, user_id=uid))
            added.append(uid)

    await db.commit()
    return success_response(data={"granted_to": added}, message="Access granted")


@router.delete("/{document_id}/access/{user_id}", response_model=APIResponse)
async def revoke_access(
    document_id: int,
    user_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    if not _can_manage(doc, current_user):
        return error_response(message="Only the uploader or CEO can revoke access")

    access = await db.execute(
        select(DocumentUserAccess).filter(
            DocumentUserAccess.document_id == document_id,
            DocumentUserAccess.user_id == user_id,
        )
    )
    record = access.scalars().first()
    if record:
        await db.delete(record)
        await db.commit()
    return success_response(message="Access revoked")


@router.put("/{document_id}", response_model=APIResponse)
async def update_document(
    document_id: int,
    *,
    db: AsyncSession = Depends(deps.get_db),
    body: DocumentUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    if not _can_manage(doc, current_user):
        return error_response(message="Only the uploader or CEO can edit this document")

    update_data = body.dict(exclude_unset=True)
    for field, value in update_data.items():
        setattr(doc, field, value)

    await db.commit()
    await db.refresh(doc)
    data = await _serialize_document(db, doc)
    return success_response(data=data, message="Document updated")


@router.delete("/{document_id}", response_model=APIResponse)
async def delete_document(
    document_id: int,
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(select(Document).filter(Document.id == document_id, Document.is_deleted == False))
    doc = result.scalars().first()
    if not doc:
        return error_response(message="Document not found")

    if not _can_manage(doc, current_user):
        return error_response(message="Only the uploader or CEO can delete this document")

    doc.is_deleted = True
    delete_file_from_s3(doc.s3_key)
    await db.commit()
    return success_response(message="Document deleted")

@router.get("/download/{filename}")
async def download_local_file(filename: str):
    file_path = os.path.join(UPLOAD_DIR, filename)
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="File not found")
    return FileResponse(file_path)
