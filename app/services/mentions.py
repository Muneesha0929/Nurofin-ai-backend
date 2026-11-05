import re
from typing import List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from app.models.user import User


async def extract_mentioned_users(db: AsyncSession, text: str) -> List[User]:
    """
    Extract all active users tagged with @Name, @username, or @[Name](id) in the text.
    Prevents false positives on email addresses.
    """
    if not text or "@" not in text:
        return []

    res = await db.execute(select(User).filter(User.is_active == True))
    users = res.scalars().all()
    mentioned: List[User] = []
    text_lower = text.lower()

    # 1. Check for structured ID mentions: @[Name](123) or @user:123
    id_mentions = set(re.findall(r"@\[.*?\]\((\d+)\)", text) + re.findall(r"@user:(\d+)", text))
    for uid in id_mentions:
        u = next((u for u in users if str(u.id) == uid), None)
        if u and u not in mentioned:
            mentioned.append(u)

    # 2. Check for @Full Name or @Username (case-insensitive)
    # Sort descending by name length so "Johnathan Doe" matches before "John"
    sorted_users = sorted(users, key=lambda u: len(u.full_name or ""), reverse=True)
    for u in sorted_users:
        if u in mentioned:
            continue
        if u.full_name and len(u.full_name.strip()) > 1:
            name_pattern = rf"(?<!\w)@{re.escape(u.full_name.strip().lower())}(?![a-zA-Z0-9_])"
            if re.search(name_pattern, text_lower):
                mentioned.append(u)
                continue
        if u.username and len(u.username.strip()) > 1:
            uname_pattern = rf"(?<!\w)@{re.escape(u.username.strip().lower())}(?![a-zA-Z0-9_])"
            if re.search(uname_pattern, text_lower):
                mentioned.append(u)
                continue

    return mentioned
