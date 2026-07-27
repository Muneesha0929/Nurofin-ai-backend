from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import text, func, case
from app.models.knowledge_chunk import KnowledgeChunk
import logging

logger = logging.getLogger(__name__)


@dataclass
class RetrievalResult:
    chunk_id: int
    score: float
    content: str
    title: str
    source_type: str
    source_id: int
    source_title: str
    chunk_type: str
    metadata: dict = field(default_factory=dict)


class BaseRetrievalBackend(ABC):
    @abstractmethod
    async def search(
        self,
        db: AsyncSession,
        query: str,
        source_type: Optional[str] = None,
        project_id: Optional[int] = None,
        top_k: int = 20,
    ) -> list[RetrievalResult]:
        ...

    @abstractmethod
    async def index_chunk(self, db: AsyncSession, chunk: KnowledgeChunk) -> None:
        ...

    @abstractmethod
    async def delete_chunks(
        self, db: AsyncSession, source_type: str, source_id: int
    ) -> None:
        ...

    @abstractmethod
    async def rebuild_index(self, db: AsyncSession) -> int:
        ...


class PostgresFTSBackend(BaseRetrievalBackend):
    """PostgreSQL full-text search backend. Falls back to SQLite-compatible LIKE search."""

    async def search(
        self,
        db: AsyncSession,
        query: str,
        source_type: Optional[str] = None,
        project_id: Optional[int] = None,
        top_k: int = 20,
    ) -> list[RetrievalResult]:
        is_sqlite = str(db.bind.url).startswith("sqlite")

        if is_sqlite:
            return await self._search_sqlite(db, query, source_type, project_id, top_k)
        else:
            return await self._search_pg(db, query, source_type, project_id, top_k)

    async def _search_sqlite(
        self,
        db: AsyncSession,
        query: str,
        source_type: Optional[str],
        project_id: Optional[int],
        top_k: int,
    ) -> list[RetrievalResult]:
        like_pattern = f"%{query}%"
        stmt = (
            select(
                KnowledgeChunk,
                case(
                    (KnowledgeChunk.title.ilike(like_pattern), 2.0),
                    else_=1.0,
                ).label("score"),
            )
            .filter(KnowledgeChunk.is_deleted == False)
            .filter(
                (KnowledgeChunk.content.ilike(like_pattern))
                | (KnowledgeChunk.title.ilike(like_pattern))
            )
        )
        if source_type:
            stmt = stmt.filter(KnowledgeChunk.source_type == source_type)
        if project_id:
            stmt = stmt.filter(KnowledgeChunk.project_id == project_id)

        stmt = stmt.order_by(text("score DESC")).limit(top_k)
        result = await db.execute(stmt)
        rows = result.all()

        return [
            RetrievalResult(
                chunk_id=row[0].id,
                score=float(row[1]),
                content=row[0].content,
                title=row[0].title,
                source_type=row[0].source_type,
                source_id=row[0].source_id,
                source_title=row[0].source_title,
                chunk_type=row[0].chunk_type,
                metadata=self._parse_metadata(row[0].chunk_metadata),
            )
            for row in rows
        ]

    async def _search_pg(
        self,
        db: AsyncSession,
        query: str,
        source_type: Optional[str],
        project_id: Optional[int],
        top_k: int,
    ) -> list[RetrievalResult]:
        ts_query = func.plainto_tsquery("english", query)
        search_vector = func.to_tsvector(
            "english",
            func.coalesce(KnowledgeChunk.title, "")
            + " "
            + func.coalesce(KnowledgeChunk.content, ""),
        )
        rank = func.ts_rank(search_vector, ts_query)

        stmt = (
            select(KnowledgeChunk, rank.label("score"))
            .filter(KnowledgeChunk.is_deleted == False)
            .filter(search_vector.op("@@")(ts_query))
        )
        if source_type:
            stmt = stmt.filter(KnowledgeChunk.source_type == source_type)
        if project_id:
            stmt = stmt.filter(KnowledgeChunk.project_id == project_id)

        stmt = stmt.order_by(text("score DESC")).limit(top_k)
        result = await db.execute(stmt)
        rows = result.all()

        return [
            RetrievalResult(
                chunk_id=row[0].id,
                score=float(row[1]),
                content=row[0].content,
                title=row[0].title,
                source_type=row[0].source_type,
                source_id=row[0].source_id,
                source_title=row[0].source_title,
                chunk_type=row[0].chunk_type,
                metadata=self._parse_metadata(row[0].chunk_metadata),
            )
            for row in rows
        ]

    async def index_chunk(self, db: AsyncSession, chunk: KnowledgeChunk) -> None:
        pass

    async def delete_chunks(
        self, db: AsyncSession, source_type: str, source_id: int
    ) -> None:
        from sqlalchemy import update as sa_update

        stmt = (
            sa_update(KnowledgeChunk)
            .where(
                KnowledgeChunk.source_type == source_type,
                KnowledgeChunk.source_id == source_id,
            )
            .values(is_deleted=True)
        )
        await db.execute(stmt)

    async def rebuild_index(self, db: AsyncSession) -> int:
        from app.services.knowledge_indexer import KnowledgeIndexer

        indexer = KnowledgeIndexer(db)
        return await indexer.reindex_all()

    @staticmethod
    def _parse_metadata(raw: Optional[str]) -> dict:
        if not raw:
            return {}
        try:
            import json
            return json.loads(raw)
        except Exception:
            return {}


def get_retrieval_backend() -> BaseRetrievalBackend:
    return PostgresFTSBackend()
