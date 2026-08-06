from typing import Any, List
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from sqlalchemy.orm import Session
import uuid
import secrets
import string

from app.api import deps
from app.models.document import Document, DocumentUserAccess
from app.schemas.document import Document as DocumentSchema, DocumentCreate, DocumentWithPasscode, DocumentUpdate
from app.models.user import User
from app.services.s3 import upload_file_to_s3, get_presigned_url, delete_file_from_s3

router = APIRouter()

def generate_passcode(length=8):
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for i in range(length))

@router.post("/", response_model=DocumentWithPasscode)
def create_document(
    *,
    db: Session = Depends(deps.get_db),
    title: str = Form(...),
    project_id: int = Form(None),
    task_id: int = Form(None),
    access_type: str = Form("code"),
    file: UploadFile = File(...),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Create a new document.
    """
    if access_type not in ["code", "access"]:
        raise HTTPException(status_code=400, detail="Invalid access_type")
        
    s3_key = upload_file_to_s3(file.file, file.filename, file.content_type)
    url = f"s3://{s3_key}" # Placeholder, we use presigned URLs to access
    
    passcode = None
    if access_type == "code":
        passcode = generate_passcode()
        
    doc = Document(
        title=title,
        s3_key=s3_key,
        url=url,
        project_id=project_id,
        task_id=task_id,
        access_type=access_type,
        passcode=passcode,
        uploaded_by_id=current_user.id
    )
    db.add(doc)
    db.commit()
    db.refresh(doc)
    
    return doc

@router.get("/{document_id}/url")
def get_document_url(
    *,
    db: Session = Depends(deps.get_db),
    document_id: int,
    passcode: str = None,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Get presigned URL for a document if access is granted.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc.uploaded_by_id == current_user.id:
        has_access = True
    elif doc.access_type == "code":
        if not passcode or passcode != doc.passcode:
            raise HTTPException(status_code=403, detail="Invalid passcode")
        has_access = True
    else: # access based
        access = db.query(DocumentUserAccess).filter(
            DocumentUserAccess.document_id == document_id,
            DocumentUserAccess.user_id == current_user.id
        ).first()
        if not access:
            raise HTTPException(status_code=403, detail="You do not have access to this document")
        has_access = True
        
    presigned_url = get_presigned_url(doc.s3_key)
    return {"url": presigned_url}

@router.delete("/{document_id}")
def delete_document(
    *,
    db: Session = Depends(deps.get_db),
    document_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Delete a document.
    """
    doc = db.query(Document).filter(Document.id == document_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
        
    if doc.uploaded_by_id != current_user.id:
        raise HTTPException(status_code=403, detail="Not enough permissions")
        
    delete_file_from_s3(doc.s3_key)
    db.delete(doc)
    db.commit()
    return {"status": "success"}

@router.get("/", response_model=List[DocumentSchema])
def read_documents(
    db: Session = Depends(deps.get_db),
    skip: int = 0,
    limit: int = 100,
    project_id: int = None,
    task_id: int = None,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    """
    Retrieve documents.
    """
    query = db.query(Document)
    
    if project_id:
        query = query.filter(Document.project_id == project_id)
    if task_id:
        query = query.filter(Document.task_id == task_id)
        
    documents = query.offset(skip).limit(limit).all()
    return documents
