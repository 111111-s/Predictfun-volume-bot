"""
Statistics Service for Predict.fun Split Bot

Сервис статистики и отчётов.
"""

from datetime import datetime, timedelta
from typing import Dict, List

from .database import trades_db, stats_db


class StatsService:
    """Сервис статистики"""
    
    def get_daily_stats(self, account_address: str = None) -> Dict:
        """Получить статистику за сегодня"""
        return trades_db.get_daily_stats(account_address)
    
    def get_weekly_stats(self, account_address: str = None) -> Dict:
        """Получить статистику за неделю"""
        week_ago = datetime.now() - timedelta(days=7)
        trades = trades_db.get_trades(account_address, limit=1000)
        
        weekly_trades = []
        for t in trades:
            try:
                ts = datetime.fromisoformat(t.get("timestamp", ""))
                if ts >= week_ago:
                    weekly_trades.append(t)
            except (ValueError, TypeError):
                pass
        
        wins = sum(1 for t in weekly_trades if t.get("pnl", 0) > 0)
        losses = sum(1 for t in weekly_trades if t.get("pnl", 0) < 0)
        total = wins + losses
        
        return {
            "period": "Last 7 days",
            "trades": len(weekly_trades),
            "volume": sum(t.get("value", 0) for t in weekly_trades),
            "pnl": sum(t.get("pnl", 0) for t in weekly_trades),
            "wins": wins,
            "losses": losses,
            "win_rate": (wins / total * 100) if total > 0 else 0,
            "stopped_out": sum(1 for t in weekly_trades if t.get("stopped_out"))
        }
    
    def get_account_stats(self, account_address: str) -> Dict:
        """Получить полную статистику аккаунта"""
        return stats_db.get_account_stats(account_address)
    
    def get_total_stats(self) -> Dict:
        """Получить общую статистику по всем аккаунтам"""
        return stats_db.get_total_stats()
    
    def get_recent_trades(self, limit: int = 10) -> List[Dict]:
        """Получить последние сделки"""
        return trades_db.get_trades(limit=limit)


# Синглтон
stats_service = StatsService()
