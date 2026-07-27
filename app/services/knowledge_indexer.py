from __future__ import annotations
import json
from typing import Optional
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select

from app.models.knowledge_chunk import KnowledgeChunk
from app.models.meeting import Meeting
from app.models.task import Task
from app.models.project import Project
from app.models.conversation import Conversation, ConversationMessage
import logging

logger = logging.getLogger(__name__)


class KnowledgeIndexer:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def index_meeting(self, meeting_id: int) -> int:
        result = await self.db.execute(
            select(Meeting).filter(Meeting.id == meeting_id, Meeting.is_deleted == False)
        )
        meeting = result.scalars().first()
        if not meeting:
            return 0

        await self._delete_source_chunks("meeting", meeting_id)
        count = 0

        if meeting.title:
            await self._add_chunk(
                source_type="meeting",
                source_id=meeting_id,
                source_title=meeting.title,
                title=f"Meeting: {meeting.title}",
                content=f"{meeting.title}. {meeting.description or ''}",
                chunk_type="meeting_info",
                project_id=None,
                meeting_id=meeting_id,
            )
            count += 1

        if meeting.ai_summary:
            await self._add_chunk(
                source_type="meeting",
                source_id=meeting_id,
                source_title=meeting.title,
                title=f"Summary: {meeting.title}",
                content=meeting.ai_summary,
                chunk_type="summary",
                meeting_id=meeting_id,
            )
            count += 1

        if meeting.mom_summary:
            await self._add_chunk(
                source_type="meeting",
                source_id=meeting_id,
                source_title=meeting.title,
                title=f"MOM: {meeting.title}",
                content=meeting.mom_summary,
                chunk_type="mom",
                meeting_id=meeting_id,
            )
            count += 1

        for field_name, chunk_type in [
            ("mom_decisions", "decision"),
            ("mom_risks", "risk"),
            ("mom_blockers", "blocker"),
            ("mom_followups", "followup"),
            ("mom_deadlines", "deadline"),
        ]:
            raw = getattr(meeting, field_name, None)
            items = self._parse_json_list(raw)
            if items:
                content = "\n".join(
                    f"- {item}" if isinstance(item, str) else f"- {json.dumps(item)}"
                    for item in items
                )
                await self._add_chunk(
                    source_type="meeting",
                    source_id=meeting_id,
                    source_title=meeting.title,
                    title=f"{chunk_type.title()}s from: {meeting.title}",
                    content=content,
                    chunk_type=chunk_type,
                    meeting_id=meeting_id,
                )
                count += 1

        if meeting.mom_executive_summary:
            summary_text = self._extract_str(meeting.mom_executive_summary)
            if summary_text:
                await self._add_chunk(
                    source_type="meeting",
                    source_id=meeting_id,
                    source_title=meeting.title,
                    title=f"Executive Summary: {meeting.title}",
                    content=summary_text,
                    chunk_type="executive_summary",
                    meeting_id=meeting_id,
                )
                count += 1

        if meeting.transcript:
            transcript_content = meeting.transcript[:10000]
            await self._add_chunk(
                source_type="meeting",
                source_id=meeting_id,
                source_title=meeting.title,
                title=f"Transcript: {meeting.title}",
                content=transcript_content,
                chunk_type="transcript",
                meeting_id=meeting_id,
            )
            count += 1

        await self.db.flush()
        return count

    async def index_task(self, task_id: int) -> int:
        result = await self.db.execute(
            select(Task).filter(Task.id == task_id, Task.is_deleted == False)
        )
        task = result.scalars().first()
        if not task:
            return 0

        await self._delete_source_chunks("task", task_id)

        content_parts = [task.title or ""]
        if task.description:
            content_parts.append(task.description)
        if task.status:
            content_parts.append(f"Status: {task.status}")
        if task.priority:
            content_parts.append(f"Priority: {task.priority}")
        if task.deadline:
            content_parts.append(f"Deadline: {task.deadline}")

        await self._add_chunk(
            source_type="task",
            source_id=task_id,
            source_title=task.title or "Untitled Task",
            title=f"Task: {task.title}",
            content=". ".join(content_parts),
            chunk_type="task",
            project_id=task.project_id,
            meeting_id=task.meeting_id,
            task_id=task_id,
            metadata=json.dumps({"status": task.status, "priority": task.priority}),
        )
        await self.db.flush()
        return 1

    async def index_project(self, project_id: int) -> int:
        result = await self.db.execute(
            select(Project).filter(Project.id == project_id, Project.is_deleted == False)
        )
        project = result.scalars().first()
        if not project:
            return 0

        await self._delete_source_chunks("project", project_id)

        content_parts = [project.name or ""]
        if project.description:
            content_parts.append(project.description)
        if project.status:
            content_parts.append(f"Status: {project.status}")
        if project.priority:
            content_parts.append(f"Priority: {project.priority}")

        await self._add_chunk(
            source_type="project",
            source_id=project_id,
            source_title=project.name or "Untitled Project",
            title=f"Project: {project.name}",
            content=". ".join(content_parts),
            chunk_type="project",
            project_id=project_id,
            metadata=json.dumps({"status": project.status, "priority": project.priority}),
        )
        await self.db.flush()
        return 1

    async def index_conversation(self, conversation_id: int) -> int:
        result = await self.db.execute(
            select(Conversation).filter(Conversation.id == conversation_id)
        )
        conv = result.scalars().first()
        if not conv:
            return 0

        await self._delete_source_chunks("conversation", conversation_id)

        msg_result = await self.db.execute(
            select(ConversationMessage)
            .filter(ConversationMessage.conversation_id == conversation_id)
            .order_by(ConversationMessage.created_at)
            .limit(50)
        )
        messages = msg_result.scalars().all()
        if not messages:
            return 0

        conversation_content = "\n".join(
            f"{m.role}: {m.content[:500]}" for m in messages if m.content
        )

        await self._add_chunk(
            source_type="conversation",
            source_id=conversation_id,
            source_title=conv.title or f"Conversation {conversation_id}",
            title=f"Chat: {conv.title}",
            content=conversation_content[:10000],
            chunk_type="conversation",
            conversation_id=conversation_id,
            user_id=conv.user_id,
        )
        await self.db.flush()
        return 1

    async def reindex_all(self) -> int:
        total = 0

        meetings_result = await self.db.execute(
            select(Meeting.id).filter(Meeting.is_deleted == False)
        )
        for (mid,) in meetings_result.all():
            total += await self.index_meeting(mid)

        tasks_result = await self.db.execute(
            select(Task.id).filter(Task.is_deleted == False)
        )
        for (tid,) in tasks_result.all():
            total += await self.index_task(tid)

        projects_result = await self.db.execute(
            select(Project.id).filter(Project.is_deleted == False)
        )
        for (pid,) in projects_result.all():
            total += await self.index_project(pid)

        convs_result = await self.db.execute(select(Conversation.id))
        for (cid,) in convs_result.all():
            total += await self.index_conversation(cid)

        await self.db.commit()
        return total

    async def _add_chunk(
        self,
        source_type: str,
        source_id: int,
        source_title: str,
        title: str,
        content: str,
        chunk_type: str,
        project_id: Optional[int] = None,
        meeting_id: Optional[int] = None,
        task_id: Optional[int] = None,
        conversation_id: Optional[int] = None,
        user_id: Optional[int] = None,
        metadata: Optional[str] = None,
    ) -> KnowledgeChunk:
        chunk = KnowledgeChunk(
            source_type=source_type,
            source_id=source_id,
            source_title=source_title,
            title=title,
            content=content,
            chunk_type=chunk_type,
            project_id=project_id,
            meeting_id=meeting_id,
            task_id=task_id,
            conversation_id=conversation_id,
            user_id=user_id,
            chunk_metadata=metadata,
        )
        self.db.add(chunk)
        return chunk

    async def _delete_source_chunks(self, source_type: str, source_id: int) -> None:
        from sqlalchemy import update as sa_update

        stmt = (
            sa_update(KnowledgeChunk)
            .where(
                KnowledgeChunk.source_type == source_type,
                KnowledgeChunk.source_id == source_id,
            )
            .values(is_deleted=True)
        )
        await self.db.execute(stmt)

    @staticmethod
    def _parse_json_list(raw) -> list:
        if raw is None:
            return []
        if isinstance(raw, list):
            return raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return parsed if isinstance(parsed, list) else []
            except (json.JSONDecodeError, ValueError):
                return []
        return []

    @staticmethod
    def _extract_str(raw) -> Optional[str]:
        if raw is None:
            return None
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                return str(parsed) if parsed else None
            except (json.JSONDecodeError, ValueError):
                return raw
        return str(raw)
