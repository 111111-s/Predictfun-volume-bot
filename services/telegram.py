"""
Telegram Service for Predict.fun Split Bot

Уведомления в Telegram - повний порт з Opinion.trade
"""

import io
import asyncio
from typing import Optional, Dict, List
from datetime import datetime, timezone

import aiohttp
from loguru import logger

from config import config


class TelegramService:
    """Сервіс відправки повідомлень в Telegram"""
    
    BASE_URL = "https://api.telegram.org/bot{token}"
    
    def __init__(self):
        self._session: Optional[aiohttp.ClientSession] = None
        self._weekly_task: Optional[asyncio.Task] = None
    
    @property
    def enabled(self) -> bool:
        return config.telegram.enabled
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    async def send(self, message: str, parse_mode: str = "HTML") -> bool:
        """Відправити повідомлення всім користувачам"""
        if not self.enabled:
            return False
        
        try:
            session = await self._get_session()
            url = f"{self.BASE_URL.format(token=config.telegram.bot_token)}/sendMessage"
            
            for user_id in config.telegram.user_ids:
                await session.post(url, json={
                    "chat_id": user_id,
                    "text": message,
                    "parse_mode": parse_mode,
                    "disable_web_page_preview": True
                })
            
            return True
            
        except Exception as e:
            logger.debug(f"Telegram send error: {e}")
            return False
    
    async def send_document(
        self,
        document: io.BytesIO,
        filename: str,
        caption: str = "",
        parse_mode: str = "HTML"
    ) -> bool:
        """Відправити документ"""
        if not self.enabled:
            return False
        
        try:
            session = await self._get_session()
            url = f"{self.BASE_URL.format(token=config.telegram.bot_token)}/sendDocument"
            
            for user_id in config.telegram.user_ids:
                document.seek(0)
                
                form = aiohttp.FormData()
                form.add_field("chat_id", str(user_id))
                form.add_field("document", document, filename=filename)
                if caption:
                    form.add_field("caption", caption)
                    form.add_field("parse_mode", parse_mode)
                
                await session.post(url, data=form)
            
            return True
            
        except Exception as e:
            logger.debug(f"Telegram document error: {e}")
            return False
    
    async def send_alert(self, label: str, message: str):
        """Алерт"""
        await self.send(f"⚠️ <b>{label}</b>\n{message}")
    
    async def send_stop_loss(
        self, 
        label: str, 
        market: str,
        side: str = "",
        entry_price: float = 0,
        current_price: float = 0,
        loss_percent: float = 0,
        amount: float = 0
    ):
        """Повідомлення про стоп-лос"""
        loss_usd = 0
        if amount > 0 and entry_price > 0 and current_price > 0:
            loss_usd = amount * (entry_price - current_price) / 100
        
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        msg = f"🛑 <b>STOP-LOSS TRIGGERED</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"👤 Account: <code>{label}</code>\n"
        msg += f"📊 Market: {market}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        if side:
            msg += f"📍 Side: <b>{side}</b>\n"
        if entry_price > 0:
            msg += f"💵 Entry price: {entry_price:.1f}¢\n"
        if current_price > 0:
            msg += f"📉 Market price: {current_price:.1f}¢\n"
        if loss_percent > 0:
            msg += f"📊 Drop: <b>-{loss_percent:.1f}%</b>\n"
        if amount > 0:
            msg += f"📦 Tokens sold: {amount:.2f}\n"
        if loss_usd > 0:
            msg += f"💸 Est. loss: <b>-${loss_usd:.2f}</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        await self.send(msg)
    
    async def send_order_filled(
        self,
        label: str,
        market: str,
        side: str,
        price: float,
        amount: float,
        revenue: float,
        remaining_side: str = ""
    ):
        """Повідомлення про виконання ордера"""
        emoji = "🟢" if side == "YES" else "🔴"
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        msg = f"{emoji} <b>ORDER FILLED</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"👤 Account: <code>{label}</code>\n"
        msg += f"📊 Market: {market}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📍 Side: <b>{side}</b> @ {price:.1f}¢\n"
        msg += f"📦 Tokens: {amount:.2f}\n"
        msg += f"💵 Revenue: <b>${revenue:.2f}</b>\n"
        if remaining_side:
            msg += f"━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"⏳ Waiting: {remaining_side}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        await self.send(msg)
    
    async def send_position_closed(
        self,
        label: str,
        market: str,
        volume: float,
        yes_price: float = 0,
        no_price: float = 0
    ):
        """Повідомлення про закриття позиції"""
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        msg = f"✅ <b>POSITION CLOSED</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"👤 Account: <code>{label}</code>\n"
        msg += f"📊 Market: {market}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        if yes_price > 0:
            msg += f"🟢 YES sold @ {yes_price:.1f}¢\n"
        if no_price > 0:
            msg += f"🔴 NO sold @ {no_price:.1f}¢\n"
        msg += f"💰 Total volume: <b>${volume:.2f}</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        await self.send(msg)
    
    async def send_markets_watching(
        self,
        label: str,
        markets: list,
        total_volume: float = 0
    ):
        """Повідомлення що всі маркети запущені"""
        if not markets:
            return
        
        now = datetime.now(timezone.utc).strftime("%H:%M UTC")
        
        market_list = "\n".join([f"  • {m}" for m in markets[:10]])
        if len(markets) > 10:
            market_list += f"\n  ... +{len(markets) - 10} more"
        
        msg = f"👁 <b>ALL MARKETS WATCHING</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"👤 Account: <code>{label}</code>\n"
        msg += f"📊 Active markets: <b>{len(markets)}</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"{market_list}\n"
        if total_volume > 0:
            msg += f"━━━━━━━━━━━━━━━━━━━━\n"
            msg += f"💰 Total invested: ${total_volume:.2f}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        
        await self.send(msg)
    
    async def send_start(
        self, 
        accounts: int, 
        markets: int, 
        threads: int,
        cycles: tuple = (1, 1),
        sl_enabled: bool = True,
        sl_percent: float = 4.0
    ):
        """Повідомлення про старт"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        sl_status = f"✅ {sl_percent}%" if sl_enabled else "❌ Off"
        
        msg = f"🚀 <b>PREDICT.FUN BOT STARTED</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"👤 Accounts: <b>{accounts}</b>\n"
        msg += f"📊 Markets: <b>{markets}</b>\n"
        msg += f"⚡ Threads: <b>{threads}</b>\n"
        msg += f"🔄 Cycles: <b>{cycles[0]}-{cycles[1]}</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🛑 Stop-Loss: {sl_status}\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        
        await self.send(msg)
    
    async def send_finish(self, total: int, success: int, failed: int, stop_losses: int):
        """Фінальний звіт"""
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        success_rate = (success / total * 100) if total > 0 else 0
        
        msg = f"🏁 <b>SESSION FINISHED</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"📊 Total cycles: <b>{total}</b>\n"
        msg += f"✅ Successful: <b>{success}</b> ({success_rate:.0f}%)\n"
        msg += f"❌ Failed: <b>{failed}</b>\n"
        msg += f"🛑 Stop-losses: <b>{stop_losses}</b>\n"
        msg += f"━━━━━━━━━━━━━━━━━━━━\n"
        msg += f"🕐 {now}"
        
        await self.send(msg)
    
    async def send_statistics_excel(
        self,
        excel_buffer: io.BytesIO,
        filename: str,
        summary: Dict
    ) -> bool:
        """Відправити Excel статистику"""
        if not self.enabled:
            return False
        
        caption = (
            f"📊 <b>Statistics Report</b>\n\n"
            f"📈 Trades: {summary.get('total_trades', 0)}\n"
            f"💰 Volume: ${summary.get('total_volume', 0)}\n"
            f"💵 PnL: ${summary.get('total_pnl', 0)}\n"
            f"👤 Accounts: {summary.get('accounts', 0)}\n"
            f"🛑 Stop-losses: {summary.get('stop_losses', 0)}"
        )
        
        return await self.send_document(excel_buffer, filename, caption)
    
    async def close(self):
        """Закрити сесію"""
        if self._session and not self._session.closed:
            await self._session.close()


# Глобальний екземпляр
telegram = TelegramService()
