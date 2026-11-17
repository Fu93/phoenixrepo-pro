"""Bound concurrent model calls.

NVIDIA's free key is about 40 requests a minute. A semaphore caps how many
calls are in flight. A spacing rule stops a burst from starting faster than
that budget. The clock can be replaced in tests.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")
R = TypeVar("R")


class Pace:
    def __init__(self, *, concurrency: int = 4, per_minute: int = 40, sleep=time.sleep, clock=time.monotonic) -> None:
        self.concurrency = max(1, concurrency)
        self.per_minute = max(1, per_minute)
        self._gap = 60.0 / self.per_minute
        self._sleep = sleep
        self._clock = clock
        self._sem = threading.Semaphore(self.concurrency)
        self._lock = threading.Lock()
        self._starts: deque[float] = deque()

    def slot(self):
        return _Slot(self)

    def _acquire(self) -> None:
        self._sem.acquire()
        while True:
            with self._lock:
                now = self._clock()
                while self._starts and now - self._starts[0] >= 60.0:
                    self._starts.popleft()
                wait = 0.0
                if self._starts and now - self._starts[-1] < self._gap:
                    wait = self._gap - (now - self._starts[-1])
                elif len(self._starts) >= self.per_minute:
                    wait = 60.0 - (now - self._starts[0])
                if wait <= 0:
                    self._starts.append(now)
                    return
            self._sleep(wait)

    def _release(self) -> None:
        self._sem.release()


class _Slot:
    def __init__(self, pace: Pace) -> None:
        self._pace = pace

    def __enter__(self) -> Pace:
        self._pace._acquire()
        return self._pace

    def __exit__(self, *_) -> None:
        self._pace._release()


def map_ordered(fn: Callable[[T], R], items: Iterable[T], *, workers: int = 4) -> list[R]:
    """Run fn across items. workers=1 keeps the caller's thread and order."""

    rows = list(items)
    if workers <= 1 or len(rows) <= 1:
        return [fn(item) for item in rows]
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(fn, rows))
