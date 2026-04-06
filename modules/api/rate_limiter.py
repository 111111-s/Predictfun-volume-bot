"""
Rate limiter для Predict.fun API (240 req/min лимит).
Используем 200 req/min для безопасности.
"""
import asyncio
import time
from loguru import logger


class RateLimiter:
    """Token bucket rate limiter"""

    def __init__(self, max_requests: int = 200, period: float = 60.0):
        self._max_requests = max_requests
        self._period = period
        self._min_interval = period / max_requests
        self._lock = asyncio.Lock()
        self._last_request = 0.0
        self._request_count = 0
        self._window_start = 0.0

    async def acquire(self):
        """Ожидать разрешения на запрос"""
        async with self._lock:
            now = time.monotonic()

            # Reset window
            if now - self._window_start >= self._period:
                self._window_start = now
                self._request_count = 0

            # Check limit
            if self._request_count >= self._max_requests:
                wait = self._period - (now - self._window_start)
                if wait > 0:
                    logger.debug(f"Rate limit: waiting {wait:.1f}s")
                    await asyncio.sleep(wait)
                self._window_start = time.monotonic()
                self._request_count = 0

            # Min interval between requests
            elapsed = now - self._last_request
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)

            self._last_request = time.monotonic()
            self._request_count += 1
