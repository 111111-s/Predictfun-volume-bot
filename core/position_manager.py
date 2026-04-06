"""
Position Manager for Predict.fun

Управление позициями: merge, market sell, cancel orders.
Точная копия логики Opinion.trade бота.
"""

import math
from typing import Optional, List, Dict
from loguru import logger

from config import config
from constants import MIN_ORDER_VALUE_USD, MIN_MERGE_VALUE_USD
from models import OrderSide
from modules import PredictBrowser
from utils import async_sleep


class PositionManager:
    """Менеджер позиций для merge и market sell"""
    
    def __init__(self, browser: PredictBrowser, label: str = ""):
        self._browser = browser
        self._label = label or browser.address[:10]
    
    def _log(self, text: str, level: str = "INFO"):
        """Логирование"""
        logger.opt(colors=True).log(level, f'<white>{self._label}</white> | {text}')
    
    async def get_all_positions(self) -> List:
        """Получить все позиции аккаунта"""
        return await self._browser.get_positions()
    
    async def get_all_open_orders(self) -> List:
        """Получить все открытые ордера"""
        all_orders = await self._browser.get_orders()
        # Filter for open orders on client side
        return [o for o in all_orders if o.status.lower() in ("open", "partial", "pending")]
    
    async def cancel_all_orders(self) -> int:
        """Отменить все открытые ордера через SDK"""
        orders = await self.get_all_open_orders()
        cancelled = 0
        
        for order in orders:
            try:
                # Use SDK cancel with full order data
                if order.raw_data:
                    is_neg_risk = order.raw_data.get("isNegRisk", False)
                    is_yield_bearing = order.raw_data.get("isYieldBearing", False)
                    result = await self._browser.cancel_order_by_data(
                        order.raw_data,
                        is_yield_bearing=is_yield_bearing,
                        is_neg_risk=is_neg_risk
                    )
                    if result:
                        cancelled += 1
                else:
                    # Fallback to hash-based cancel
                    order_hash = order.order_hash if order.order_hash else order.order_id
                    await self._browser.cancel_order(order_hash)
                    cancelled += 1
                await async_sleep((0.5, 1))
            except Exception as e:
                self._log(f"Cancel failed: {e}", "DEBUG")
        
        return cancelled
    
    async def merge_position(self, condition_id: str, amount: float) -> bool:
        """
        Выполнить merge YES + NO -> USDT через SDK.
        
        Args:
            condition_id: ID условия маркета
            amount: Количество токенов для merge
        """
        try:
            result = await self._browser.merge_positions(
                condition_id=condition_id,
                amount=amount
            )
            return result.success if hasattr(result, 'success') else bool(result)
        except Exception as e:
            self._log(f"Merge failed: {e}", "ERROR")
            return False
    
    async def merge_all_positions(self) -> Dict[str, int]:
        """
        Merge все позиции где есть и YES и NO.
        
        Returns:
            Dict с количеством merged и failed
        """
        positions = await self.get_all_positions()
        
        # Группируем по market_id
        by_market: Dict[str, Dict] = {}
        for pos in positions:
            market_id = pos.market_id
            if not market_id:
                continue
            
            if market_id not in by_market:
                by_market[market_id] = {
                    "yes": None,
                    "no": None,
                    "condition_id": None,
                    "title": ""
                }
            
            if pos.is_yes:
                by_market[market_id]["yes"] = pos
            else:
                by_market[market_id]["no"] = pos
            
            if pos.condition_id:
                by_market[market_id]["condition_id"] = pos.condition_id
        
        merged = 0
        failed = 0
        skipped = 0
        
        self._log(f"Found {len(by_market)} markets with positions")
        
        for market_id, data in by_market.items():
            yes_pos = data["yes"]
            no_pos = data["no"]
            condition_id = data["condition_id"]
            
            # Нужны оба токена для merge
            if not yes_pos or not no_pos:
                skipped += 1
                continue
            
            if not condition_id:
                skipped += 1
                continue
            
            yes_amount = yes_pos.balance
            no_amount = no_pos.balance
            
            # Merge можно только минимум из двух
            merge_amount = min(yes_amount, no_amount)
            
            if merge_amount <= 0.01:
                skipped += 1
                continue
            
            # Retry логика
            max_retries = 3
            for attempt in range(1, max_retries + 1):
                try:
                    self._log(
                        f"Merging {merge_amount:.2f} [market: {market_id[:12]}...]"
                        + (f" (attempt {attempt})" if attempt > 1 else "")
                    )
                    
                    await self.merge_position(condition_id, merge_amount)
                    merged += 1
                    
                    await async_sleep((1, 2))
                    break
                    
                except Exception as e:
                    if attempt < max_retries:
                        await async_sleep((2, 3))
                    else:
                        self._log(f"Merge failed: {e}", "ERROR")
                        failed += 1
        
        if skipped > 0:
            self._log(f"Skipped {skipped} markets (no pair or too small)")
        
        return {"merged": merged, "failed": failed, "skipped": skipped}
    
    async def market_sell_position(
        self,
        market_id: str,
        outcome: str,
        amount: float
    ) -> bool:
        """
        Продать позицию по маркету.
        
        Args:
            market_id: ID маркета
            outcome: "yes" или "no"
            amount: Количество для продажи
        """
        try:
            # Маркет ордер = очень низкая цена
            await self._browser.create_order(
                market_id=market_id,
                outcome=outcome,
                side="sell",
                price=0.01,
                size=amount
            )
            return True
        except Exception as e:
            self._log(f"Market sell failed: {e}", "ERROR")
            return False
    
    async def market_sell_all_positions(self) -> Dict[str, int]:
        """
        Продать все позиции по маркету.
        
        Returns:
            Dict с количеством sold и failed
        """
        positions = await self.get_all_positions()
        
        sold = 0
        failed = 0
        
        MIN_ORDER_VALUE = MIN_ORDER_VALUE_USD
        
        self._log(f"Found {len(positions)} positions total")
        
        for pos in positions:
            market_id = pos.market_id
            outcome = "yes" if pos.is_yes else "no"
            
            # Доступное количество
            available = pos.balance
            
            if available <= 0.01:
                continue
            
            # Оценка стоимости
            estimated_value = available * 0.5  # Примерная оценка
            
            if estimated_value < MIN_ORDER_VALUE:
                self._log(
                    f"Skip dust: {outcome.upper()} ~${estimated_value:.2f}",
                    "WARNING"
                )
                continue
            
            try:
                self._log(f"Selling {available:.4f} {outcome.upper()} [market: {market_id[:12]}...]")
                
                await self.market_sell_position(market_id, outcome, available)
                sold += 1
                
                await async_sleep((1, 2))
                
            except Exception as e:
                self._log(f"Sell failed: {e}", "ERROR")
                failed += 1
        
        if sold > 0:
            self._log(f"=== MARKET SELL SUMMARY: Sold {sold} positions ===")
        
        return {"sold": sold, "failed": failed}
    
    async def stop_and_merge(self) -> Dict:
        """
        Полная остановка: отмена ордеров + merge позиций
        """
        self._log("Cancelling all orders...")
        cancelled = await self.cancel_all_orders()
        
        if cancelled > 0:
            self._log(f"Cancelled {cancelled} orders, waiting...")
            await async_sleep((3, 5))
        
        self._log("Merging all positions...")
        merge_result = await self.merge_all_positions()
        
        return {
            "cancelled": cancelled,
            "merged": merge_result["merged"],
            "failed": merge_result["failed"]
        }
    
    async def stop_and_sell(self) -> Dict:
        """
        Полная остановка: отмена ордеров + market sell
        """
        self._log("Cancelling all orders...")
        cancelled = await self.cancel_all_orders()
        
        if cancelled > 0:
            self._log(f"Cancelled {cancelled} orders, waiting...")
            await async_sleep((3, 5))
        
        self._log("Selling all positions at market...")
        sell_result = await self.market_sell_all_positions()
        
        return {
            "cancelled": cancelled,
            "sold": sell_result["sold"],
            "failed": sell_result["failed"]
        }
    
    async def check_dust_positions(self) -> List[Dict]:
        """
        Проверить наличие dust позиций (маленьких балансов).
        
        Returns:
            Список dust позиций
        """
        positions = await self.get_all_positions()
        dust = []
        
        MIN_VALUE = MIN_ORDER_VALUE_USD
        
        for pos in positions:
            if pos.balance <= 0.01:
                continue
            
            estimated_value = pos.balance * 0.5
            
            if estimated_value < MIN_VALUE:
                dust.append({
                    "market_id": pos.market_id,
                    "outcome": "yes" if pos.is_yes else "no",
                    "balance": pos.balance,
                    "value": estimated_value
                })
        
        return dust
