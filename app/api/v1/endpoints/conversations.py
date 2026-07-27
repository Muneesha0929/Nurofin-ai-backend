from typing import Any
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func

from app.api import deps
from app.models.user import User
from app.models.conversation import Conversation, ConversationMessage
from app.schemas.conversation import (
    ConversationCreate,
    ConversationUpdate,
    ConversationRead,
    ConversationListRead,
    ConversationMessageCreate,
    ConversationMessageRead,
)
from app.core.responses import APIResponse, success_response, error_response
from app.services.knowledge_indexer import KnowledgeIndexer

router = APIRouter()


@router.get("", response_model=APIResponse)
async def list_conversations(
    db: AsyncSession = Depends(deps.get_db),
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(Conversation.user_id == current_user.id, Conversation.is_deleted == False)
        .order_by(Conversation.updated_at.desc())
    )
    convs = result.scalars().all()

    data = []
    for c in convs:
        msg_count_result = await db.execute(
            select(func.count(ConversationMessage.id))
            .filter(ConversationMessage.conversation_id == c.id)
        )
        msg_count = msg_count_result.scalar() or 0
        data.append({
            "id": c.id,
            "title": c.title,
            "user_id": c.user_id,
            "created_at": str(c.created_at) if c.created_at else None,
            "updated_at": str(c.updated_at) if c.updated_at else None,
            "message_count": msg_count,
        })

    return success_response(data=data, message="Conversations fetched successfully")


@router.post("", response_model=APIResponse)
async def create_conversation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_in: ConversationCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    db_conv = Conversation(
        title=conv_in.title or "New Conversation",
        user_id=current_user.id,
    )
    db.add(db_conv)
    await db.commit()
    await db.refresh(db_conv)

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_conversation(db_conv.id)
        await db.commit()
    except Exception:
        pass

    return success_response(
        data={
            "id": db_conv.id,
            "title": db_conv.title,
            "user_id": db_conv.user_id,
            "created_at": str(db_conv.created_at) if db_conv.created_at else None,
            "updated_at": str(db_conv.updated_at) if db_conv.updated_at else None,
            "message_count": 0,
        },
        message="Conversation created successfully",
    )


@router.get("/{conv_id}", response_model=APIResponse)
async def get_conversation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conv = result.scalars().first()
    if not conv:
        return error_response(message="Conversation not found")

    msg_result = await db.execute(
        select(ConversationMessage)
        .filter(ConversationMessage.conversation_id == conv_id)
        .order_by(ConversationMessage.created_at)
    )
    messages = msg_result.scalars().all()

    return success_response(
        data={
            "id": conv.id,
            "title": conv.title,
            "user_id": conv.user_id,
            "created_at": str(conv.created_at) if conv.created_at else None,
            "updated_at": str(conv.updated_at) if conv.updated_at else None,
            "messages": [
                {
                    "id": m.id,
                    "conversation_id": m.conversation_id,
                    "role": m.role,
                    "content": m.content,
                    "files_json": m.files_json,
                    "created_at": str(m.created_at) if m.created_at else None,
                }
                for m in messages
            ],
        },
        message="Conversation fetched successfully",
    )


@router.put("/{conv_id}", response_model=APIResponse)
async def update_conversation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_id: int,
    conv_in: ConversationUpdate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conv = result.scalars().first()
    if not conv:
        return error_response(message="Conversation not found")

    if conv_in.title is not None:
        conv.title = conv_in.title

    await db.commit()
    await db.refresh(conv)
    return success_response(
        data={"id": conv.id, "title": conv.title},
        message="Conversation updated successfully",
    )


@router.delete("/{conv_id}", response_model=APIResponse)
async def delete_conversation(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_id: int,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conv = result.scalars().first()
    if not conv:
        return error_response(message="Conversation not found")

    conv.is_deleted = True
    await db.commit()
    return success_response(message="Conversation deleted successfully")


@router.post("/{conv_id}/messages", response_model=APIResponse)
async def add_message(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_id: int,
    msg_in: ConversationMessageCreate,
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conv = result.scalars().first()
    if not conv:
        return error_response(message="Conversation not found")

    db_msg = ConversationMessage(
        conversation_id=conv_id,
        role=msg_in.role,
        content=msg_in.content,
        files_json=msg_in.files_json,
    )
    db.add(db_msg)

    conv.updated_at = conv.updated_at
    from datetime import datetime
    conv.updated_at = datetime.utcnow()

    await db.commit()
    await db.refresh(db_msg)

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_conversation(conv.id)
        await db.commit()
    except Exception:
        pass

    return success_response(
        data={
            "id": db_msg.id,
            "conversation_id": db_msg.conversation_id,
            "role": db_msg.role,
            "content": db_msg.content,
            "files_json": db_msg.files_json,
            "created_at": str(db_msg.created_at) if db_msg.created_at else None,
        },
        message="Message added successfully",
    )


@router.post("/{conv_id}/messages/bulk", response_model=APIResponse)
async def add_messages_bulk(
    *,
    db: AsyncSession = Depends(deps.get_db),
    conv_id: int,
    messages: list[ConversationMessageCreate],
    current_user: User = Depends(deps.get_current_user),
) -> Any:
    result = await db.execute(
        select(Conversation)
        .filter(
            Conversation.id == conv_id,
            Conversation.user_id == current_user.id,
            Conversation.is_deleted == False,
        )
    )
    conv = result.scalars().first()
    if not conv:
        return error_response(message="Conversation not found")

    created = []
    for msg_in in messages:
        db_msg = ConversationMessage(
            conversation_id=conv_id,
            role=msg_in.role,
            content=msg_in.content,
            files_json=msg_in.files_json,
        )
        db.add(db_msg)
        created.append(db_msg)

    from datetime import datetime
    conv.updated_at = datetime.utcnow()

    await db.commit()

    try:
        indexer = KnowledgeIndexer(db)
        await indexer.index_conversation(conv.id)
        await db.commit()
    except Exception:
        pass

    return success_response(
        data=[
            {
                "id": m.id,
                "conversation_id": m.conversation_id,
                "role": m.role,
                "content": m.content,
                "files_json": m.files_json,
                "created_at": str(m.created_at) if m.created_at else None,
            }
            for m in created
        ],
        message=f"{len(created)} messages added successfully",
    )
