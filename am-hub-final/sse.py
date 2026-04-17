"""Server-Sent Events для real-time обновлений в UI."""
import asyncio
import json
import logging
from typing import AsyncIterator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

router = APIRouter()

# Простая in-memory очередь per-user.
# Production: заменить на Redis pub/sub.
_subscribers: dict[int, asyncio.Queue] = {}


async def publish(user_id: int, event_type: str, data: dict):
    """Опубликовать событие для конкретного пользователя."""
    q = _subscribers.get(user_id)
    if q:
        try:
            await q.put({"type": event_type, "data": data})
        except Exception as e:
            logger.warning(f"SSE publish failed for user {user_id}: {e}")


async def _event_stream(user_id: int) -> AsyncIterator[str]:
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _subscribers[user_id] = q
    try:
        # Приветствие
        yield f"event: connected\ndata: {json.dumps({'user_id': user_id})}\n\n"

        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=15)
                yield f"event: {event['type']}\ndata: {json.dumps(event['data'], default=str)}\n\n"
            except asyncio.TimeoutError:
                # Keep-alive comment
                yield ": ping\n\n"
    except asyncio.CancelledError:
        pass
    finally:
        _subscribers.pop(user_id, None)


@router.get("/api/events")
async def sse_endpoint(request: Request):
    """Подписка SSE. Auth — через cookie auth_token.

    Frontend: new EventSource('/api/events')
    """
    from auth import decode_access_token

    token = request.cookies.get("auth_token")
    if not token:
        return StreamingResponse(iter([]), status_code=401)
    payload = decode_access_token(token)
    if not payload:
        return StreamingResponse(iter([]), status_code=401)
    try:
        user_id = int(payload.get("sub", 0))
    except (TypeError, ValueError):
        return StreamingResponse(iter([]), status_code=401)

    return StreamingResponse(
        _event_stream(user_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # nginx не буферит
        },
    )
