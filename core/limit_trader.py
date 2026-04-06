"""
Limit Trading Strategy - Parallel market trading with limit orders
"""
import asyncio
from dataclasses import dataclass, field
from random import uniform, randint
from typing import List, Optional, Dict

from loguru import logger

from config.settings import config, MarketCondition
from modules.browser import PredictBrowser, AuthTokenExpiredError
from models.positions import Order, Position
from services import telegram
from services.entry_prices import entry_prices
from utils.helpers import format_cents


@dataclass
class MarketState:
    """State for a single market"""
    market_id: str
    market_name: str
    slug: str
    outcome: str  # 'yes' or 'no'
    token_id: str = ""
    
    # Position state
    has_position: bool = False
    position_size: float = 0.0
    
    # Order state
    buy_order_id: Optional[str] = None
    sell_order_id: Optional[str] = None
    buy_price: float = 0.0
    sell_price: float = 0.0


class LimitTrader:
    """
    Parallel limit trading strategy.
    Each market operates independently in a BUY -> SELL -> BUY cycle.
    """
    
    def __init__(self, browser: PredictBrowser, label: str = ""):
        self._browser = browser
        self._label = label
        self._market_states: Dict[str, MarketState] = {}
        self._buy_amount: int = 0
        self._insufficient_balance: bool = False  # Flag to pause new BUY orders
    
    def _log(self, message: str, level: str = "INFO", prefix: str = ""):
        """Log with label prefix"""
        formatted = f"[{self._label}] [{prefix}] {message}" if prefix else f"[{self._label}] {message}"
        getattr(logger, level.lower())(formatted)
    
    async def run(self) -> bool:
        """Run the limit trading strategy"""
        try:
            # Authenticate
            if not await self._browser.authenticate():
                self._log("Auth failed!", "ERROR", "X")
                return False
            
            self._log(f"Logged in: {self._browser.address[:12]}...", "SUCCESS", "+")
            
            # Check balances
            usdt_balance = await self._browser.get_usdt_balance()
            bnb_balance, bnb_usd = await self._browser.get_bnb_balance_usd()
            self._log(f"USDT: ${usdt_balance:.2f} | BNB: {bnb_balance:.4f} (${bnb_usd:.2f})", "INFO", "$")
            
            # Alerts
            if bnb_balance < config.alerts.min_bnb:
                await telegram.send_alert(
                    label=f"LOW BNB - {self._label}",
                    message=f"BNB balance: {bnb_balance:.4f}"
                )
            
            if usdt_balance < config.alerts.min_usdt:
                await telegram.send_alert(
                    label=f"LOW USDT - {self._label}",
                    message=f"USDT balance: ${usdt_balance:.2f}"
                )
                self._log(f"Low USDT: ${usdt_balance:.2f}", "WARNING", "!")
                return False
            
            # Buy amount (same for all markets)
            self._buy_amount = int(uniform(*config.markets.split_amount))
            
            # Get configured markets
            markets = config.markets.list
            if not markets:
                self._log("No markets configured!", "WARNING", "!")
                return False
            
            self._log(f"Starting parallel trading on {len(markets)} markets", "INFO", "*")
            self._log(f"Buy amount: ${self._buy_amount} per market", "INFO", "$")
            
            # Initialize market states
            await self._initialize_markets(markets)
            
            # Main loop - run indefinitely until stopped
            self._log("Running indefinitely (Ctrl+C to stop)", "INFO", "~")
            
            cycle = 0
            while True:
                cycle += 1
                
                try:
                    await self._run_cycle()
                except AuthTokenExpiredError:
                    self._log("Token expired - re-logging...", "WARNING", "!")
                    await self._browser.relogin()
                except KeyboardInterrupt:
                    self._log("Stopped by user", "INFO", "!")
                    break
                except Exception as e:
                    self._log(f"Cycle error: {e}", "ERROR", "X")
                
                await asyncio.sleep(uniform(*config.sleep.between_cycles))
            
            self._log("Trading completed!", "SUCCESS", "+")
            return True
            
        except Exception as e:
            self._log(f"Run failed: {e}", "ERROR", "X")
            return False
    
    async def _initialize_markets(self, markets: List[MarketCondition]):
        """Initialize state for all markets - preserves existing orders and positions"""
        self._log("Initializing markets (preserving existing orders)...", "INFO", "~")
        
        # Get existing positions and orders
        positions = await self._browser.get_positions() or []
        open_orders = await self._browser.get_orders(status="open") or []
        
        self._log(f"Found {len(positions)} positions, {len(open_orders)} open orders", "INFO", "*")
        
        # Index by market
        positions_by_market = {}
        for pos in positions:
            mid = str(pos.market_id)
            if mid not in positions_by_market:
                positions_by_market[mid] = []
            positions_by_market[mid].append(pos)
        
        orders_by_market = {}
        for order in open_orders:
            mid = str(order.market_id)
            if mid not in orders_by_market:
                orders_by_market[mid] = []
            orders_by_market[mid].append(order)
        
        # Process each market
        for idx, market_cfg in enumerate(markets, 1):
            try:
                # Get market info
                event = await self._browser.get_market_event(
                    market_cfg.market_id, 
                    choice_index=market_cfg.choice
                )
                
                if not event:
                    self._log(f"[M{idx}] Market not found: {market_cfg.market_id}", "WARNING", "!")
                    continue
                
                if event.status != "REGISTERED":
                    self._log(f"[M{idx}] {event.name[:30]} | Status: {event.status}", "WARNING", "!")
                    continue
                
                market_id = str(event.market_id)
                
                # First check if we already have positions or orders on this market
                # If yes - use that outcome instead of choosing by price
                existing_outcome = None
                
                # Check existing positions
                if market_id in positions_by_market:
                    for pos in positions_by_market[market_id]:
                        if pos.balance >= 1.0:
                            existing_outcome = pos.outcome.lower()
                            self._log(f"[M{idx}] Found existing position: {pos.balance:.1f} {existing_outcome.upper()}", "INFO", "*")
                            break
                
                # Check existing orders (if no position)
                if not existing_outcome and market_id in orders_by_market:
                    for order in orders_by_market[market_id]:
                        existing_outcome = order.outcome.lower()
                        self._log(f"[M{idx}] Found existing {order.side.upper()} order: {order.outcome.upper()}", "INFO", "*")
                        break
                
                # Determine outcome
                if existing_outcome:
                    # Use existing outcome
                    outcome = existing_outcome
                else:
                    # No existing position/orders - determine by price or config
                    yes_book = await self._browser.get_market_orderbook(market_id, "yes")
                    yes_price = yes_book.best_bid if yes_book and yes_book.bids else 0.0
                    no_price = round(1.0 - yes_price, 3)
                    
                    # Use explicit choice if provided
                    choice_str = str(market_cfg.choice).lower() if market_cfg.choice else ""
                    if choice_str in ['yes', 'no']:
                        outcome = choice_str
                    else:
                        # Pick the more expensive share
                        outcome = "yes" if yes_price >= no_price else "no"
                    
                    self._log(f"[M{idx}] New market, choosing {outcome.upper()}", "INFO", "?")
                
                # Get token ID
                token_id = event.yes_token_id if outcome == "yes" else event.no_token_id
                
                # Create state
                state = MarketState(
                    market_id=market_id,
                    market_name=event.name,
                    slug=market_cfg.market_id,
                    outcome=outcome,
                    token_id=token_id
                )
                
                # Check for existing position
                if market_id in positions_by_market:
                    for pos in positions_by_market[market_id]:
                        if pos.outcome.lower() == outcome and pos.balance >= 1.0:
                            state.has_position = True
                            state.position_size = pos.balance
                
                # Check for existing orders and save entry price
                if market_id in orders_by_market:
                    for order in orders_by_market[market_id]:
                        if order.outcome.lower() == outcome:
                            if order.side.lower() == "buy":
                                state.buy_order_id = order.order_id
                                state.buy_price = order.price
                                # Save entry price for future SELL calculation
                                if outcome == "yes":
                                    entry_prices.set_entry_prices(market_id, order.price, 0.0)
                                else:
                                    entry_prices.set_entry_prices(market_id, 0.0, order.price)
                            elif order.side.lower() == "sell":
                                state.sell_order_id = order.order_id
                                state.sell_price = order.price
                
                self._market_states[market_id] = state
                
                # Determine status for logging
                if state.sell_order_id:
                    status = f"SELL @ {format_cents(state.sell_price)}"
                elif state.has_position:
                    status = f"POS: {state.position_size:.0f}"
                elif state.buy_order_id:
                    status = f"BUY @ {format_cents(state.buy_price)}"
                else:
                    status = "NEW"
                
                self._log(f"[M{idx}] {event.name[:30]} | {outcome.upper()} | {status}", "INFO", "+")
                
                await asyncio.sleep(0.3)
                
            except Exception as e:
                self._log(f"[M{idx}] Init error: {e}", "ERROR", "X")
        
        self._log(f"Initialized {len(self._market_states)} markets", "SUCCESS", "+")
    
    async def _run_cycle(self):
        """Run one cycle through all markets"""
        # Refresh positions and orders
        positions = await self._browser.get_positions() or []
        open_orders = await self._browser.get_orders(status="open") or []
        
        # Build lookup sets for quick checking
        # Positions: set of (market_id, outcome) tuples
        position_set = set()
        position_balances = {}
        for pos in positions:
            key = (str(pos.market_id), pos.outcome.lower())
            if pos.balance >= 1.0:
                position_set.add(key)
                position_balances[key] = pos.balance
        
        # Orders: separate dicts for BUY and SELL orders
        # Can have BOTH buy and sell orders for same token!
        buy_orders = {}   # (market_id, token_id) -> (order_id, price, order_obj)
        sell_orders = {}  # (market_id, token_id) -> (order_id, price, order_obj)
        for order in open_orders:
            key = (str(order.market_id), str(order.token_id))
            if order.side.lower() == "buy":
                buy_orders[key] = (order.order_id, order.price, order)
            elif order.side.lower() == "sell":
                sell_orders[key] = (order.order_id, order.price, order)
        
        logger.debug(f"Found {len(positions)} positions, {len(buy_orders)} BUY orders, {len(sell_orders)} SELL orders")
        
        # If we're waiting for balance, show status
        if self._insufficient_balance:
            if sell_orders:
                self._log(f"Waiting for balance... {len(sell_orders)} SELL orders active", "INFO", "$")
            else:
                # No SELL orders but marked as insufficient - reset flag
                self._insufficient_balance = False
                self._log("Balance restored (no pending SELLs)", "SUCCESS", "+")
        
        # Process each market
        for market_id, state in self._market_states.items():
            try:
                short_name = state.market_name[:20]
                
                # Check current state
                pos_key = (market_id, state.outcome)
                has_position = pos_key in position_set
                position_size = position_balances.get(pos_key, 0.0)
                
                order_key = (market_id, state.token_id)
                buy_order_info = buy_orders.get(order_key)
                sell_order_info = sell_orders.get(order_key)
                
                has_buy_order = buy_order_info is not None
                has_sell_order = sell_order_info is not None
                
                # State machine logic:
                # 1. BUY order active -> wait for fill
                # 2. Position exists (shares bought) -> place SELL
                # 3. SELL order active -> wait for fill (DON'T create BUY!)
                # 4. No orders, no position -> place BUY (new cycle)
                
                if has_sell_order:
                    # SELL order is active - wait for it to fill
                    # DON'T create new BUY while SELL is pending!
                    state.sell_order_id = sell_order_info[0]
                    state.sell_price = sell_order_info[1]
                    state.has_position = True  # Shares are locked in SELL order
                    self._log(f"[{short_name}] SELL active @ {format_cents(sell_order_info[1])} - waiting...", "INFO", "~")
                    
                    # Also cancel any stale BUY orders if they exist
                    if has_buy_order:
                        self._log(f"[{short_name}] Canceling stale BUY order while SELL is active...", "INFO", "-")
                        try:
                            await self._browser.cancel_order(buy_order_info[0])
                        except Exception as e:
                            logger.debug(f"Failed to cancel stale BUY: {e}")
                    
                elif has_position:
                    # Have position (shares) but no SELL order -> need to place SELL
                    state.has_position = True
                    state.position_size = position_size
                    state.buy_order_id = None  # BUY was filled
                    
                    # Cancel any BUY order first (might be partial fill situation)
                    if has_buy_order:
                        self._log(f"[{short_name}] Canceling BUY order, placing SELL instead...", "INFO", "-")
                        try:
                            await self._browser.cancel_order(buy_order_info[0])
                            await asyncio.sleep(0.5)
                        except Exception as e:
                            logger.debug(f"Failed to cancel BUY: {e}")
                    
                    self._log(f"[{short_name}] Position: {position_size:.0f} shares. Placing SELL...", "INFO", "$")
                    await self._place_sell(state)
                    
                elif has_buy_order:
                    # BUY order exists - wait for fill / check reprice
                    state.has_position = False
                    state.position_size = 0.0
                    state.buy_order_id = buy_order_info[0]
                    state.buy_price = buy_order_info[1]
                    
                    # Get the full Order object for repricing
                    current_order = buy_order_info[2]
                    
                    if current_order and config.limits.repost_if_not_best:
                        await self._check_reprice_buy(state, current_order)
                    else:
                        self._log(f"[{short_name}] BUY waiting @ {format_cents(buy_order_info[1])}", "INFO", "~")
                        
                else:
                    # No orders, no position -> start new cycle with BUY
                    state.has_position = False
                    state.position_size = 0.0
                    
                    if state.sell_order_id:
                        # SELL was just filled - cycle complete! Balance should be available now
                        self._log(f"[{short_name}] SELL filled! Starting new cycle...", "SUCCESS", "$")
                        state.sell_order_id = None
                        self._insufficient_balance = False  # Reset flag - we have balance now
                    
                    # Check if we have balance to place new BUY orders
                    if self._insufficient_balance:
                        self._log(f"[{short_name}] Waiting for balance (SELL to fill)...", "INFO", "~")
                    else:
                        self._log(f"[{short_name}] No orders, placing BUY...", "INFO", ">")
                        await self._place_buy(state)
                
                await asyncio.sleep(0.5)
                
            except Exception as e:
                self._log(f"[{state.market_name[:20]}] Process error: {e}", "ERROR", "X")
    
    
    async def _place_buy(self, state: MarketState):
        """Place BUY order"""
        try:
            # API always returns YES orderbook
            book = await self._browser.get_market_orderbook(state.market_id, "yes")
            
            if not book or not book.bids:
                self._log(f"[{state.market_name[:20]}] No bids in orderbook", "WARNING", "!")
                return
            
            buy_step = config.limits.buy_price_step / 100  # центы -> доллары
            
            # Calculate price based on outcome (per documentation):
            # YES bids = from orderbook directly
            # NO bids = complement of YES asks
            if state.outcome == "yes":
                # YES: place at best_bid + step to be first in queue
                best_bid = book.best_bid
                buy_price = best_bid + buy_step
                # Don't exceed best_ask (to avoid instant fill as market order)
                if book.asks and buy_step > 0:
                    buy_price = min(buy_price, book.best_ask - abs(buy_step))
            else:
                # NO: NO best bid = 1 - YES best ask
                # noBids = yesAsks.map(([p, q]) => [1 - p, q])
                if not book.asks:
                    self._log(f"[{state.market_name[:20]}] No asks in orderbook", "WARNING", "!")
                    return
                no_best_bid = round(1.0 - book.best_ask, 3)
                buy_price = no_best_bid + buy_step
                # Don't exceed NO best_ask (= 1 - YES best_bid)
                if buy_step > 0:
                    no_best_ask = round(1.0 - book.best_bid, 3)
                    buy_price = min(buy_price, no_best_ask - abs(buy_step))
            
            buy_price = round(buy_price, 3)
            buy_price = max(buy_price, 0.01)  # Min 1 cent
            buy_price = min(buy_price, 0.95)  # Max 95 cents
            
            # Calculate size
            size = int(self._buy_amount / buy_price)
            if size < 1:
                self._log(f"[{state.market_name[:20]}] Size too small", "WARNING", "!")
                return
            
            self._log(f"[{state.market_name[:20]}] BUY {state.outcome.upper()} @ {format_cents(buy_price)} x {size}", "INFO", ">")
            
            order_id = await self._browser.create_order(
                market_id=state.market_id,
                outcome=state.outcome,
                side="buy",
                price=buy_price,
                size=size,
                token_id=state.token_id
            )
            
            if order_id:
                state.buy_order_id = order_id
                state.buy_price = buy_price
                self._insufficient_balance = False  # Success - we have balance
                self._log(f"[{state.market_name[:20]}] BUY order placed", "SUCCESS", "+")
                
                # Save entry price
                if state.outcome == "yes":
                    entry_prices.set_entry_prices(state.market_id, buy_price, 0.0)
                else:
                    entry_prices.set_entry_prices(state.market_id, 0.0, buy_price)
                
        except Exception as e:
            error_str = str(e).lower()
            # Check if it's a balance/collateral error
            if "insufficient" in error_str or "collateral" in error_str or "balance" in error_str:
                self._insufficient_balance = True
                self._log(f"[{state.market_name[:20]}] Insufficient balance - waiting for SELL to fill", "WARNING", "$")
            else:
                self._log(f"[{state.market_name[:20]}] BUY error: {e}", "ERROR", "X")
    
    async def _place_sell(self, state: MarketState):
        """Place SELL order"""
        try:
            # Base price from entry + markup
            entry_data = entry_prices.get_entry_prices(state.market_id)
            if entry_data:
                base_price = entry_data.get(f"{state.outcome}_entry_price", 0.0)
            else:
                base_price = 0.0
            
            if not base_price:
                base_price = state.buy_price or 0.5
            
            sell_step = config.limits.sell_price_step / 100  # наценка от цены покупки
            sell_price = base_price + sell_step
            sell_price = min(sell_price, 0.99)
            
            size = int(state.position_size)
            if size < 1:
                self._log(f"[{state.market_name[:20]}] Position too small to sell", "WARNING", "!")
                return
            
            self._log(f"[{state.market_name[:20]}] SELL {state.outcome.upper()} @ {format_cents(sell_price)} x {size}", "INFO", ">")
            
            order_id = await self._browser.create_order(
                market_id=state.market_id,
                outcome=state.outcome,
                side="sell",
                price=sell_price,
                size=size,
                token_id=state.token_id
            )
            
            if order_id:
                state.sell_order_id = order_id
                state.sell_price = sell_price
                self._log(f"[{state.market_name[:20]}] SELL order placed", "SUCCESS", "+")
                
                # Telegram notification
                await telegram.send_order_filled(
                    label=self._label,
                    market=state.market_name[:50],
                    side=f"BUY {state.outcome.upper()}",
                    price=base_price * 100,
                    amount=state.position_size,
                    revenue=state.position_size * base_price,
                    remaining_side="SELL pending"
                )
            
        except Exception as e:
            self._log(f"[{state.market_name[:20]}] SELL error: {e}", "ERROR", "X")
    
    async def _check_reprice_buy(self, state: MarketState, current_order: Order):
        """Check if BUY order needs repricing to stay at best bid + step"""
        try:
            # API always returns YES orderbook
            book = await self._browser.get_market_orderbook(state.market_id, "yes")
            
            if not book or not book.bids:
                return
            
            buy_step = config.limits.buy_price_step / 100  # может быть отрицательным (пассивный режим)
            abs_step = abs(buy_step) if buy_step != 0 else 0.001  # минимальный шаг для сравнения
            our_price = state.buy_price
            
            # Calculate target price based on outcome
            if state.outcome == "yes":
                best_bid = book.best_bid
                target_price = best_bid + buy_step  # + положительный = агрессивно, + отрицательный = пассивно
                # Don't exceed best_ask (для положительного step)
                if book.asks and buy_step > 0:
                    target_price = min(target_price, book.best_ask - abs_step)
            else:
                # NO: NO best bid = 1 - YES best ask
                if not book.asks:
                    return
                no_best_bid = round(1.0 - book.best_ask, 3)
                target_price = no_best_bid + buy_step
                # Don't exceed NO best_ask (= 1 - YES best_bid) для положительного step
                if buy_step > 0:
                    no_best_ask = round(1.0 - book.best_bid, 3)
                    target_price = min(target_price, no_best_ask - abs_step)
                best_bid = no_best_bid  # for logging
            
            target_price = round(target_price, 3)
            target_price = max(target_price, 0.01)
            target_price = min(target_price, 0.95)
            
            # Check if we're already at target price (within tolerance)
            if abs(our_price - target_price) < 0.001:
                self._log(
                    f"[{state.market_name[:20]}] BUY @ {format_cents(our_price)} ✓ (best bid: {format_cents(best_bid)})",
                    "INFO", "~"
                )
                return
            
            # Only reprice if difference is significant (at least half of abs_step)
            if abs(target_price - our_price) < abs_step * 0.5:
                self._log(
                    f"[{state.market_name[:20]}] BUY @ {format_cents(our_price)} (target: {format_cents(target_price)}, skip small diff)",
                    "INFO", "~"
                )
                return
            
            # Determine direction for logging
            direction = "↑" if target_price > our_price else "↓"
            
            self._log(
                f"[{state.market_name[:20]}] BUY reprice: {format_cents(our_price)} → {format_cents(target_price)} (best bid: {format_cents(best_bid)})",
                "INFO", direction
            )
            
            # Cancel old order
            await self._browser.cancel_order(state.buy_order_id)
            await asyncio.sleep(1)
            
            # Place new order
            remaining = current_order.size - current_order.filled
            if remaining >= 1:
                order_id = await self._browser.create_order(
                    market_id=state.market_id,
                    outcome=state.outcome,
                    side="buy",
                    price=target_price,
                    size=int(remaining),
                    token_id=state.token_id
                )
                
                if order_id:
                    state.buy_order_id = order_id
                    state.buy_price = target_price
                    if state.outcome == "yes":
                        entry_prices.set_entry_prices(state.market_id, target_price, 0.0)
                    else:
                        entry_prices.set_entry_prices(state.market_id, 0.0, target_price)
                    self._log(f"[{state.market_name[:20]}] BUY repriced successfully", "SUCCESS", "+")
                    
        except Exception as e:
            logger.debug(f"Reprice BUY error: {e}")
    
    async def _check_reprice_sell(self, state: MarketState, current_order: Order):
        """Check if SELL order needs repricing"""
        try:
            book = await self._browser.get_market_orderbook(state.market_id, state.outcome)
            
            if not book.asks:
                return
            
            best_ask = book.best_ask
            our_price = state.sell_price
            sell_step = config.limits.sell_price_step / 100  # наценка от цены покупки
            
            # Минимальная цена продажи = цена покупки + наценка
            min_sell_price = state.buy_price + sell_step
            
            # Only reprice if we're significantly above best ask
            if our_price <= best_ask + 0.0001:
                self._log(
                    f"[{state.market_name[:20]}] SELL @ {format_cents(our_price)} ✓ (best: {format_cents(best_ask)})",
                    "INFO", "~"
                )
                return
            
            # Новая цена = лучший ask (чтобы быть первым в очереди)
            # Но не ниже минимальной цены продажи
            new_price = best_ask
            new_price = max(new_price, min_sell_price)
            
            if abs(our_price - new_price) < 0.001:
                return
            
            self._log(
                f"[{state.market_name[:20]}] SELL reprice: {format_cents(our_price)} → {format_cents(new_price)} (best ask: {format_cents(best_ask)})",
                "INFO", "↓"
            )
            
            # Cancel old order
            await self._browser.cancel_order(state.sell_order_id)
            await asyncio.sleep(1)
            
            # Place new order
            remaining = current_order.size - current_order.filled
            if remaining >= 1:
                order_id = await self._browser.create_order(
                    market_id=state.market_id,
                    outcome=state.outcome,
                    side="sell",
                    price=new_price,
                    size=int(remaining),
                    token_id=state.token_id
                )
                
                if order_id:
                    state.sell_order_id = order_id
                    state.sell_price = new_price
                    self._log(f"[{state.market_name[:20]}] SELL repriced successfully", "SUCCESS", "+")
                    
        except Exception as e:
            logger.debug(f"Reprice SELL error: {e}")
