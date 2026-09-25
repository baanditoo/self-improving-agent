"""Per-vendor token bucket with jitter."""

from __future__ import annotations

import asyncio
import random
import time


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float) -> None:
        self.rate = max(rate_per_sec, 0.1)
        self.capacity = max(capacity, 1)
        self.tokens = self.capacity
        self.updated = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, cost: float = 1) -> None:
        cost = max(cost, 0.1)
        while True:
            async with self._lock:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated) * self.rate)
                self.updated = now
                if self.tokens >= cost:
                    self.tokens -= cost
                    break
                wait = (cost - self.tokens) / self.rate
            await asyncio.sleep(wait)
        await asyncio.sleep(random.uniform(0.01, 0.08))
