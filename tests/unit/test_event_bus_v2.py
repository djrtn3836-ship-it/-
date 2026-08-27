"""
tests/unit/test_event_bus_v2.py - v2.0 (Session 14)
EventBus v2.0 + EventStore 단위 테스트 (52개)
"""
import asyncio
import pytest

from core.constants import Priority
from orchestrator.event_bus import EventBus, EventMessage, EventStore


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


@pytest.fixture
def bus():
    EventBus.reset_for_testing()
    b = EventBus()
    b.retry_backoff_base = 0.01
    b.retry_jitter_max = 0.01
    yield b
    EventBus.reset_for_testing()


class TestEventMessage:
    def test_default_priority_normal(self):
        assert EventMessage(event_type="X").priority == Priority.NORMAL

    def test_custom_priority(self):
        assert EventMessage(event_type="X", priority=Priority.CRITICAL).priority == Priority.CRITICAL

    def test_unique_ids(self):
        assert EventMessage(event_type="X").id != EventMessage(event_type="X").id

    def test_to_dict_keys(self):
        d = EventMessage(event_type="X").to_dict()
        for k in ["id", "event_type", "priority", "timestamp", "trace_id", "retry_count", "version"]:
            assert k in d

    def test_default_version(self):
        assert EventMessage(event_type="X").version == "1.0"

    def test_default_retry_zero(self):
        assert EventMessage(event_type="X").retry_count == 0


class TestEventStore:
    def test_save_and_get_all(self):
        async def scenario():
            store = EventStore()
            await store.save(EventMessage(event_type="A"))
            return await store.get_all()
        assert len(_run(scenario())) == 1

    def test_get_by_type(self):
        async def scenario():
            store = EventStore()
            await store.save(EventMessage(event_type="A"))
            await store.save(EventMessage(event_type="B"))
            return await store.get_by_type("A")
        events = _run(scenario())
        assert len(events) == 1 and events[0].event_type == "A"

    def test_get_by_type_empty(self):
        async def scenario():
            return await EventStore().get_by_type("NONE")
        assert _run(scenario()) == []

    def test_get_by_trace_id(self):
        async def scenario():
            store = EventStore()
            await store.save(EventMessage(event_type="A", trace_id="T-1"))
            await store.save(EventMessage(event_type="B", trace_id="T-2"))
            return await store.get_by_trace_id("T-1")
        assert len(_run(scenario())) == 1

    def test_clear(self):
        async def scenario():
            store = EventStore()
            await store.save(EventMessage(event_type="A"))
            await store.clear()
            return await store.count()
        assert _run(scenario()) == 0

    def test_count(self):
        async def scenario():
            store = EventStore()
            for _ in range(3):
                await store.save(EventMessage(event_type="A"))
            return await store.count()
        assert _run(scenario()) == 3

    def test_max_size_eviction(self):
        async def scenario():
            store = EventStore(max_size=5)
            for _ in range(10):
                await store.save(EventMessage(event_type="A"))
            return await store.count()
        assert _run(scenario()) == 5

    def test_concurrent_saves(self):
        async def scenario():
            store = EventStore()
            await asyncio.gather(*[store.save(EventMessage(event_type="A")) for _ in range(20)])
            return await store.count()
        assert _run(scenario()) == 20

    def test_get_all_returns_independent_copy(self):
        async def scenario():
            store = EventStore()
            await store.save(EventMessage(event_type="A"))
            events = await store.get_all()
            events.clear()
            return await store.count()
        assert _run(scenario()) == 1


class TestEventBusSingleton:
    def test_singleton_identity(self, bus):
        assert EventBus() is bus

    def test_reset_creates_new_instance(self):
        b1 = EventBus()
        EventBus.reset_for_testing()
        assert EventBus() is not b1

    def test_state_isolated_after_reset(self, bus):
        bus.subscribe("X", lambda e: None)
        EventBus.reset_for_testing()
        assert len(EventBus()._subscribers["X"]) == 0

    def test_new_instance_has_empty_dlq(self, bus):
        assert bus.dlq.qsize() == 0


