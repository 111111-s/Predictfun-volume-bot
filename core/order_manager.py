"""
Order Manager for Predict.fun

Управление созданием и отменой ордеров через API.
"""

from typing import Optional, Dict, Any
from decimal import Decimal
from loguru import logger

from models import OrderSide, MarketEvent, Order
from config import config


class OrderCreationError(Exception):
    """Ошибка создания ордера"""
    pass


class OrderManager:
    """Менеджер ордеров для Predict.fun API"""
    
    def __init__(self, browser):
        self._browser = browser
    
    async def create_buy_order(
        self,
        market_id: str,
        outcome: str,
        amount_usdt: float,
        max_price: float = 0.99
    ) -> Optional[Dict]:
        """
        Создать BUY ордер.
        
        Args:
            market_id: ID маркета
            outcome: "yes" или "no"
            amount_usdt: Сумма в USDT
            max_price: Макс. цена
            
        Returns:
            Order data dict или None
        """
        max_retries = config.limits.retry_on_fail
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                # Получаем стакан для лучшей цены
                book = await self._browser.get_market_orderbook(market_id, outcome)
                
                # Цена = лучший ask (или чуть выше)
                price = min(book.best_ask, max_price)
                
                # Количество шейров
                # Округляем вниз до 6 знаков
                shares = Decimal(str(amount_usdt)) / Decimal(str(price))
                shares = float(shares.quantize(Decimal('0.000001'), rounding='ROUND_DOWN'))
                
                if shares <= 0:
                    logger.warning(f"Invalid shares amount: {shares}")
                    return None
                
                logger.info(
                    f"BUY {outcome.upper()} @ {price:.4f} x {shares:.2f} = ${amount_usdt:.2f}"
                )
                
                # Создаем ордер через API
                order_hash = await self._browser.create_order(
                    market_id=market_id,
                    outcome=outcome,
                    side="buy",
                    price=price,
                    size=shares
                )
                
                if order_hash:
                    return {
                        "order_id": order_hash,
                        "price": price,
                        "shares": shares,
                        "outcome": outcome
                    }
                
            except Exception as e:
                last_error = e
                logger.warning(f"Buy order failed (attempt {attempt}/{max_retries}): {e}")
                
                if attempt < max_retries:
                    from asyncio import sleep
                    await sleep(2)
        
        if last_error:
            raise OrderCreationError(f"Failed to create buy order: {last_error}")
        return None
    
    async def create_sell_order(
        self,
        market_id: str,
        outcome: str,
        shares: float,
        min_price: float = 0.01
    ) -> Optional[Dict]:
        """
        Создать SELL лимитный ордер.
        
        Args:
            market_id: ID маркета
            outcome: "yes" или "no"
            shares: Количество шейров
            min_price: Мин. цена
            
        Returns:
            Order data dict или None
        """
        max_retries = config.limits.retry_on_fail
        last_error = None
        
        for attempt in range(1, max_retries + 1):
            try:
                # Получаем стакан маркета (это стакан YES)
                book = await self._browser.get_market_orderbook(market_id, "yes")
                
                # Проверяем что стакан не пустой
                if not book.bids and not book.asks:
                    logger.warning(f"Orderbook empty for {outcome}, using safe price")
                    price = 0.50
                else:
                    sell_step = config.limits.sell_price_step / 100  # центы -> доллары
                    min_spread = config.limits.min_spread / 100
                    
                    if outcome.lower() == "yes":
                        # Для YES смотрим на asks
                        best_ask = book.best_ask
                        best_bid = book.best_bid
                        
                        logger.debug(
                            f"YES orderbook: bid={best_bid:.4f} ask={best_ask:.4f}"
                        )
                        
                        target_price = best_ask - sell_step
                        floor_price = best_bid + min_spread
                        
                        if target_price <= floor_price:
                            price = best_ask
                        else:
                            price = target_price
                    else:
                        # Для NO инвертируем цены YES
                        yes_best_bid = book.best_bid
                        yes_best_ask = book.best_ask
                        
                        no_best_ask = 1 - yes_best_bid
                        no_best_bid = 1 - yes_best_ask
                        
                        logger.debug(
                            f"NO orderbook (inverted): bid={no_best_bid:.4f} ask={no_best_ask:.4f}"
                        )
                        
                        target_price = no_best_ask - sell_step
                        floor_price = no_best_bid + min_spread
                        
                        if target_price <= floor_price:
                            price = no_best_ask
                        else:
                            price = target_price
                
                # Минимальная цена
                price = max(price, min_price)
                price = min(price, 0.99)
                
                # Округляем шейры вниз
                shares_rounded = float(
                    Decimal(str(shares)).quantize(Decimal('0.000001'), rounding='ROUND_DOWN')
                )
                
                if shares_rounded <= 0:
                    logger.warning(f"Invalid shares: {shares_rounded}")
                    return None
                
                logger.info(
                    f"SELL {outcome.upper()} @ {price:.4f} x {shares_rounded:.2f}"
                )
                
                order_hash = await self._browser.create_order(
                    market_id=market_id,
                    outcome=outcome,
                    side="sell",
                    price=price,
                    size=shares_rounded
                )
                
                if order_hash:
                    return {
                        "order_id": order_hash,
                        "price": price,
                        "shares": shares_rounded,
                        "outcome": outcome
                    }
                
            except Exception as e:
                last_error = e
                logger.warning(f"Sell order failed (attempt {attempt}/{max_retries}): {e}")
                
                if attempt < max_retries:
                    from asyncio import sleep
                    await sleep(2)
        
        if last_error:
            raise OrderCreationError(f"Failed to create sell order: {last_error}")
        return None
    
    async def cancel_order(self, order_id: str) -> bool:
        """Отменить ордер"""
        try:
            return await self._browser.cancel_order(order_id)
        except Exception as e:
            logger.error(f"Cancel failed: {e}")
            return False
    
    async def check_order_filled(self, order_id: str) -> tuple:
        """
        Проверить исполнение ордера.
        
        Returns:
            (is_filled, filled_amount)
        """
        try:
            order = await self._browser.get_order(order_id)
            if order:
                return (order.is_filled, order.filled)
            return (False, 0)
        except Exception as e:
            logger.error(f"Check order failed: {e}")
            return (False, 0)
    
    async def get_open_orders(self, market_id: Optional[str] = None) -> list:
        """Получить открытые ордера"""
        return await self._browser.get_orders(market_id=market_id, status="open")
