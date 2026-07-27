from __future__ import annotations
import json
from typing import Any, Optional
import httpx


BACKEND_URL = None


def _get_backend_url() -> str:
    global BACKEND_URL
    if BACKEND_URL:
        return BACKEND_URL
    import os
    BACKEND_URL = os.getenv("BACKEND_URL", "http://127.0.0.1:8099").rstrip("/")
    return BACKEND_URL


def _get_service_token() -> Optional[str]:
    import os
    return os.getenv("KNOWLEDGE_SERVICE_TOKEN")


async def search_knowledge(
    query: str,
    source_type: Optional[str] = None,
    project_id: Optional[int] = None,
    top_k: int = 10,
) -> list[dict[str, Any]]:
    url = f"{_get_backend_url()}/api/v1/knowledge/search"
    params: dict[str, Any] = {"q": query, "top_k": top_k}
    if source_type:
        params["source_type"] = source_type
    if project_id:
        params["project_id"] = project_id

    token = _get_service_token()
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(url, params=params, headers=headers)
            resp.raise_for_status()
            data = resp.json()
            return data.get("data", [])
    except Exception as e:
        return []
