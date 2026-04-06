"""
Entry Prices Service for Predict.fun Split Bot

Хранение цен входа для корректного расчёта стоп-лоссов.
"""

import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Optional
from threading import Lock

from loguru import logger

from config import DATABASE_DIR


class EntryPricesService:
    """
    Сервис для хранения цен входа.
    
    Используется для корректного расчёта стоп-лоссов после рестарта бота.
    Без этого при перезапуске теряются оригинальные цены входа.
    """
    
    def __init__(self):
        self._file_path = DATABASE_DIR / "entry_prices.json"
        self._lock = Lock()
        self._ensure_file()
    
    def _ensure_file(self):
        """Создать файл если не существует"""
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._save({})
    
    def _load(self) -> Dict:
        """Загрузить данные"""
        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def _save(self, data: Dict):
        """Сохранить данные"""
        with open(self._file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2)
    
    def set_entry_prices(
        self,
        market_full_id: str,
        yes_entry_price: float,
        no_entry_price: float
    ):
        """
        Сохранить цены входа для маркета.
        
        Args:
            market_full_id: Полный ID маркета (включая choice для мульти)
            yes_entry_price: Цена входа YES
            no_entry_price: Цена входа NO
        """
        with self._lock:
            data = self._load()
            
            data[market_full_id] = {
                "yes_entry_price": yes_entry_price,
                "no_entry_price": no_entry_price,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat()
            }
            
            self._save(data)
            logger.debug(f"Saved entry prices for {market_full_id}")
    
    def get_entry_prices(self, market_full_id: str) -> Optional[Dict]:
        """
        Получить цены входа для маркета.
        
        Returns:
            Dict с yes_entry_price и no_entry_price или None
        """
        with self._lock:
            data = self._load()
            return data.get(market_full_id)
    
    def delete_entry_prices(self, market_full_id: str):
        """Удалить цены входа (после закрытия позиции)"""
        with self._lock:
            data = self._load()
            
            if market_full_id in data:
                del data[market_full_id]
                self._save(data)
                logger.debug(f"Deleted entry prices for {market_full_id}")
    
    def cleanup_old_entries(self, days: int = 7):
        """
        Очистить старые записи.
        
        Записи старше N дней удаляются (позиции скорее всего уже закрыты).
        """
        with self._lock:
            data = self._load()
            cutoff = datetime.now() - timedelta(days=days)
            
            to_delete = []
            for market_id, entry in data.items():
                created_str = entry.get("created_at", "")
                try:
                    created = datetime.fromisoformat(created_str)
                    if created < cutoff:
                        to_delete.append(market_id)
                except (ValueError, TypeError):
                    pass
            
            if to_delete:
                for market_id in to_delete:
                    del data[market_id]
                self._save(data)
                logger.info(f"Cleaned up {len(to_delete)} old entry price records")
    
    def get_all_entries(self) -> Dict:
        """Получить все записи"""
        with self._lock:
            return self._load()
    
    def clear_all(self):
        """Очистить все записи"""
        with self._lock:
            self._save({})
            logger.info("Cleared all entry prices")


# Синглтон
entry_prices = EntryPricesService()
