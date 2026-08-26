"""
Event Bus v5.1.2
Priority Queue + Retry + DLQ 지원
"""

import asyncio
import random
import uuid
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from core.constants import Priority
from core.logger import setup_logger

logger = setup_logger("event_bus")


@dataclass
class EventMessage:
    """이벤트 메시지 (재시도 시 ID 유지)"""

    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    event_type: str = ""
    data: Any = None
    priority: Priority = Priority.NORMAL
    version: str = "1.0"
    retry_count: int = 0
    max_retries: int = 3
    timeout: float = 30.0
    created_at: datetime = field(default_factory=datetime.now)
    timestamp: datetime = field(default_factory=datetime.now)


class EventBus:
    """이벤트 버스 (Priority + Retry + DLQ)"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self):
        self._subscribers: dict[str, list[dict]] = defaultdict(list)
        self._priority_queues = {
            Priority.CRITICAL: asyncio.Queue(),
            Priority.HIGH: asyncio.Queue(),
            Priority.NORMAL: asyncio.Queue(),
            Priority.LOW: asyncio.Queue(),
        }
        self._dead_letter_queue = asyncio.Queue()
        self._is_running = False
        self._tasks: list[asyncio.Task] = []

    def subscribe(self, event_type: str, callback: Callable, version: str = "1.0"):
        """이벤트 구독"""
        self._subscribers[event_type].append({"callback": callback, "version": version})
        logger.debug(f"Subscribed to {event_type}")

    async def publish(
        self,
        event_type: str,
        data: Any,
        priority: Priority = Priority.NORMAL,
        version: str = "1.0",
        message_id: str = None,
    ):
        """이벤트 발행 (메시지 ID 유지)"""
        message = EventMessage(
            id=message_id or str(uuid.uuid4()), event_type=event_type, data=data, priority=priority, version=version
        )
        await self._priority_queues[priority].put(message)
        logger.debug(f"Event published: {event_type} (priority: {priority.value})")

    async def start(self):
        """이벤트 처리 시작"""
        self._is_running = True
        for priority in Priority:
            task = asyncio.create_task(self._process_queue(priority))
            self._tasks.append(task)
        logger.info("EventBus started")

    async def stop(self):
        """이벤트 처리 중지"""
        self._is_running = False
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("EventBus stopped")

    async def _process_queue(self, priority: Priority):
        """우선순위별 이벤트 처리"""
        queue = self._priority_queues[priority]

        while self._is_running:
            try:
                message = await queue.get()
                await self._handle_message(message)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Event processing error: {e}")

    async def _handle_message(self, message: EventMessage):
        """개별 메시지 처리 (Retry + Timeout)"""
        if message.event_type not in self._subscribers:
            return

        for subscriber in self._subscribers[message.event_type]:
            if subscriber["version"] != message.version:
                continue

            try:
                # Timeout 적용
                await asyncio.wait_for(subscriber["callback"](message.data), timeout=message.timeout)
            except TimeoutError:
                await self._retry(message, "Timeout")
            except Exception as e:
                await self._retry(message, str(e))

    async def _retry(self, message: EventMessage, reason: str):
        """재시도 (Exponential Backoff + Jitter)"""
        if message.retry_count < message.max_retries:
            # Exponential Backoff + Jitter
            base_delay = 2**message.retry_count
            jitter = random.uniform(0, base_delay * 0.3)
            delay = base_delay + jitter

            await asyncio.sleep(delay)

            message.retry_count += 1
            await self.publish(
                message.event_type,
                message.data,
                priority=message.priority,
                version=message.version,
                message_id=message.id,
            )
            logger.warning(f"Retry {message.retry_count}/{message.max_retries} for {message.id} ({reason})")
        else:
            # Dead Letter Queue
            await self._dead_letter_queue.put(
                {"message": message, "reason": reason, "timestamp": datetime.now().isoformat()}
            )
            logger.error(f"Message {message.id} moved to DLQ ({reason})")
