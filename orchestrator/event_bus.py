"""
orchestrator/event_bus.py - v2.0 (Session 14)

Event-Driven Architecture 핵심 컴포넌트
- EventStore: 이벤트 소싱 (In-Memory, max_size 상한 + trace_id 조회 지원)
- DLQ (Dead Letter Queue): 처리 실패 이벤트 격리
- 재시도는 백그라운드 태스크로 분리 (큐 컨슈머 블로킹 방지)
- Priority는 core.constants 재사용 (기존 모듈과의 하위 호환성 유지)

설계상 알려진 제약: 동일 event_type에 다중 구독자가 있을 때 재발행(retry)은
이벤트 전체를 재전달하므로 "최소 1회(at-least-once)" 전달을 보장하며,
이미 성공한 구독자가 재시도 시 중복 호출될 수 있습니다. 구독자는 멱등성을
스스로 보장해야 합니다.
"""

import asyncio
import random
import uuid
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from core.constants import Priority
from core.logger import setup_logger
from observability.trace_id import current_trace_id

logger = setup_logger("event_bus")


@dataclass
class EventMessage:
    event_type: str
    data: Any = None
    priority: Priority = Priority.NORMAL
    id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: float = field(default_factory=lambda: datetime.now().timestamp())
    trace_id: str = field(default_factory=current_trace_id)
    retry_count: int = 0
    max_retries: int = 3
    timeout: float = 5.0
    version: str = "1.0"

    def to_dict(self) -> dict:
        return {
            "id": self.id, "event_type": self.event_type, "priority": self.priority.name,
            "timestamp": self.timestamp, "trace_id": self.trace_id,
            "retry_count": self.retry_count, "version": self.version,
        }


class EventStore:
    """이벤트 소싱 저장소 (In-Memory, 향후 DB/Kafka 연동 대비 인터페이스 고정)."""

    def __init__(self, max_size: int = 100_000) -> None:
        self._events: List[EventMessage] = []
        self._lock = asyncio.Lock()
        self._max_size = max_size

    async def save(self, event: EventMessage) -> None:
        async with self._lock:
            self._events.append(event)
            if len(self._events) > self._max_size:
                overflow = len(self._events) - self._max_size
                del self._events[:overflow]

    async def get_all(self) -> List[EventMessage]:
        async with self._lock:
            return list(self._events)

    async def get_by_type(self, event_type: str) -> List[EventMessage]:
        async with self._lock:
            return [e for e in self._events if e.event_type == event_type]

    async def get_by_trace_id(self, trace_id: str) -> List[EventMessage]:
        async with self._lock:
            return [e for e in self._events if e.trace_id == trace_id]

    async def clear(self) -> None:
        async with self._lock:
            self._events.clear()

    async def count(self) -> int:
        async with self._lock:
            return len(self._events)


class EventBus:
    _instance: Optional["EventBus"] = None

    def __new__(cls) -> "EventBus":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._init()
        return cls._instance

    def _init(self) -> None:
        self._subscribers: Dict[str, List[Callable]] = defaultdict(list)
        self._queues: Dict[Priority, asyncio.Queue] = {p: asyncio.Queue() for p in Priority}
        self.dlq: asyncio.Queue = asyncio.Queue()
        self.store = EventStore()
        self._is_running = False
        self._consumer_tasks: List[asyncio.Task] = []
        self._retry_tasks: List[asyncio.Task] = []
        self.retry_backoff_base: float = 2.0
        self.retry_jitter_max: float = 1.0

    @classmethod
    def reset_for_testing(cls) -> None:
        cls._instance = None

    def subscribe(self, event_type: str, callback: Callable) -> None:
        self._subscribers[event_type].append(callback)
        logger.debug(f"Subscribed to {event_type}")

    def unsubscribe(self, event_type: str, callback: Callable) -> bool:
        if event_type in self._subscribers and callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            return True
        return False

    async def publish(self, event: EventMessage) -> None:
        await self.store.save(event)
        await self._queues[event.priority].put(event)
        logger.debug(f"Published: {event.event_type} [{event.id}] priority={event.priority.name}")

    async def start(self) -> None:
        if self._is_running:
            return
        self._is_running = True
        for priority in Priority:
            self._consumer_tasks.append(asyncio.create_task(self._process_queue(priority)))
        logger.info("EventBus v2.0 started")

    async def stop(self) -> None:
        self._is_running = False
        all_tasks = self._consumer_tasks + self._retry_tasks
        for task in all_tasks:
            task.cancel()
        await asyncio.gather(*all_tasks, return_exceptions=True)
        self._consumer_tasks.clear()
        self._retry_tasks.clear()
        logger.info("EventBus v2.0 stopped")

    async def _process_queue(self, priority: Priority) -> None:
        queue = self._queues[priority]
        while self._is_running:
            try:
                event: EventMessage = await asyncio.wait_for(queue.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            try:
                await self._handle_event(event)
            finally:
                queue.task_done()

    async def _handle_event(self, event: EventMessage) -> None:
        callbacks = list(self._subscribers.get(event.event_type, []))
        if not callbacks:
            return
        for callback in callbacks:
            try:
                if asyncio.iscoroutinefunction(callback):
                    await asyncio.wait_for(callback(event), timeout=event.timeout)
                else:
                    callback(event)
            except asyncio.TimeoutError:
                self._schedule_retry(event, "Timeout")
            except Exception as e:
                self._schedule_retry(event, str(e))

    def _schedule_retry(self, event: EventMessage, reason: str) -> None:
        """재시도를 별도 태스크로 분리 — 큐 컨슈머가 블로킹되지 않도록 함."""
        task = asyncio.create_task(self._retry_or_dlq(event, reason))
        self._retry_tasks.append(task)
        task.add_done_callback(lambda t: self._retry_tasks.remove(t) if t in self._retry_tasks else None)

    async def _retry_or_dlq(self, event: EventMessage, reason: str) -> None:
        if event.retry_count < event.max_retries:
            event.retry_count += 1
            delay = (self.retry_backoff_base ** event.retry_count) + random.uniform(0, self.retry_jitter_max)
            logger.warning(f"Retry {event.id} in {delay:.2f}s ({reason})")
            await asyncio.sleep(delay)
            if self._is_running:
                await self._queues[event.priority].put(event)
        else:
            logger.error(f"Event {event.id} exhausted retries → DLQ ({reason})")
            await self.dlq.put({"event": event, "reason": reason, "timestamp": datetime.now().timestamp()})

    async def drain_dlq(self) -> List[dict]:
        items = []
        while not self.dlq.empty():
            items.append(await self.dlq.get())
        return items
