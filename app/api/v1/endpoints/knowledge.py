from typing import Any, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.api import deps
from app.models.user import User
from app.models.knowledge_chunk import KnowledgeChunk
from app.core.responses import APIResponse, success_response, error_response
from app.services.retrieval_backend import get_retrieval_backend
from app.services.knowledge_indexer import KnowledgeIndexer

router = APIRouter()


@router.get("/search", response_model=APIResponse)
async def search_knowledge(
    q: str = Query(..., description="Search query"),
    source_type: Optional[str] = Query(None, description="Filter by source type"),
    project_id: Optional[int] = Query(None, description="Filter by project ID"),
    top_k: int = Query(20, le=100, description="Number of results"),
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    if not q or not q.strip():
        return error_response(message="Search query cannot be empty")

    backend = get_retrieval_backend()
    results = await backend.search(
        db=db,
        query=q.strip(),
        source_type=source_type,
        project_id=project_id,
        top_k=top_k,
    )

    data = [
        {
            "chunk_id": r.chunk_id,
            "score": r.score,
            "title": r.title,
            "content": r.content,
            "source_type": r.source_type,
            "source_id": r.source_id,
            "source_title": r.source_title,
            "chunk_type": r.chunk_type,
            "metadata": r.metadata,
        }
        for r in results
    ]

    return success_response(data=data, message=f"Found {len(data)} results")


@router.get("/chunks", response_model=APIResponse)
async def list_chunks(
    source_type: Optional[str] = Query(None),
    project_id: Optional[int] = Query(None),
    chunk_type: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, le=200),
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    stmt = select(KnowledgeChunk).filter(KnowledgeChunk.is_deleted == False)

    if source_type:
        stmt = stmt.filter(KnowledgeChunk.source_type == source_type)
    if project_id:
        stmt = stmt.filter(KnowledgeChunk.project_id == project_id)
    if chunk_type:
        stmt = stmt.filter(KnowledgeChunk.chunk_type == chunk_type)

    count_stmt = select(func.count()).select_from(stmt.subquery())
    count_result = await db.execute(count_stmt)
    total = count_result.scalar() or 0

    stmt = stmt.order_by(KnowledgeChunk.created_at.desc())
    stmt = stmt.offset(skip).limit(limit)

    result = await db.execute(stmt)
    chunks = result.scalars().all()

    data = [
        {
            "id": c.id,
            "source_type": c.source_type,
            "source_id": c.source_id,
            "source_title": c.source_title,
            "title": c.title,
            "content": c.content[:500],
            "chunk_type": c.chunk_type,
            "project_id": c.project_id,
            "meeting_id": c.meeting_id,
            "task_id": c.task_id,
            "conversation_id": c.conversation_id,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in chunks
    ]

    return success_response(data={"items": data, "total": total}, message="Chunks fetched")


@router.get("/sources", response_model=APIResponse)
async def get_source_types(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    stmt = (
        select(
            KnowledgeChunk.source_type,
            func.count(KnowledgeChunk.id).label("count"),
        )
        .filter(KnowledgeChunk.is_deleted == False)
        .group_by(KnowledgeChunk.source_type)
    )
    result = await db.execute(stmt)
    rows = result.all()

    data = [{"source_type": row[0], "count": row[1]} for row in rows]
    return success_response(data=data, message="Source types fetched")


@router.get("/chunk-types", response_model=APIResponse)
async def get_chunk_types(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    stmt = (
        select(
            KnowledgeChunk.chunk_type,
            func.count(KnowledgeChunk.id).label("count"),
        )
        .filter(KnowledgeChunk.is_deleted == False)
        .group_by(KnowledgeChunk.chunk_type)
    )
    result = await db.execute(stmt)
    rows = result.all()

    data = [{"chunk_type": row[0], "count": row[1]} for row in rows]
    return success_response(data=data, message="Chunk types fetched")


@router.get("/stats", response_model=APIResponse)
async def get_knowledge_stats(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    total_stmt = select(func.count(KnowledgeChunk.id)).filter(KnowledgeChunk.is_deleted == False)
    total_result = await db.execute(total_stmt)
    total_chunks = total_result.scalar() or 0

    sources_stmt = (
        select(
            KnowledgeChunk.source_type,
            func.count(KnowledgeChunk.id),
        )
        .filter(KnowledgeChunk.is_deleted == False)
        .group_by(KnowledgeChunk.source_type)
    )
    sources_result = await db.execute(sources_stmt)
    by_source = {row[0]: row[1] for row in sources_result.all()}

    types_stmt = (
        select(
            KnowledgeChunk.chunk_type,
            func.count(KnowledgeChunk.id),
        )
        .filter(KnowledgeChunk.is_deleted == False)
        .group_by(KnowledgeChunk.chunk_type)
    )
    types_result = await db.execute(types_stmt)
    by_type = {row[0]: row[1] for row in types_result.all()}

    return success_response(
        data={
            "total_chunks": total_chunks,
            "by_source": by_source,
            "by_type": by_type,
        },
        message="Knowledge stats fetched",
    )


@router.post("/index", response_model=APIResponse)
async def index_entity(
    source_type: str,
    source_id: int,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    indexer = KnowledgeIndexer(db)
    valid_sources = ["meeting", "task", "project", "conversation"]
    if source_type not in valid_sources:
        return error_response(message=f"Invalid source_type. Must be one of: {valid_sources}")

    count = 0
    if source_type == "meeting":
        count = await indexer.index_meeting(source_id)
    elif source_type == "task":
        count = await indexer.index_task(source_id)
    elif source_type == "project":
        count = await indexer.index_project(source_id)
    elif source_type == "conversation":
        count = await indexer.index_conversation(source_id)

    await db.commit()
    return success_response(data={"chunks_created": count}, message=f"Indexed {count} chunks for {source_type}/{source_id}")


@router.post("/reindex", response_model=APIResponse)
async def reindex_all(
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    if current_user.role not in ["super_admin", "ceo"]:
        return error_response(message="Only admins can trigger a full reindex")

    indexer = KnowledgeIndexer(db)
    total = await indexer.reindex_all()
    return success_response(data={"total_chunks": total}, message=f"Full reindex complete: {total} chunks created")


@router.get("/{chunk_id}", response_model=APIResponse)
async def get_chunk(
    chunk_id: int,
    current_user: User = Depends(deps.get_current_user),
    db: AsyncSession = Depends(deps.get_db),
) -> Any:
    result = await db.execute(
        select(KnowledgeChunk).filter(
            KnowledgeChunk.id == chunk_id,
            KnowledgeChunk.is_deleted == False,
        )
    )
    chunk = result.scalars().first()
    if not chunk:
        return error_response(message="Knowledge chunk not found")

    data = {
        "id": chunk.id,
        "source_type": chunk.source_type,
        "source_id": chunk.source_id,
        "source_title": chunk.source_title,
        "title": chunk.title,
        "content": chunk.content,
        "chunk_type": chunk.chunk_type,
        "project_id": chunk.project_id,
        "meeting_id": chunk.meeting_id,
        "task_id": chunk.task_id,
        "conversation_id": chunk.conversation_id,
        "chunk_metadata": chunk.chunk_metadata,
        "created_at": chunk.created_at.isoformat() if chunk.created_at else None,
    }
    return success_response(data=data, message="Chunk fetched")
