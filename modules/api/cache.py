"""
TTL-кэш для API ответов.
Снижает нагрузку на API при повторных запросах.
"""
import time
from typing import Any, Optional, Dict, Tuple


class TTLCache:
    """Простой кэш с TTL (time-to-live)"""

    # TTL по умолчанию для разных типов данных (секунды)
    TTL_MARKET = 60.0       # Данные маркета (редко меняются)
    TTL_ORDERBOOK = 5.0     # Стакан (меняется часто)
    TTL_POSITIONS = 10.0    # Позиции
    TTL_ORDERS = 5.0        # Ордера
    TTL_BALANCE = 15.0      # Балансы
    TTL_ACCOUNT = 30.0      # Аккаунт

    def __init__(self, default_ttl: float = 30.0):
        self._cache: Dict[str, Tuple[float, Any]] = {}
        self._default_ttl = default_ttl

    def get(self, key: str) -> Optional[Any]:
        """Получить значение из кэша (None если просрочено или нет)"""
        if key in self._cache:
            expires, value = self._cache[key]
            if time.time() < expires:
                return value
            del self._cache[key]
        return None

    def set(self, key: str, value: Any, ttl: Optional[float] = None):
        """Записать значение в кэш"""
        self._cache[key] = (time.time() + (ttl or self._default_ttl), value)

    def invalidate(self, key: str):
        """Удалить конкретный ключ"""
        self._cache.pop(key, None)

    def invalidate_prefix(self, prefix: str):
        """Удалить все ключи с данным префиксом"""
        keys_to_delete = [k for k in self._cache if k.startswith(prefix)]
        for key in keys_to_delete:
            del self._cache[key]

    def clear(self):
        """Очистить весь кэш"""
        self._cache.clear()

    @property
    def size(self) -> int:
        """Количество записей в кэше"""
        return len(self._cache)