class TestEventBusSubscribe:
    def test_subscribe_adds_callback(self, bus):
        cb = lambda e: None
        bus.subscribe("X", cb)
        assert cb in bus._subscribers["X"]

    def test_subscribe_multiple_callbacks(self, bus):
        bus.subscribe("X", lambda e: None)
        bus.subscribe("X", lambda e: None)
        assert len(bus._subscribers["X"]) == 2

    def test_unsubscribe_removes_callback(self, bus):
        cb = lambda e: None
        bus.subscribe("X", cb)
        assert bus.unsubscribe("X", cb) is True

    def test_unsubscribe_nonexistent_returns_false(self, bus):
        bus.subscribe("X", lambda e: None)
        assert bus.unsubscribe("X", lambda e: None) is False

    def test_unsubscribe_unknown_type_returns_false(self, bus):
        assert bus.unsubscribe("NOPE", lambda e: None) is False

    def test_subscribers_isolated_by_type(self, bus):
        bus.subscribe("A", lambda e: None)
        assert len(bus._subscribers["B"]) == 0


class TestEventBusPublish:
    def test_publish_saves_to_store(self, bus):
        async def scenario():
            await bus.publish(EventMessage(event_type="X"))
            return await bus.store.count()
        assert _run(scenario()) == 1

    def test_publish_routes_priority_queue(self, bus):
        _run(bus.publish(EventMessage(event_type="X", priority=Priority.HIGH)))
        assert bus._queues[Priority.HIGH].qsize() == 1

    def test_publish_multiple_priorities_isolated(self, bus):
        async def scenario():
            await bus.publish(EventMessage(event_type="X", priority=Priority.LOW))
            await bus.publish(EventMessage(event_type="X", priority=Priority.CRITICAL))
        _run(scenario())
        assert bus._queues[Priority.LOW].qsize() == 1
        assert bus._queues[Priority.CRITICAL].qsize() == 1

    def test_publish_without_subscribers_no_error(self, bus):
        _run(bus.publish(EventMessage(event_type="NOBODY_LISTENS")))

    def test_publish_preserves_event_id(self, bus):
        msg = EventMessage(event_type="X")
        async def scenario():
            await bus.publish(msg)
            return (await bus.store.get_all())[0].id
        assert _run(scenario()) == msg.id

    def test_publish_default_priority_normal_queue(self, bus):
        _run(bus.publish(EventMessage(event_type="X")))
        assert bus._queues[Priority.NORMAL].qsize() == 1


class TestEventBusProcessing:
    def test_start_creates_consumer_tasks(self, bus):
        async def scenario():
            await bus.start()
            n = len(bus._consumer_tasks)
            await bus.stop()
            return n
        assert _run(scenario()) == len(Priority)

    def test_double_start_no_duplicate_tasks(self, bus):
        async def scenario():
            await bus.start(); await bus.start()
            n = len(bus._consumer_tasks)
            await bus.stop()
            return n
        assert _run(scenario()) == len(Priority)

    def test_event_delivered_to_subscriber(self, bus):
        received = []
        async def scenario():
            bus.subscribe("PING", lambda e: received.append(e))
            await bus.start()
            await bus.publish(EventMessage(event_type="PING"))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(received) == 1

    def test_event_delivered_to_multiple_subscribers(self, bus):
        a, b = [], []
        async def scenario():
            bus.subscribe("PING", lambda e: a.append(e))
            bus.subscribe("PING", lambda e: b.append(e))
            await bus.start()
            await bus.publish(EventMessage(event_type="PING"))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(a) == 1 and len(b) == 1

    def test_sync_callback_supported(self, bus):
        received = []
        async def scenario():
            bus.subscribe("PING", lambda e: received.append(e))
            await bus.start()
            await bus.publish(EventMessage(event_type="PING"))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(received) == 1

    def test_async_callback_supported(self, bus):
        received = []
        async def async_cb(e): received.append(e)
        async def scenario():
            bus.subscribe("PING", async_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="PING"))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(received) == 1

    def test_unrelated_event_not_delivered(self, bus):
        received = []
        async def scenario():
            bus.subscribe("PING", lambda e: received.append(e))
            await bus.start()
            await bus.publish(EventMessage(event_type="PONG"))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(received) == 0

    def test_events_across_priorities_all_delivered(self, bus):
        received = []
        async def scenario():
            bus.subscribe("PING", lambda e: received.append(e))
            await bus.start()
            await bus.publish(EventMessage(event_type="PING", priority=Priority.LOW))
            await bus.publish(EventMessage(event_type="PING", priority=Priority.CRITICAL))
            await asyncio.sleep(0.1)
            await bus.stop()
        _run(scenario())
        assert len(received) == 2


