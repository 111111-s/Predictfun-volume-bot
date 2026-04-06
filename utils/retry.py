"""
Retry с exponential backoff для API вызовов и on-chain операций.
"""
import asyncio
from random import uniform
from typing import Callable, Tuple, Type, Optional
from loguru import logger


async def retry_with_backoff(
    func: Callable,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    exceptions: Tuple[Type[Exception], ...] = (Exception,),
    on_retry: Optional[Callable] = None
):
    """
    Выполнить функцию с повторами и exponential backoff.

    Args:
        func: Async функция для вызова
        max_retries: Максимум попыток
        base_delay: Начальная задержка (секунды)
        max_delay: Максимальная задержка (секунды)
        exceptions: Типы исключений для перехвата
        on_retry: Callback при повторе (attempt, error)

    Returns:
        Результат func()

    Raises:
        Последнее исключение если все попытки неуспешны
    """
    for attempt in range(max_retries):
        try:
            return await func()
        except exceptions as e:
            if attempt == max_retries - 1:
                raise

            delay = min(base_delay * (2 ** attempt), max_delay)
            delay += uniform(0, 1)  # Jitter

            if on_retry:
                on_retry(attempt + 1, e)
            else:
                logger.warning(
                    f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s: {e}"
                )

            await asyncio.sleep(delay)
