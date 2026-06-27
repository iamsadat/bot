"""Redis client for queues, pub/sub, and caching.

Provides abstraction over redis-py so the application can be tested offline
(FakeRedisClient) or run with real Redis in production.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from typing import Any


class BaseRedisClient(ABC):
    """Abstract Redis client interface."""

    @abstractmethod
    def queue_push(self, queue: str, item: dict) -> None:
        """Push an item to a queue."""

    @abstractmethod
    def queue_pop(self, queue: str, timeout: int = 0) -> dict | None:
        """Pop an item from a queue (blocking if timeout > 0)."""

    @abstractmethod
    def queue_size(self, queue: str) -> int:
        """Get the number of items in a queue."""

    @abstractmethod
    def publish(self, channel: str, message: dict) -> int:
        """Publish a message to a channel. Returns number of subscribers."""

    @abstractmethod
    def subscribe(self, *channels: str):
        """Subscribe to one or more channels. Returns subscription object."""

    @abstractmethod
    def cache_get(self, key: str) -> Any | None:
        """Get a cached value."""

    @abstractmethod
    def cache_set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Set a cached value with optional TTL in seconds."""

    @abstractmethod
    def cache_delete(self, key: str) -> None:
        """Delete a cached key."""


class RedisClient(BaseRedisClient):
    """Real Redis client using redis-py."""

    def __init__(self, url: str = "redis://localhost:6379/0") -> None:
        import redis
        self.client = redis.from_url(url, decode_responses=True)

    def queue_push(self, queue: str, item: dict) -> None:
        self.client.rpush(queue, json.dumps(item))

    def queue_pop(self, queue: str, timeout: int = 0) -> dict | None:
        if timeout > 0:
            result = self.client.blpop(queue, timeout=timeout)
            if result:
                _, data = result
                return json.loads(data)
            return None
        else:
            data = self.client.lpop(queue)
            if data:
                return json.loads(data)
            return None

    def queue_size(self, queue: str) -> int:
        return self.client.llen(queue)

    def publish(self, channel: str, message: dict) -> int:
        return self.client.publish(channel, json.dumps(message))

    def subscribe(self, *channels: str):
        pubsub = self.client.pubsub()
        pubsub.subscribe(*channels)
        return pubsub

    def cache_get(self, key: str) -> Any | None:
        data = self.client.get(key)
        if data:
            return json.loads(data)
        return None

    def cache_set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self.client.set(key, json.dumps(value), ex=ttl)

    def cache_delete(self, key: str) -> None:
        self.client.delete(key)


class FakeRedisClient(BaseRedisClient):
    """In-memory Redis mock for offline testing."""

    def __init__(self) -> None:
        self._queues: dict[str, list[dict]] = {}
        self._subscribers: dict[str, list[list[dict]]] = {}
        self._cache: dict[str, Any] = {}

    def queue_push(self, queue: str, item: dict) -> None:
        if queue not in self._queues:
            self._queues[queue] = []
        self._queues[queue].append(item)

    def queue_pop(self, queue: str, timeout: int = 0) -> dict | None:
        if queue not in self._queues or not self._queues[queue]:
            return None
        return self._queues[queue].pop(0)

    def queue_size(self, queue: str) -> int:
        return len(self._queues.get(queue, []))

    def publish(self, channel: str, message: dict) -> int:
        if channel not in self._subscribers:
            self._subscribers[channel] = []
        for subscriber_queue in self._subscribers[channel]:
            subscriber_queue.append(message)
        return len(self._subscribers.get(channel, []))

    def subscribe(self, *channels: str):
        """Return a fake subscription object."""
        class FakeSubscription:
            def __init__(self, client, channels):
                self.client = client
                self.channels = channels
                self.queue: list[dict] = []
                for ch in channels:
                    if ch not in self.client._subscribers:
                        self.client._subscribers[ch] = []
                    self.client._subscribers[ch].append(self.queue)

            def __iter__(self):
                return iter(self.queue)

            def unsubscribe(self):
                for ch in self.channels:
                    if ch in self.client._subscribers:
                        self.client._subscribers[ch].remove(self.queue)

        return FakeSubscription(self, channels)

    def cache_get(self, key: str) -> Any | None:
        return self._cache.get(key)

    def cache_set(self, key: str, value: Any, ttl: int | None = None) -> None:
        self._cache[key] = value

    def cache_delete(self, key: str) -> None:
        self._cache.pop(key, None)
