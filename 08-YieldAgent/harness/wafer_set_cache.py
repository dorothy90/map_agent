"""Small byte-bounded, expiring LRU for immutable derived values."""
from __future__ import annotations

from collections import OrderedDict
import sys
import time
from typing import Any


class ByteLRU:
    def __init__(self, *, max_bytes: int, max_entries: int, ttl_seconds: float):
        if max_bytes < 1 or max_entries < 1 or ttl_seconds <= 0:
            raise ValueError("Cache limits must be positive")
        self.max_bytes = max_bytes
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self._items: OrderedDict[Any, tuple[float, int, Any]] = OrderedDict()
        self._bytes = 0

    @staticmethod
    def _size(value):
        return len(value) if isinstance(value, (bytes, bytearray, memoryview)) else sys.getsizeof(value)

    def get(self, key):
        item = self._items.get(key)
        if item is None:
            return None
        deadline, size, value = item
        if deadline <= time.monotonic():
            self._items.pop(key)
            self._bytes -= size
            return None
        self._items.move_to_end(key)
        return value

    def put(self, key, value):
        size = self._size(value)
        old = self._items.pop(key, None)
        if old:
            self._bytes -= old[1]
        if size > self.max_bytes:
            return
        self._items[key] = (time.monotonic() + self.ttl_seconds, size, value)
        self._bytes += size
        while self._bytes > self.max_bytes or len(self._items) > self.max_entries:
            _, (_, removed, _) = self._items.popitem(last=False)
            self._bytes -= removed

    def clear(self):
        self._items.clear()
        self._bytes = 0

