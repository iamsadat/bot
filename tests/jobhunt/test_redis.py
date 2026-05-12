"""Tests for Redis client abstraction (queues, pub/sub, caching).

Tests both FakeRedisClient (offline) and validate the interface contract.
Real Redis tests are integration tests (require external Redis).
"""

import pytest
from jobhunt.redis_client import FakeRedisClient


@pytest.fixture
def redis_client():
    """Return a fake Redis client for offline tests."""
    return FakeRedisClient()


def test_queue_push_and_pop(redis_client):
    """Test basic queue operations."""
    item = {"job_id": "j-123", "action": "vet"}
    redis_client.queue_push("jobs:pending", item)

    popped = redis_client.queue_pop("jobs:pending")
    assert popped == item


def test_queue_fifo_order(redis_client):
    """Test that queue maintains FIFO order."""
    redis_client.queue_push("q", {"id": 1})
    redis_client.queue_push("q", {"id": 2})
    redis_client.queue_push("q", {"id": 3})

    assert redis_client.queue_pop("q")["id"] == 1
    assert redis_client.queue_pop("q")["id"] == 2
    assert redis_client.queue_pop("q")["id"] == 3


def test_queue_pop_empty(redis_client):
    """Test popping from empty queue returns None."""
    result = redis_client.queue_pop("nonexistent")
    assert result is None


def test_queue_size(redis_client):
    """Test queue size tracking."""
    assert redis_client.queue_size("q") == 0

    redis_client.queue_push("q", {"id": 1})
    assert redis_client.queue_size("q") == 1

    redis_client.queue_push("q", {"id": 2})
    assert redis_client.queue_size("q") == 2

    redis_client.queue_pop("q")
    assert redis_client.queue_size("q") == 1


def test_publish_and_subscribe(redis_client):
    """Test pub/sub messaging."""
    # Subscribe to channel
    sub = redis_client.subscribe("thought:stream", "jobs:updates")

    # Publish messages
    redis_client.publish("thought:stream", {"agent": "discovery", "thought": "Found 10 jobs"})
    redis_client.publish("jobs:updates", {"job_id": "j-456", "status": "applied"})

    # Check subscription queue received messages
    messages = list(sub.queue)
    assert len(messages) == 2
    assert messages[0]["agent"] == "discovery"
    assert messages[1]["job_id"] == "j-456"


def test_publish_returns_subscriber_count(redis_client):
    """Test that publish returns number of subscribers."""
    sub1 = redis_client.subscribe("ch")
    sub2 = redis_client.subscribe("ch")

    count = redis_client.publish("ch", {"msg": "test"})
    assert count == 2


def test_publish_to_non_subscribed_channel(redis_client):
    """Test publishing to a channel with no subscribers."""
    count = redis_client.publish("empty", {"msg": "test"})
    assert count == 0


def test_cache_operations(redis_client):
    """Test cache get/set/delete."""
    # Set and get
    redis_client.cache_set("user:1", {"name": "Alice", "email": "alice@example.com"})
    cached = redis_client.cache_get("user:1")
    assert cached["name"] == "Alice"

    # Nonexistent key
    assert redis_client.cache_get("user:999") is None

    # Delete
    redis_client.cache_delete("user:1")
    assert redis_client.cache_get("user:1") is None


def test_cache_with_ttl(redis_client):
    """Test that cache_set accepts TTL (though FakeRedisClient ignores it)."""
    # Just verify the method signature works
    redis_client.cache_set("temp:key", {"value": 42}, ttl=60)
    assert redis_client.cache_get("temp:key")["value"] == 42


def test_multiple_queues_independent(redis_client):
    """Test that queues are independent."""
    redis_client.queue_push("q1", {"id": "a"})
    redis_client.queue_push("q2", {"id": "b"})

    assert redis_client.queue_pop("q1")["id"] == "a"
    assert redis_client.queue_pop("q2")["id"] == "b"


def test_unsubscribe_removes_subscription(redis_client):
    """Test that unsubscribe stops receiving messages."""
    sub = redis_client.subscribe("ch")

    redis_client.publish("ch", {"msg": "before"})
    assert len(sub.queue) == 1

    sub.unsubscribe()

    redis_client.publish("ch", {"msg": "after"})
    assert len(sub.queue) == 1  # Still 1, didn't receive "after"
