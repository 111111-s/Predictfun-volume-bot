"""
Market Maker for Predict.fun

Стратегия:
1. Покупаем YES + NO на нескольких маркетах
2. Ждем исполнения (наливки)
3. Когда налило - выставляем на продажу
4. Если налило обе стороны с разным количеством - мерджим максимум
5. Остаток продаем лимитками
"""

import asyncio
from typing import Optional, List, Dict, Tuple
from random import uniform, sample, randint, shuffle
from loguru import logger

from models import (
    MarketEvent, OrderBook, BuyPosition, OrderSide, Order
)
from config import config, MarketCondition
from .order_manager import OrderManager, OrderCreationError
from modules.browser import AuthTokenExpiredError
from services import telegram
from services.entry_prices import entry_prices
from utils.helpers import format_cents, async_sleep


class MarketNotActiveError(Exception):
    """Market is not active"""
    pass


class InsufficientBalanceError(Exception):
    """Insufficient balance"""
    pass


class MarketMaker:
    """
    Predict.fun Market Maker
    
    Логика:
    1. Выбираем маркеты из конфига
    2. На каждом маркете покупаем YES и NO
    3. Мониторим исполнение покупок
    4. Когда купили - выставляем на продажу
    5. Мониторим продажи
    6. Если обе стороны проданы частично - мерджим
    """
    
    def __init__(self, browser, label: str):
        self._browser = browser
        self._label = label
        self._order_manager = OrderManager(browser)
        
        # Активные позиции
        self._positions: Dict[str, BuyPosition] = {}
    
    def _log(self, message: str, level: str = "INFO", prefix: str = ""):
        """Log with label"""
        prefix_str = f"[{prefix}] " if prefix else ""
        full_msg = f"[{self._label}] {prefix_str}{message}"
        
        if level == "SUCCESS":
            logger.success(full_msg)
        elif level == "ERROR":
            logger.error(full_msg)
        elif level == "WARNING":
            logger.warning(full_msg)
        elif level == "DEBUG":
            logger.debug(full_msg)
        else:
            logger.info(full_msg)
    
    async def run(self) -> bool:
        """
        Запустить бота.
        
        Returns:
            True если успешно
        """
        try:
            # Логин
            self._log("Authenticating...", "INFO", "~")
            if not await self._browser.authenticate():
                self._log("Authentication failed!", "ERROR", "X")
                return False
            
            self._log(f"Logged in: {self._browser.address[:12]}...", "SUCCESS", "+")
            
            # Проверяем балансы
            usdt_balance = await self._browser.get_usdt_balance()
            bnb_balance, bnb_usd = await self._browser.get_bnb_balance_usd()
            self._log(f"USDT: ${usdt_balance:.2f} | BNB: {bnb_balance:.4f} (${bnb_usd:.2f})", "INFO", "$")
            
            # Алерты о низком балансе
            if bnb_balance < config.alerts.min_bnb:
                await telegram.send_alert(
                    label=f"LOW BNB - {self._label}",
                    message=f"BNB balance: {bnb_balance:.4f} (min: {config.alerts.min_bnb})\nTop up for gas fees!"
                )
                self._log(f"⚠️ Low BNB: {bnb_balance:.4f}", "WARNING", "!")
            
            if usdt_balance < config.alerts.min_usdt:
                await telegram.send_alert(
                    label=f"LOW USDT - {self._label}",
                    message=f"USDT balance: ${usdt_balance:.2f} (min: ${config.alerts.min_usdt})\nNot enough for trading!"
                )
                self._log(f"⚠️ Low USDT: ${usdt_balance:.2f}", "WARNING", "!")
                return False
            
            # Выбираем маркеты
            markets = await self._select_markets()
            if not markets:
                self._log("No markets to trade!", "WARNING", "!")
                return False
            
            self._log(f"Selected {len(markets)} markets", "INFO", "*")
            
            # Запускаем циклы
            cycles = randint(*config.markets.cycles)
            self._log(f"Running {cycles} cycles", "INFO", "~")
            
            for cycle in range(1, cycles + 1):
                self._log(f"=== Cycle {cycle}/{cycles} ===", "INFO", "#")
                
                try:
                    success = await self._run_cycle(markets)
                    if not success:
                        self._log(f"Cycle {cycle} failed", "WARNING", "!")
                except AuthTokenExpiredError:
                    self._log("Token expired - re-logging in...", "WARNING", "!")
                    try:
                        await self._browser.relogin()
                        self._log("Re-login successful, retrying cycle...", "SUCCESS", "+")
                        # Retry the cycle
                        success = await self._run_cycle(markets)
                    except Exception as login_err:
                        self._log(f"Re-login failed: {login_err}", "ERROR", "X")
                except Exception as e:
                    self._log(f"Cycle {cycle} error: {e}", "ERROR", "X")
                
                if cycle < cycles:
                    await async_sleep(config.sleep.between_cycles)
            
            self._log("All cycles completed!", "SUCCESS", "+")
            return True
            
        except Exception as e:
            self._log(f"Run failed: {e}", "ERROR", "X")
            return False
    
    async def _select_markets(self) -> List[MarketCondition]:
        """Выбрать маркеты для торговли"""
        all_markets = config.markets.list
        
        if not all_markets:
            return []
        
        # Количество маркетов
        min_m, max_m = config.markets.markets_per_account
        count = randint(min_m, min(max_m, len(all_markets)))
        
        # Случайный выбор
        selected = sample(all_markets, count)
        
        return selected
    
    async def _run_cycle(self, markets: List[MarketCondition]) -> bool:
        """
        Один цикл торговли.
        
        1. Split USDT → YES + NO на всех маркетах
        2. Выставляем на продажу
        3. Мониторим продажи
        4. Если обе стороны проданы частично - мерджим
        """
        # Шаг 1: Покупаем YES и NO через ордера
        self._log("Phase 1: Buying YES + NO tokens...", "INFO", ">")
        
        split_amount = int(uniform(*config.markets.split_amount))  # Сумма для split (целое число)
        
        for i, market in enumerate(markets, 1):
            try:
                await self._split_on_market(market, split_amount, f"M{i}")
            except Exception as e:
                self._log(f"[M{i}] Split failed: {e}", "ERROR", "X")
            
            await async_sleep(config.sleep.between_orders)
        
        # Шаг 2: Чекаємо появу токенів і виставляємо на продаж
        self._log("Phase 2: Waiting for tokens & placing sells...", "INFO", "~")
        await async_sleep(config.sleep.after_split)  # Чекаємо синхронізацію
        await self._place_sell_orders()
        
        # Уведомление что все маркеты активны
        active_markets = [p.market_name[:30] for p in self._positions.values() if not p.is_complete]
        total_volume = sum(p.yes_bought + p.no_bought for p in self._positions.values()) * 0.50
        if active_markets:
            await telegram.send_markets_watching(
                label=self._label,
                markets=active_markets,
                total_volume=total_volume
            )
        
        # Шаг 3: Моніторимо продажі (без автоматичного merge!)
        self._log("Phase 3: Monitoring sells...", "INFO", "~")
        await self._monitor_sells()
        
        return True
    
    async def _split_on_market(
        self,
        market: MarketCondition,
        amount: float,
        label: str
    ):
        """
        Get or split tokens on market.
        
        First checks if we already have YES/NO tokens.
        If yes - use existing tokens.
        If no - split USDT into YES + NO via SDK.
        """
        market_slug = market.market_id  # Это может быть slug из URL
        choice_index = int(market.choice) if market.choice and market.choice.isdigit() else 0
        
        # Получаем инфо о маркете (конвертирует slug в числовой ID)
        event = await self._browser.get_market_event(market_slug, choice_index)
        if not event:
            raise MarketNotActiveError(f"Market not found: {market_slug}")
        
        self._log(f"[{label}] Market: {event.name[:40]}...", "INFO", "~")
        
        # Проверяем существующие позиции
        existing = await self._browser.get_all_position_balances(str(event.market_id))
        yes_existing = existing.get("yes", 0)
        no_existing = existing.get("no", 0)
        
        if yes_existing > 0.5 or no_existing > 0.5:
            # Уже есть токены - используем их
            self._log(
                f"[{label}] Found existing: {yes_existing:.2f} YES + {no_existing:.2f} NO",
                "INFO", "*"
            )
            shares_yes = yes_existing
            shares_no = no_existing
        else:
            # Нет токенов - делаем split
            self._log(f"[{label}] Splitting ${amount:.2f} USDT -> YES + NO...", "INFO", ">")
            
            success = await self._browser.split_position_simple(
                market_id=str(event.market_id),
                amount=amount
            )
            
            if not success:
                self._log(f"[{label}] Split failed!", "ERROR", "X")
                raise Exception("Split failed")
            
            shares_yes = amount
            shares_no = amount
            
            self._log(
                f"[{label}] Split successful: {shares_yes:.2f} YES + {shares_no:.2f} NO",
                "SUCCESS", "+"
            )
        
        # Создаем позицию для отслеживания
        logger.debug(f"Token IDs: YES={event.yes_token_id[:20]}... NO={event.no_token_id[:20]}...")
        position = BuyPosition(
            market_id=str(event.market_id),
            market_name=event.name,
            condition_id=event.condition_id,
            yes_token_id=event.yes_token_id,
            no_token_id=event.no_token_id,
            yes_bought=shares_yes,
            no_bought=shares_no,
            yes_buy_price=0.50,
            no_buy_price=0.50,
        )
        
        # Проверяем существующие активные ордера на продажу
        try:
            all_orders = await self._browser.get_orders()
            for order in all_orders:
                # Filter by market and open status
                if str(order.market_id) != str(event.market_id):
                    continue
                if order.status.lower() not in ("open", "partial", "pending"):
                    continue
                if order.side.lower() != "sell":
                    continue
                    
                if order.outcome.lower() == "yes":
                    position.yes_sell_order_id = order.order_id
                    position.yes_sell_price = order.price
                    self._log(f"[{label}] Found existing YES sell order", "INFO", "*")
                elif order.outcome.lower() == "no":
                    position.no_sell_order_id = order.order_id
                    position.no_sell_price = order.price
                    self._log(f"[{label}] Found existing NO sell order", "INFO", "*")
        except Exception as e:
            self._log(f"[{label}] Could not check existing orders: {e}", "WARNING", "!")
        
        self._positions[str(event.market_id)] = position
    
    async def _verify_split_tokens(self):
        """Проверить что токены появились после split"""
        # Split on-chain, токени повинні бути одразу
        # Просто чекаємо синхронізацію з API
        await async_sleep(config.sleep.after_buy)
        
        for market_id, position in self._positions.items():
            try:
                # Перевіряємо баланси через API
                balances = await self._browser.get_all_position_balances(market_id)
                
                if balances.get("yes", 0) > 0:
                    position.yes_bought = balances["yes"]
                if balances.get("no", 0) > 0:
                    position.no_bought = balances["no"]
                
                self._log(
                    f"Tokens verified: YES={position.yes_bought:.2f}, NO={position.no_bought:.2f} "
                    f"[{position.market_name[:20]}]",
                    "INFO", "~"
                )
            except Exception as e:
                self._log(f"Token verification failed: {e}", "WARNING", "!")
    
    async def _place_sell_orders(self):
        """Выставить ордера на продажу"""
        for market_id, position in self._positions.items():
            # Проверяем активные ордера на этом маркете
            try:
                all_orders = await self._browser.get_orders()
                self._log(f"Found {len(all_orders)} orders total", "DEBUG", "~")
                
                # Считаем все SELL ордера на этом маркете
                total_selling = 0.0
                yes_selling = 0.0
                no_selling = 0.0
                
                for order in all_orders:
                    if str(order.market_id) != str(market_id):
                        continue
                    if order.status.lower() not in ("open", "partial", "pending"):
                        continue
                    if order.side.lower() != "sell":
                        continue
                    
                    remaining = order.size - order.filled
                    
                    # Определяем outcome по token_id, если outcome пустой
                    outcome = order.outcome.lower() if order.outcome else ""
                    if not outcome and order.token_id:
                        if order.token_id == position.yes_token_id:
                            outcome = "yes"
                        elif order.token_id == position.no_token_id:
                            outcome = "no"
                    
                    self._log(f"Order: market={order.market_id} side={order.side} outcome={outcome} token={order.token_id[:16]}... status={order.status} size={order.size:.2f} price={order.price:.4f}", "DEBUG", "~")
                    
                    total_selling += remaining
                    
                    # Считаем отдельно YES и NO
                    if outcome == "yes":
                        yes_selling += remaining
                        if not position.yes_sell_order_id:
                            position.yes_sell_order_id = order.order_id
                            position.yes_sell_order_hash = order.order_hash
                            position.yes_sell_price = order.price
                            position.yes_sell_order = order  # Сохраняем весь объект для raw_data
                            self._log(f"Found existing YES sell @ {order.price:.4f}", "INFO", "*")
                    elif outcome == "no":
                        no_selling += remaining
                        if not position.no_sell_order_id:
                            position.no_sell_order_id = order.order_id
                            position.no_sell_order_hash = order.order_hash
                            position.no_sell_price = order.price
                            position.no_sell_order = order  # Сохраняем весь объект для raw_data
                            self._log(f"Found existing NO sell @ {order.price:.4f}", "INFO", "*")
                
                if total_selling > 0:
                    self._log(f"Already selling: YES={yes_selling:.2f}, NO={no_selling:.2f}, Total={total_selling:.2f}", "INFO", "*")
            except Exception as e:
                self._log(f"Could not check orders: {e}", "WARNING", "!")
                yes_selling = 0
                no_selling = 0
            
            # Вычисляем сколько ещё нужно продать каждого типа
            yes_to_sell = max(0, position.yes_bought - yes_selling)
            no_to_sell = max(0, position.no_bought - no_selling)
            
            min_sell = config.limits.min_sell_amount
            
            # Если уже всё выставлено или меньше минимума - пропускаем
            if yes_to_sell < min_sell and no_to_sell < min_sell:
                if yes_to_sell > 0 or no_to_sell > 0:
                    self._log(f"Shares below min_sell_amount ({min_sell}): YES={yes_to_sell:.2f} NO={no_to_sell:.2f}", "INFO", "~")
                else:
                    self._log(f"All shares already listed for sale", "INFO", "*")
                continue
            
            if yes_to_sell >= min_sell and not position.yes_sell_order_id:
                try:
                    result = await self._order_manager.create_sell_order(
                        market_id=market_id,
                        outcome="yes",
                        shares=yes_to_sell
                    )
                    if result:
                        position.yes_sell_order_id = result["order_id"]
                        position.yes_sell_price = result["price"]
                        self._log(
                            f"SELL YES @ {format_cents(result['price'])} [{position.market_name[:20]}]",
                            "SUCCESS", "+"
                        )
                except Exception as e:
                    self._log(f"Sell YES failed: {e}", "ERROR", "X")
            elif position.yes_sell_order_id:
                self._log(f"YES sell already placed", "INFO", "*")
            
            if no_to_sell >= min_sell and not position.no_sell_order_id:
                try:
                    result = await self._order_manager.create_sell_order(
                        market_id=market_id,
                        outcome="no",
                        shares=no_to_sell
                    )
                    if result:
                        position.no_sell_order_id = result["order_id"]
                        position.no_sell_price = result["price"]
                        self._log(
                            f"SELL NO @ {format_cents(result['price'])} [{position.market_name[:20]}]",
                            "SUCCESS", "+"
                        )
                except Exception as e:
                    self._log(f"Sell NO failed: {e}", "ERROR", "X")
            elif position.no_sell_order_id:
                self._log(f"NO sell already placed", "INFO", "*")
            
            await async_sleep(config.sleep.small_pause)
    
    async def _calculate_best_sell_price(self, market_id: str, outcome: str) -> float:
        """
        Вычислить лучшую цену для продажи из стакана.
        
        API возвращает один стакан для маркета (YES):
        - bids = покупатели YES
        - asks = продавцы YES
        
        Для YES: продаём в asks
        Для NO: NO price = 1 - YES price
        
        price_step > 0: становимся БЛИЖЕ к bid (агрессивно)
        price_step < 0: становимся ДАЛЬШЕ от bid (пассивно)
        """
        try:
            # Получаем стакан маркета (это стакан YES)
            book = await self._browser.get_market_orderbook(market_id, "yes")
            
            sell_step = config.limits.sell_price_step / 100  # центы -> доллары
            min_spread = config.limits.min_spread / 100
            
            # Проверка пустого стакана
            if not book.bids and not book.asks:
                logger.debug(f"Empty orderbook for {outcome}")
                return 0.0
            
            if outcome.lower() == "yes":
                # Для YES смотрим на asks (продавцы YES)
                best_ask = book.best_ask
                best_bid = book.best_bid
                
                # Целевая цена: 
                # sell_step > 0: ниже ask (ближе к bid)
                # sell_step < 0: выше ask (дальше от bid)
                target_price = best_ask - sell_step
                
                # Защита от самокупки (только для агрессивного режима)
                if sell_step > 0:
                    floor_price = best_bid + min_spread
                    price = max(target_price, floor_price)
                else:
                    # Пассивный режим - просто ставим дальше
                    price = target_price
                
                logger.debug(
                    f"YES orderbook: bid={best_bid:.4f} ask={best_ask:.4f} "
                    f"-> target={price:.4f}"
                )
            else:
                # Для NO инвертируем цены YES
                # NO ask = 1 - YES bid
                # NO bid = 1 - YES ask
                yes_best_bid = book.best_bid  # лучший покупатель YES
                yes_best_ask = book.best_ask  # лучший продавец YES
                
                no_best_ask = 1 - yes_best_bid  # продаём NO = кто-то покупает YES
                no_best_bid = 1 - yes_best_ask  # покупаем NO = кто-то продаёт YES
                
                # Целевая цена для продажи NO
                target_price = no_best_ask - sell_step
                
                # Защита от самокупки (только для агрессивного режима)
                if sell_step > 0:
                    floor_price = no_best_bid + min_spread
                    price = max(target_price, floor_price)
                else:
                    # Пассивный режим
                    price = target_price
                
                logger.debug(
                    f"NO orderbook (inverted): bid={no_best_bid:.4f} ask={no_best_ask:.4f} "
                    f"-> target={price:.4f}"
                )
            
            # Ограничения
            price = max(0.01, min(0.99, price))
            
            return round(price, 4)
            
        except Exception as e:
            logger.debug(f"Error getting orderbook: {e}")
            return 0.0
    
    async def _get_second_best_ask(self, market_id: str, outcome: str, our_price: float) -> float:
        """
        Получить второй лучший ask (следующий после нашего ордера).
        
        Используется для определения gap - можно ли подняться вверх.
        """
        try:
            book = await self._browser.get_market_orderbook(market_id, "yes")
            
            if not book.asks:
                return 0.0
            
            if outcome.lower() == "yes":
                # Для YES - прямой стакан
                asks = sorted(book.asks)  # [(price, amount), ...]
                
                # Ищем ask выше нашей цены
                for price, amount in asks:
                    if price > our_price + 0.0001:  # Небольшой допуск
                        return price
                return 0.0
            else:
                # Для NO - инвертированный стакан
                # NO asks = 1 - YES bids
                if not book.bids:
                    return 0.0
                
                # YES bids отсортированы по убыванию, берём их как NO asks
                yes_bids = sorted(book.bids, reverse=True)
                
                for yes_bid_price, amount in yes_bids:
                    no_ask_price = 1 - yes_bid_price
                    if no_ask_price > our_price + 0.0001:
                        return no_ask_price
                return 0.0
                
        except Exception as e:
            logger.debug(f"Error getting second best ask: {e}")
            return 0.0
    
    async def _try_replace_order(
        self, 
        position: BuyPosition, 
        outcome: str, 
        current_order: Order
    ) -> bool:
        """
        Попытаться перевыставить ордер по лучшей цене.
        
        Returns:
            True если перевыставили, False если не нужно
        """
        if not config.limits.repost_if_not_best:
            logger.debug("Replace disabled in config")
            return False
        
        market_id = position.market_id
        sell_step = config.limits.sell_price_step / 100
        
        # Для пассивного режима (отрицательный sell_step) replace не нужен
        # Мы специально ставим дальше и не хотим перебивать
        # При sell_step = 0 тоже не перебиваем (ставим ровно по best_ask)
        if sell_step <= 0:
            logger.debug(f"Passive mode (sell_step={sell_step}), no replace needed")
            return False
        
        # Текущая цена ордера
        current_price = current_order.price if current_order.price > 0 else (
            position.yes_sell_price if outcome == "yes" else position.no_sell_price
        )
        
        logger.debug(
            f"Replace check {outcome.upper()}: current_price={current_price:.4f} "
            f"order.price={current_order.price:.4f}"
        )
        
        # Вычисляем лучшую цену
        best_price = await self._calculate_best_sell_price(market_id, outcome)
        
        logger.debug(f"Best price for {outcome}: {best_price:.4f}")
        
        if best_price <= 0:
            logger.debug("Best price is 0, skipping replace")
            return False
        
        # Проверяем нужно ли перевыставлять
        price_diff = current_price - best_price
        
        logger.debug(f"Price diff: {price_diff:.4f}, price_step: {price_step:.4f}")
        
        # Случай 1: Можем поставить НИЖЕ (кто-то встал лучше нас)
        if price_diff >= price_step:
            logger.debug("Can improve price DOWN, replacing")
            # best_price уже правильный, продолжаем replace
        
        # Случай 2: Проверяем gap для движения ВВЕРХ
        elif config.limits.repost_up_if_gap:
            # Получаем второй лучший ask (следующий после best)
            second_best = await self._get_second_best_ask(market_id, outcome, current_price)
            
            if second_best and second_best > 0:
                # Gap = разница между вторым лучшим и нашей ценой
                gap = second_best - current_price
                
                logger.debug(f"Gap check: our={current_price:.4f} second_best={second_best:.4f} gap={gap:.4f}")
                
                if gap >= price_step * 2:
                    # Есть gap - поднимаемся на step, оставаясь первыми
                    best_price = current_price + price_step
                    logger.debug(f"Gap detected, moving UP to {best_price:.4f}")
                else:
                    logger.debug("No significant gap, no replace needed")
                    return False
            else:
                logger.debug("Could not get second best ask")
                return False
        else:
            # Наша цена и так хорошая
            logger.debug("Price is already good, no replace needed")
            return False
        
        self._log(
            f"Replace {outcome.upper()}: {format_cents(current_price)} -> {format_cents(best_price)} "
            f"[{position.market_name[:20]}]",
            "INFO", "~"
        )
        
        try:
            # Отменяем старый ордер - нужны полные данные для SDK
            # Берём order объект из position
            order_obj = position.yes_sell_order if outcome == "yes" else position.no_sell_order
            
            if not order_obj or not order_obj.raw_data:
                logger.warning(f"No raw_data for {outcome} order, cannot cancel")
                return False
            
            # Get market flags for cancel options
            is_yield_bearing = order_obj.raw_data.get("isYieldBearing", True)
            is_neg_risk = order_obj.raw_data.get("isNegRisk", False)
            
            cancelled = await self._browser.cancel_order_by_data(
                order_obj.raw_data,
                is_yield_bearing=is_yield_bearing,
                is_neg_risk=is_neg_risk
            )
            if not cancelled:
                order_hash = order_obj.order_hash or "unknown"
                logger.warning(f"Failed to cancel order {order_hash[:16]}...")
                return False
            
            await async_sleep(config.sleep.small_pause)
            
            # Определяем количество для перевыставления
            remaining = current_order.size - current_order.filled
            if remaining <= 0:
                return False
            
            # Создаём новый ордер
            new_order = await self._order_manager.create_sell_order(
                market_id=market_id,
                outcome=outcome,
                shares=remaining,
                min_price=0.01
            )
            
            if new_order:
                # Обновляем позицию
                if outcome == "yes":
                    position.yes_sell_order_id = new_order["order_id"]
                    position.yes_sell_price = new_order["price"]
                else:
                    position.no_sell_order_id = new_order["order_id"]
                    position.no_sell_price = new_order["price"]
                
                self._log(
                    f"Replaced {outcome.upper()} @ {format_cents(new_order['price'])}",
                    "SUCCESS", "+"
                )
                
                await async_sleep(config.sleep.after_repost)
                return True
            
        except Exception as e:
            self._log(f"Replace failed: {e}", "WARNING", "!")
        
        return False
    
    async def _check_stop_loss(self, position: BuyPosition) -> bool:
        """
        Проверить и исполнить стоп-лосс для позиции.
        
        Для split бота цена входа = 50 центов (0.50).
        Если рыночная цена упала на stop_loss.percent%, продаём по рынку.
        
        Returns:
            True если стоп-лосс сработал
        """
        if not config.stop_loss.enabled:
            return False
        
        # Если позиция уже закрыта - пропускаем
        if position.is_complete:
            return False
        
        try:
            # Получаем текущие цены из стакана
            book = await self._browser.get_market_orderbook(position.market_id, "yes")
            
            if not book.bids and not book.asks:
                return False
            
            # Цена входа для split = 50 центов
            entry_price = 0.50
            sl_percent = config.stop_loss.percent
            sl_threshold = entry_price * (1 - sl_percent / 100)
            
            # Проверяем YES (если не продан)
            if position.yes_sell_order_id and position.yes_sold == 0:
                yes_market_price = book.best_bid  # Цена по которой можем продать
                
                if yes_market_price < sl_threshold:
                    # Стоп-лосс сработал для YES
                    drop_percent = (entry_price - yes_market_price) / entry_price * 100
                    
                    self._log(
                        f"🛑 STOP-LOSS YES: {format_cents(entry_price)} -> {format_cents(yes_market_price)} "
                        f"(-{drop_percent:.1f}%) [{position.market_name[:20]}]",
                        "WARNING", "!"
                    )
                    
                    # Отменяем ордер
                    if position.yes_sell_order_id:
                        try:
                            await self._order_manager.cancel_order(position.yes_sell_order_id)
                        except:
                            pass
                    
                    # Продаём по рынку
                    amount = position.yes_bought - position.merged
                    if amount > 0.01:
                        await self._browser.create_order(
                            market_id=position.market_id,
                            outcome="yes",
                            side="sell",
                            price=0.01,  # Market sell
                            size=amount
                        )
                    
                    position.yes_sold = position.yes_bought
                    position.stopped_out = True
                    
                    # Telegram уведомление
                    await telegram.send_stop_loss(
                        label=self._label,
                        market=position.market_name[:50],
                        side="YES",
                        entry_price=entry_price * 100,
                        current_price=yes_market_price * 100,
                        loss_percent=drop_percent,
                        amount=amount
                    )
                    
                    return True
            
            # Проверяем NO (если не продан)
            if position.no_sell_order_id and position.no_sold == 0:
                # NO цена = 1 - YES ask
                no_market_price = 1 - book.best_ask
                
                if no_market_price < sl_threshold:
                    # Стоп-лосс сработал для NO
                    drop_percent = (entry_price - no_market_price) / entry_price * 100
                    
                    self._log(
                        f"🛑 STOP-LOSS NO: {format_cents(entry_price)} -> {format_cents(no_market_price)} "
                        f"(-{drop_percent:.1f}%) [{position.market_name[:20]}]",
                        "WARNING", "!"
                    )
                    
                    # Отменяем ордер
                    if position.no_sell_order_id:
                        try:
                            await self._order_manager.cancel_order(position.no_sell_order_id)
                        except:
                            pass
                    
                    # Продаём по рынку
                    amount = position.no_bought - position.merged
                    if amount > 0.01:
                        await self._browser.create_order(
                            market_id=position.market_id,
                            outcome="no",
                            side="sell",
                            price=0.01,  # Market sell
                            size=amount
                        )
                    
                    position.no_sold = position.no_bought
                    position.stopped_out = True
                    
                    # Telegram уведомление
                    await telegram.send_stop_loss(
                        label=self._label,
                        market=position.market_name[:50],
                        side="NO",
                        entry_price=entry_price * 100,
                        current_price=no_market_price * 100,
                        loss_percent=drop_percent,
                        amount=amount
                    )
                    
                    return True
            
        except Exception as e:
            logger.debug(f"Stop-loss check error: {e}")
        
        return False
    
    async def _monitor_sells(self):
        """
        Моніторинг продажів - чекаємо поки всі ордери виконаються.
        
        Логіка аналогічна Opinion боту:
        - Перевіряємо статус ордерів через API
        - Якщо ордер не знайдено або статус != OPEN → виконано
        """
        max_wait = 3600  # 1 година максимум
        check_interval = config.limits.check_interval
        elapsed = 0
        check_count = 0
        
        while elapsed < max_wait:
            all_done = True
            check_count += 1
            
            for market_id, position in list(self._positions.items()):
                # Пропускаємо завершені
                if position.is_complete:
                    continue
                
                all_done = False
                
                # Проверяем стоп-лосс
                sl_triggered = await self._check_stop_loss(position)
                if sl_triggered:
                    continue  # Позиция обработана стоп-лоссом
                
                try:
                    # Отримуємо всі ордери і перевіряємо статус наших
                    all_orders = await self._browser.get_orders()
                    
                    # Шукаємо наші ордери
                    yes_order = None
                    no_order = None
                    
                    for order in all_orders:
                        if order.order_id == position.yes_sell_order_id:
                            yes_order = order
                            position.yes_sell_order = order  # Сохраняем для raw_data
                        elif order.order_id == position.no_sell_order_id:
                            no_order = order
                            position.no_sell_order = order  # Сохраняем для raw_data
                    
                    # Перевіряємо YES sell
                    if position.yes_sell_order_id and position.yes_sold == 0:
                        logger.debug(f"Checking YES order: id={position.yes_sell_order_id[:16]}...")
                        # Якщо ордер не знайдено АБО статус не OPEN → виконано
                        if yes_order is None or yes_order.status.lower() not in ("open", "partial", "pending"):
                            position.yes_sold = position.yes_bought
                            revenue = position.yes_sold * position.yes_sell_price
                            self._log(
                                f"YES SOLD: {position.yes_sold:.2f} @ {format_cents(position.yes_sell_price)} "
                                f"= ${revenue:.2f} [{position.market_name[:20]}]",
                                "SUCCESS", "$"
                            )
                            # Telegram уведомление
                            remaining = "NO" if position.no_sell_order_id and position.no_sold == 0 else ""
                            await telegram.send_order_filled(
                                label=self._label,
                                market=position.market_name[:50],
                                side="YES",
                                price=position.yes_sell_price * 100,
                                amount=position.yes_sold,
                                revenue=revenue,
                                remaining_side=remaining
                            )
                        else:
                            # Ордер ще відкритий - спробуємо reprice
                            logger.debug(f"YES order still open, price={yes_order.price:.4f}")
                            if yes_order and yes_order.filled > 0:
                                self._log(
                                    f"YES partial: {yes_order.filled:.2f}/{yes_order.size:.2f}",
                                    "DEBUG", "~"
                                )
                            
                            # Перевіряємо чи можна перевиставити по кращій ціні
                            if yes_order:
                                logger.debug(f"Trying replace for YES order")
                                await self._try_replace_order(position, "yes", yes_order)
                    
                    # Перевіряємо NO sell
                    if position.no_sell_order_id and position.no_sold == 0:
                        logger.debug(f"Checking NO order: id={position.no_sell_order_id[:16]}...")
                        if no_order is None or no_order.status.lower() not in ("open", "partial", "pending"):
                            position.no_sold = position.no_bought
                            revenue = position.no_sold * position.no_sell_price
                            self._log(
                                f"NO SOLD: {position.no_sold:.2f} @ {format_cents(position.no_sell_price)} "
                                f"= ${revenue:.2f} [{position.market_name[:20]}]",
                                "SUCCESS", "$"
                            )
                            # Telegram уведомление
                            remaining = "YES" if position.yes_sell_order_id and position.yes_sold == 0 else ""
                            await telegram.send_order_filled(
                                label=self._label,
                                market=position.market_name[:50],
                                side="NO",
                                price=position.no_sell_price * 100,
                                amount=position.no_sold,
                                revenue=revenue,
                                remaining_side=remaining
                            )
                        else:
                            # Ордер ще відкритий - спробуємо reprice
                            logger.debug(f"NO order still open, price={no_order.price:.4f}")
                            if no_order and no_order.filled > 0:
                                self._log(
                                    f"NO partial: {no_order.filled:.2f}/{no_order.size:.2f}",
                                    "DEBUG", "~"
                                )
                            
                            # Перевіряємо чи можна перевиставити по кращій ціні
                            if no_order:
                                logger.debug(f"Trying replace for NO order")
                                await self._try_replace_order(position, "no", no_order)
                    
                except Exception as e:
                    self._log(f"Monitor error: {e}", "WARNING", "!")
                
                # Проверяем закрытие позиции (обе стороны проданы)
                if position.is_complete and not position.stopped_out:
                    total_volume = position.total_sold_value
                    await telegram.send_position_closed(
                        label=self._label,
                        market=position.market_name[:50],
                        volume=total_volume,
                        yes_price=position.yes_sell_price * 100,
                        no_price=position.no_sell_price * 100
                    )
                
                # Перевіряємо dust (тільки відміна, без merge)
                await self._check_dust(position)
            
            if all_done:
                self._log("All positions sold!", "SUCCESS", "+")
                break
            
            await asyncio.sleep(check_interval)
            elapsed += check_interval
            
            # Статус кожні 60 сек
            if check_count % 10 == 0:
                active = sum(1 for p in self._positions.values() if not p.is_complete)
                if active > 0:
                    self._log(f"Still monitoring {active} positions...", "INFO", "~")
    
    async def _try_merge_dust(self, position: BuyPosition):
        """
        Merge dust позицій - тільки для cleanup!
        
        Викликається тільки коли:
        1. Є залишки і YES і NO
        2. Обидва менше dust_threshold
        3. Не можна продати (немає ліквідності)
        """
        try:
            import math
            
            yes_remaining = position.yes_remaining
            no_remaining = position.no_remaining
            
            # Потрібні обидві сторони
            if yes_remaining <= 0 or no_remaining <= 0:
                return
            
            merge_amount = min(yes_remaining, no_remaining)
            merge_amount = math.floor(merge_amount * 0.999 * 10000) / 10000
            
            if merge_amount < 0.5:
                return
            
            self._log(
                f"Merging dust {merge_amount:.4f} tokens [{position.market_name[:20]}]",
                "INFO", "~"
            )
            
            success = await self._browser.merge_positions(
                condition_id=position.condition_id,
                amount=merge_amount
            )
            
            if success:
                position.merged += merge_amount
                self._log(
                    f"Dust merged: {merge_amount:.4f} → ${merge_amount:.2f} [{position.market_name[:20]}]",
                    "SUCCESS", "+"
                )
                        
        except Exception as e:
            self._log(f"Dust merge failed: {e}", "WARNING", "!")
    
    async def _check_dust(self, position: BuyPosition):
        """
        Перевірити dust позиції.
        
        Dust = кількість токенів менше min_sell_amount.
        Якщо є обидва - merge, інакше просто ігноруємо.
        """
        import math
        min_sell = config.limits.min_sell_amount
        
        yes_is_dust = 0 < position.yes_remaining < min_sell
        no_is_dust = 0 < position.no_remaining < min_sell
        
        # Якщо обидва dust - спробуємо merge
        if yes_is_dust and no_is_dust:
            merge_amount = min(position.yes_remaining, position.no_remaining)
            merge_amount = math.floor(merge_amount * 0.999 * 10000) / 10000
            
            if merge_amount > 0.001:
                self._log(f"Both sides dust (<{min_sell}) - merging {merge_amount:.4f} [{position.market_name[:20]}]", "INFO", "~")
                try:
                    await self._browser.merge_positions(
                        condition_id=position.condition_id,
                        amount=merge_amount
                    )
                    position.merged += merge_amount
                    self._log(f"Dust merged: {merge_amount:.4f} [{position.market_name[:20]}]", "SUCCESS", "+")
                except Exception as e:
                    self._log(f"Dust merge failed: {e}", "WARNING", "!")
            
            # Позначаємо як завершено
            position.yes_sold = position.yes_bought - position.merged
            position.no_sold = position.no_bought - position.merged
            return
        
        # YES dust - відміняємо і ігноруємо
        if yes_is_dust:
            if position.yes_sell_order_id:
                try:
                    await self._order_manager.cancel_order(position.yes_sell_order_id)
                except:
                    pass
                position.yes_sell_order_id = ""
            position.yes_sold = position.yes_bought - position.merged
            self._log(f"YES dust {position.yes_remaining:.2f} tokens (<{min_sell}) - skipping [{position.market_name[:20]}]", "INFO", "~")
        
        # NO dust
        if no_is_dust:
            if position.no_sell_order_id:
                try:
                    await self._order_manager.cancel_order(position.no_sell_order_id)
                except:
                    pass
                position.no_sell_order_id = ""
            position.no_sold = position.no_bought - position.merged
            self._log(f"NO dust {position.no_remaining:.2f} tokens (<{min_sell}) - skipping [{position.market_name[:20]}]", "INFO", "~")
    
    async def monitor_existing_only(self) -> bool:
        """
        Режим моніторингу існуючих ордерів без створення нових.
        
        Використовується для відновлення роботи після перезапуску.
        """
        try:
            # Логін
            self._log("Authenticating...", "INFO", "~")
            if not await self._browser.authenticate():
                self._log("Authentication failed!", "ERROR", "X")
                return False
            
            self._log(f"Logged in: {self._browser.address[:12]}...", "SUCCESS", "+")
            
            # Отримуємо всі відкриті ордери
            orders = await self._browser.get_orders(status="open")
            
            if not orders:
                self._log("No open orders to monitor", "INFO", "*")
                return False
            
            self._log(f"Found {len(orders)} open orders", "INFO", "*")
            
            # Групуємо по маркетах
            markets_with_orders = set()
            for order in orders:
                markets_with_orders.add(order.market_id)
            
            self._log(f"Monitoring {len(markets_with_orders)} markets...", "INFO", "~")
            
            # Моніторимо до завершення
            max_wait = 3600 * 4  # 4 години максимум
            check_interval = config.limits.check_interval
            elapsed = 0
            
            while elapsed < max_wait:
                # Перевіряємо ордери
                current_orders = await self._browser.get_orders(status="open")
                
                if not current_orders:
                    self._log("All orders completed!", "SUCCESS", "+")
                    return True
                
                # Логуємо статус
                if elapsed % 60 == 0:
                    self._log(f"Still monitoring {len(current_orders)} orders...", "INFO", "~")
                
                await asyncio.sleep(check_interval)
                elapsed += check_interval
            
            self._log("Monitoring timeout reached", "WARNING", "!")
            return True
            
        except Exception as e:
            self._log(f"Monitor failed: {e}", "ERROR", "X")
            return False
