import asyncio
import json
import logging
from datetime import datetime
from typing import Any, Dict, Set

logger = logging.getLogger(__name__)

_subscribers: Set[asyncio.Queue] = set()


async def publish_live_event(event_type: str, payload: Dict[str, Any] | None = None) -> None:
    """Broadcast a lightweight invalidation event to all connected SSE clients."""
    if not _subscribers:
        return
    message = json.dumps({
        "type": event_type,
        "payload": payload or {},
        "ts": datetime.utcnow().isoformat() + "Z",
    })
    stale = []
    for q in list(_subscribers):
        try:
            if q.full():
                try:
                    q.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            q.put_nowait(message)
        except Exception as exc:
            logger.debug("Dropping stale SSE queue: %s", exc)
            stale.append(q)
    for q in stale:
        _subscribers.discard(q)


def subscribe_live_events() -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers.add(q)
    return q


def unsubscribe_live_events(q: asyncio.Queue) -> None:
    _subscribers.discard(q)
