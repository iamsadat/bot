"""A small on-disk cache for LLM text, and a usage counter.

The résumé rewrite is the bulk of this app's LLM spend, and most of it is
repeated work: the owner's bullets are the same for every job, so editing
them again for each of 8 matches per sweep, 4 sweeps a day, buys nothing.
Keyed on the exact input, a cached answer is what the model would have
been asked to produce anyway.

``JOBHUNT_LLM_CACHE`` sets the file (default ``~/.jobhunt/llm-cache.json``);
``off`` keeps it in memory only.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

_MAX_ENTRIES = 2000


class TextCache:
    def __init__(self, path: str | os.PathLike | None) -> None:
        self._path = Path(path).expanduser() if path else None
        self._lock = threading.Lock()
        self._items: OrderedDict[str, str] = OrderedDict()
        if self._path and self._path.exists():
            try:
                self._items.update(json.loads(self._path.read_text("utf-8")))
            except (OSError, ValueError):
                self._items.clear()

    @staticmethod
    def key(*parts: object) -> str:
        blob = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]

    def get(self, key: str) -> str | None:
        with self._lock:
            value = self._items.get(key)
            if value is not None:
                self._items.move_to_end(key)
            return value

    def put(self, key: str, value: str) -> None:
        if not value:
            return  # an empty answer means "fell back"; worth asking again
        with self._lock:
            self._items[key] = value
            self._items.move_to_end(key)
            while len(self._items) > _MAX_ENTRIES:
                self._items.popitem(last=False)
            if self._path is None:
                return
            try:
                self._path.parent.mkdir(parents=True, exist_ok=True)
                tmp = self._path.with_suffix(".tmp")
                tmp.write_text(json.dumps(self._items, ensure_ascii=False), "utf-8")
                tmp.replace(self._path)
            except OSError:
                pass  # a cache that cannot be written is just a smaller cache


_cache: TextCache | None = None


def shared_cache() -> TextCache:
    global _cache
    if _cache is None:
        where = os.environ.get("JOBHUNT_LLM_CACHE", "~/.jobhunt/llm-cache.json").strip()
        serverless = bool(os.environ.get("VERCEL"))
        _cache = TextCache(None if serverless or where.lower() in ("", "off", "0")
                           else where)
    return _cache


# ------------------------------------------------------------------ usage


class Usage:
    """LLM calls and tokens since start and today, for /api/status."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.reset()

    def reset(self) -> None:
        self.total = {"calls": 0, "cached": 0, "input_tokens": 0, "output_tokens": 0}
        self.day = time.strftime("%Y-%m-%d")
        self.today = dict(self.total)

    def _roll(self) -> None:
        day = time.strftime("%Y-%m-%d")
        if day != self.day:
            self.day, self.today = day, {k: 0 for k in self.total}

    def record(self, *, input_tokens: int = 0, output_tokens: int = 0,
               cached: bool = False) -> None:
        with self._lock:
            self._roll()
            for bucket in (self.total, self.today):
                if cached:
                    bucket["cached"] += 1
                else:
                    bucket["calls"] += 1
                    bucket["input_tokens"] += int(input_tokens or 0)
                    bucket["output_tokens"] += int(output_tokens or 0)

    def snapshot(self) -> dict:
        with self._lock:
            self._roll()
            return {"today": dict(self.today), "since_start": dict(self.total)}


usage = Usage()
