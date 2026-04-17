"""
WebSocket для real-time обновлений
"""

import logging
import json
from typing import Set, Dict
from datetime import datetime
from enum import Enum

from fastapi import WebSocket, WebSocketDisconnect
from models import User

logger = logging.getLogger(__name__)


class WebSocketEvent(str, Enum):
    TASK_CREATED = "task_created"
    TASK_UPDATED = "task_updated"
    TASK_DELETED = "task_deleted"
    MEETING_CREATED = "meeting_created"
    MEETING_UPDATED = "meeting_updated"
    CLIENT_UPDATED = "client_updated"
    NOTIFICATION = "notification"
    SYNC_STARTED = "sync_started"
    SYNC_COMPLETED = "sync_completed"


class ConnectionManager:
    """WebSocket connection manager"""

    def __init__(self):
        self.active_connections: Dict[int, Set[WebSocket]] = {}  # user_id -> set of websockets
        self.user_map: Dict[WebSocket, int] = {}  # websocket -> user_id

    async def connect(self, websocket: WebSocket, user_id: int):
        """Connect user to WebSocket"""
        await websocket.accept()
        
        if user_id not in self.active_connections:
            self.active_connections[user_id] = set()
        
        self.active_connections[user_id].add(websocket)
        self.user_map[websocket] = user_id
        
        logger.info(f"👤 User {user_id} connected to WebSocket")

    async def disconnect(self, websocket: WebSocket):
        """Disconnect user from WebSocket"""
        user_id = self.user_map.pop(websocket, None)
        
        if user_id and user_id in self.active_connections:
            self.active_connections[user_id].discard(websocket)
            
            if not self.active_connections[user_id]:
                del self.active_connections[user_id]
            
            logger.info(f"👤 User {user_id} disconnected from WebSocket")

    async def broadcast(self, event: WebSocketEvent, data: dict):
        """Отправить событие всем подключенным пользователям"""
        message = {
            "event": event.value,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        for user_connections in self.active_connections.values():
            for websocket in user_connections.copy():
                try:
                    await websocket.send_json(message)
                except Exception as e:
                    logger.error(f"Error sending broadcast message: {e}")
                    user_connections.discard(websocket)

    async def send_to_user(self, user_id: int, event: WebSocketEvent, data: dict):
        """Отправить событие конкретному пользователю"""
        if user_id not in self.active_connections:
            return

        message = {
            "event": event.value,
            "data": data,
            "timestamp": datetime.now().isoformat(),
        }

        for websocket in self.active_connections[user_id].copy():
            try:
                await websocket.send_json(message)
            except Exception as e:
                logger.error(f"Error sending message to user {user_id}: {e}")
                self.active_connections[user_id].discard(websocket)

    async def send_to_group(self, user_ids: list[int], event: WebSocketEvent, data: dict):
        """Отправить событие группе пользователей"""
        for user_id in user_ids:
            await self.send_to_user(user_id, event, data)

    def get_active_user_count(self) -> int:
        """Получить количество активных пользователей"""
        return len(self.active_connections)

    def get_connection_count(self) -> int:
        """Получить всего активных соединений"""
        return sum(len(conns) for conns in self.active_connections.values())


# Global connection manager
manager = ConnectionManager()


async def handle_websocket_connection(websocket: WebSocket, user: User):
    """Handle WebSocket connection lifecycle"""
    try:
        await manager.connect(websocket, user.id)
        
        # Send welcome message
        await websocket.send_json({
            "event": "connected",
            "message": f"Welcome, {user.name}!",
            "timestamp": datetime.now().isoformat(),
        })

        # Handle incoming messages
        while True:
            message = await websocket.receive_text()
            
            try:
                data = json.loads(message)
                action = data.get("action")
                
                # Process different actions if needed
                if action == "ping":
                    await websocket.send_json({
                        "event": "pong",
                        "timestamp": datetime.now().isoformat(),
                    })
                else:
                    logger.debug(f"Unknown action from user {user.id}: {action}")
                    
            except json.JSONDecodeError:
                logger.error(f"Invalid JSON from user {user.id}: {message}")

    except WebSocketDisconnect:
        await manager.disconnect(websocket)
        logger.info(f"WebSocket disconnected for user {user.id}")

    except Exception as e:
        logger.error(f"WebSocket error for user {user.id}: {e}")
        await manager.disconnect(websocket)


# Event emitters
async def emit_task_created(user_id: int, task: dict):
    """Emit task created event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.TASK_CREATED,
        {"task": task}
    )


async def emit_task_updated(user_id: int, task_id: int, updates: dict):
    """Emit task updated event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.TASK_UPDATED,
        {"task_id": task_id, "updates": updates}
    )


async def emit_task_deleted(user_id: int, task_id: int):
    """Emit task deleted event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.TASK_DELETED,
        {"task_id": task_id}
    )


async def emit_meeting_created(user_id: int, meeting: dict):
    """Emit meeting created event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.MEETING_CREATED,
        {"meeting": meeting}
    )


async def emit_client_updated(user_id: int, client_id: int, updates: dict):
    """Emit client updated event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.CLIENT_UPDATED,
        {"client_id": client_id, "updates": updates}
    )


async def emit_notification(user_id: int, title: str, message: str, type: str = "info"):
    """Emit notification event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.NOTIFICATION,
        {"title": title, "message": message, "type": type}
    )


async def emit_sync_started(user_id: int, source: str):
    """Emit sync started event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.SYNC_STARTED,
        {"source": source, "timestamp": datetime.now().isoformat()}
    )


async def emit_sync_completed(user_id: int, source: str, status: str, count: int):
    """Emit sync completed event"""
    await manager.send_to_user(
        user_id,
        WebSocketEvent.SYNC_COMPLETED,
        {
            "source": source,
            "status": status,
            "count": count,
            "timestamp": datetime.now().isoformat()
        }
    )


async def broadcast_notification(title: str, message: str, user_ids: list[int] = None):
    """Broadcast notification to specific users or all"""
    if user_ids:
        await manager.send_to_group(
            user_ids,
            WebSocketEvent.NOTIFICATION,
            {"title": title, "message": message, "type": "broadcast"}
        )
    else:
        await manager.broadcast(
            WebSocketEvent.NOTIFICATION,
            {"title": title, "message": message, "type": "broadcast"}
        )
