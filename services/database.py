"""
Database Service for Predict.fun Split Bot

Работа с локальными JSON базами данных.
"""

import json
from pathlib import Path
from datetime import datetime
from typing import Dict, List, Optional, Any
from threading import Lock

from loguru import logger

from config import DATABASE_DIR


class JSONDatabase:
    """Базовый класс для JSON баз данных"""
    
    def __init__(self, filename: str):
        self._file_path = DATABASE_DIR / filename
        self._lock = Lock()
        self._ensure_file()
    
    def _ensure_file(self):
        """Создать файл если не существует"""
        DATABASE_DIR.mkdir(parents=True, exist_ok=True)
        if not self._file_path.exists():
            self._save({})
    
    def _load(self) -> Dict:
        """Загрузить данные из файла"""
        try:
            with open(self._file_path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            return {}
    
    def _save(self, data: Dict):
        """Сохранить данные в файл"""
        with open(self._file_path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False, default=str)


class BlacklistService(JSONDatabase):
    """Сервис для работы с blacklist маркетов"""
    
    def __init__(self):
        super().__init__("blacklist.json")
    
    def is_blacklisted(self, market_id: str) -> bool:
        """Проверить находится ли маркет в blacklist"""
        with self._lock:
            data = self._load()
            blacklist = data.get("blacklist", [])
            return market_id in blacklist
    
    def add_to_blacklist(self, market_id: str, reason: str = ""):
        """Добавить маркет в blacklist"""
        with self._lock:
            data = self._load()
            blacklist = data.get("blacklist", [])
            
            if market_id not in blacklist:
                blacklist.append(market_id)
                data["blacklist"] = blacklist
                
                # Сохраняем причину
                reasons = data.get("reasons", {})
                reasons[market_id] = {
                    "reason": reason,
                    "added_at": datetime.now().isoformat()
                }
                data["reasons"] = reasons
                
                self._save(data)
                logger.info(f"Added to blacklist: {market_id}")
    
    def remove_from_blacklist(self, market_id: str):
        """Удалить маркет из blacklist"""
        with self._lock:
            data = self._load()
            blacklist = data.get("blacklist", [])
            
            if market_id in blacklist:
                blacklist.remove(market_id)
                data["blacklist"] = blacklist
                
                # Удаляем причину
                reasons = data.get("reasons", {})
                reasons.pop(market_id, None)
                data["reasons"] = reasons
                
                self._save(data)
                logger.info(f"Removed from blacklist: {market_id}")
    
    def get_blacklist(self) -> List[str]:
        """Получить весь blacklist"""
        with self._lock:
            data = self._load()
            return data.get("blacklist", [])
    
    def clear_blacklist(self):
        """Очистить blacklist"""
        with self._lock:
            self._save({"blacklist": [], "reasons": {}})
            logger.info("Blacklist cleared")


class TradesDatabase(JSONDatabase):
    """База данных сделок"""
    
    def __init__(self):
        super().__init__("trades.json")
    
    def add_trade(
        self,
        account_address: str,
        market_id: str,
        market_name: str,
        side: Any,
        action: str,  # "buy" or "sell"
        amount: float,
        price: float,
        value: float,
        pnl: float = 0.0,
        stopped_out: bool = False
    ):
        """Добавить запись о сделке"""
        with self._lock:
            data = self._load()
            trades = data.get("trades", [])
            
            trade = {
                "timestamp": datetime.now().isoformat(),
                "account": account_address,
                "market_id": market_id,
                "market_name": market_name,
                "side": side.value if hasattr(side, 'value') else str(side),
                "action": action,
                "amount": amount,
                "price": price,
                "value": value,
                "pnl": pnl,
                "stopped_out": stopped_out
            }
            
            trades.append(trade)
            data["trades"] = trades
            self._save(data)
    
    def get_trades(
        self,
        account_address: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """Получить сделки"""
        with self._lock:
            data = self._load()
            trades = data.get("trades", [])
            
            if account_address:
                trades = [t for t in trades if t.get("account") == account_address]
            
            # Сортируем по времени (новые первые)
            trades.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
            
            return trades[:limit]
    
    def get_daily_stats(self, account_address: Optional[str] = None) -> Dict:
        """Получить статистику за сегодня"""
        today = datetime.now().date().isoformat()
        trades = self.get_trades(account_address)
        
        daily_trades = [
            t for t in trades
            if t.get("timestamp", "").startswith(today)
        ]
        
        return {
            "trades": len(daily_trades),
            "volume": sum(t.get("value", 0) for t in daily_trades),
            "pnl": sum(t.get("pnl", 0) for t in daily_trades),
            "stopped_out": sum(1 for t in daily_trades if t.get("stopped_out"))
        }


class StatsDatabase(JSONDatabase):
    """База данных статистики"""
    
    def __init__(self):
        super().__init__("stats.json")
    
    def update_stats(
        self,
        account_address: str,
        volume: float,
        pnl: float,
        trades_count: int = 1
    ):
        """Обновить статистику аккаунта"""
        with self._lock:
            data = self._load()
            accounts = data.get("accounts", {})
            
            if account_address not in accounts:
                accounts[account_address] = {
                    "total_volume": 0,
                    "total_pnl": 0,
                    "total_trades": 0,
                    "first_trade": datetime.now().isoformat()
                }
            
            accounts[account_address]["total_volume"] += volume
            accounts[account_address]["total_pnl"] += pnl
            accounts[account_address]["total_trades"] += trades_count
            accounts[account_address]["last_trade"] = datetime.now().isoformat()
            
            data["accounts"] = accounts
            self._save(data)
    
    def get_account_stats(self, account_address: str) -> Dict:
        """Получить статистику аккаунта"""
        with self._lock:
            data = self._load()
            accounts = data.get("accounts", {})
            return accounts.get(account_address, {})
    
    def get_total_stats(self) -> Dict:
        """Получить общую статистику"""
        with self._lock:
            data = self._load()
            accounts = data.get("accounts", {})
            
            return {
                "accounts": len(accounts),
                "total_volume": sum(a.get("total_volume", 0) for a in accounts.values()),
                "total_pnl": sum(a.get("total_pnl", 0) for a in accounts.values()),
                "total_trades": sum(a.get("total_trades", 0) for a in accounts.values())
            }


# Синглтоны
blacklist = BlacklistService()
trades_db = TradesDatabase()
stats_db = StatsDatabase()
