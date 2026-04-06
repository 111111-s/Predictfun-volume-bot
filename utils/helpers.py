"""
Helper functions for Predict.fun Split Bot
"""

import asyncio
from random import uniform
from typing import Union, Tuple


async def async_sleep(delay: Union[int, float, Tuple[int, int], Tuple[float, float]]):
    """
    Асинхронная пауза с поддержкой диапазона.
    
    Args:
        delay: Секунды или (min, max) диапазон
    """
    if isinstance(delay, (list, tuple)):
        delay = uniform(delay[0], delay[1])
    await asyncio.sleep(delay)


def format_cents(price: float) -> str:
    """
    Форматирует цену в центы для читабельности.
    
    0.55 -> "55¢"
    0.5123 -> "51.2¢"
    """
    cents = price * 100
    if cents == int(cents):
        return f"{int(cents)}¢"
    return f"{cents:.1f}¢"


def format_address(address: str, start: int = 6, end: int = 4) -> str:
    """
    Сокращает адрес для отображения.
    
    0x1234...abcd
    """
    if len(address) <= start + end + 3:
        return address
    return f"{address[:start]}...{address[-end:]}"


def format_usd(value: float) -> str:
    """Форматирует USD"""
    if value >= 0:
        return f"${value:.2f}"
    return f"-${abs(value):.2f}"


def parse_market_url(url: str) -> Tuple[str, str]:
    """
    Парсит URL маркета и извлекает market_id и choice.
    
    Returns:
        (market_id, choice)
    """
    # https://predict.fun/market/123456
    # https://predict.fun/market/123456:1
    
    market_id = ""
    choice = ""
    
    if "/market/" in url:
        path = url.split("/market/")[-1]
        # Убираем query params
        path = path.split("?")[0].split("/")[0]
        
        if ":" in path:
            parts = path.split(":")
            market_id = parts[0]
            choice = parts[1] if len(parts) > 1 else ""
        else:
            market_id = path
    else:
        # Возможно просто ID
        if ":" in url:
            parts = url.split(":")
            market_id = parts[0]
            choice = parts[1] if len(parts) > 1 else ""
        else:
            market_id = url
    
    return (market_id, choice)