class TestEventBusRetryDLQ:
    def test_retry_on_failure(self, bus):
        calls = []
        async def failing_cb(e):
            calls.append(e.retry_count)
            raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=2))
            await asyncio.sleep(0.3)
            await bus.stop()
        _run(scenario())
        assert len(calls) >= 2

    def test_retry_count_monotonic(self, bus):
        seen = []
        async def failing_cb(e):
            seen.append(e.retry_count)
            raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=2))
            await asyncio.sleep(0.3)
            await bus.stop()
        _run(scenario())
        assert seen == sorted(seen)

    def test_dlq_after_max_retries(self, bus):
        async def failing_cb(e): raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=1))
            await asyncio.sleep(0.3)
            await bus.stop()
            return bus.dlq.qsize()
        assert _run(scenario()) == 1

    def test_dlq_contains_original_event(self, bus):
        async def failing_cb(e): raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            msg = EventMessage(event_type="FAIL", max_retries=0)
            await bus.publish(msg)
            await asyncio.sleep(0.2)
            await bus.stop()
            items = await bus.drain_dlq()
            return items, msg.id
        items, msg_id = _run(scenario())
        assert items[0]["event"].id == msg_id

    def test_drain_dlq_empties_queue(self, bus):
        async def failing_cb(e): raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=0))
            await asyncio.sleep(0.2)
            await bus.stop()
            await bus.drain_dlq()
            return bus.dlq.qsize()
        assert _run(scenario()) == 0

    def test_drain_dlq_returns_reason(self, bus):
        async def failing_cb(e): raise ValueError("specific error")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=0))
            await asyncio.sleep(0.2)
            await bus.stop()
            items = await bus.drain_dlq()
            return items[0]["reason"]
        assert "specific error" in _run(scenario())

    def test_timeout_triggers_retry(self, bus):
        async def slow_cb(e): await asyncio.sleep(5)
        async def scenario():
            bus.subscribe("SLOW", slow_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="SLOW", timeout=0.05, max_retries=0))
            await asyncio.sleep(0.3)
            await bus.stop()
            return bus.dlq.qsize()
        assert _run(scenario()) == 1

    def test_successful_retry_no_dlq(self, bus):
        attempt = {"n": 0}
        async def flaky_cb(e):
            attempt["n"] += 1
            if attempt["n"] == 1:
                raise ValueError("first attempt fails")
        async def scenario():
            bus.subscribe("FLAKY", flaky_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FLAKY", max_retries=2))
            await asyncio.sleep(0.3)
            await bus.stop()
            return bus.dlq.qsize()
        assert _run(scenario()) == 0


class TestEventBusLifecycle:
    def test_stop_without_start_no_error(self, bus):
        _run(bus.stop())

    def test_is_running_false_after_stop(self, bus):
        async def scenario():
            await bus.start(); await bus.stop()
            return bus._is_running
        assert _run(scenario()) is False

    def test_multiple_stop_calls_safe(self, bus):
        async def scenario():
            await bus.start(); await bus.stop(); await bus.stop()
        _run(scenario())

    def test_retry_task_cleanup_after_completion(self, bus):
        async def failing_cb(e): raise ValueError("boom")
        async def scenario():
            bus.subscribe("FAIL", failing_cb)
            await bus.start()
            await bus.publish(EventMessage(event_type="FAIL", max_retries=0))
            await asyncio.sleep(0.3)
            remaining = len(bus._retry_tasks)
            await bus.stop()
            return remaining
        assert _run(scenario()) == 0

    def test_consumer_tasks_cleared_after_stop(self, bus):
        async def scenario():
            await bus.start(); await bus.stop()
            return len(bus._consumer_tasks)
        assert _run(scenario()) == 0
