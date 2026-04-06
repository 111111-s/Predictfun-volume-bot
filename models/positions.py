"""
Models for Predict.fun bot

Data classes for markets, orders, positions.
"""

from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from enum import Enum
from datetime import datetime


class OrderSide(Enum):
    """Order side"""
    YES = "yes"
    NO = "no"


class OrderStatus(Enum):
    """Order status"""
    OPEN = "open"
    FILLED = "filled"
    CANCELLED = "cancelled"
    PARTIAL = "partial"


@dataclass
class MarketEvent:
    """Market event data"""
    market_id: str
    name: str
    condition_id: str
    status: str = "active"
    
    # Token IDs
    yes_token_id: str = ""
    no_token_id: str = ""
    
    # Multi-choice support
    choice_index: int = 0
    choice_name: str = ""
    
    # URLs
    link: str = ""
    
    @property
    def topic_id(self) -> str:
        """Alias for market_id"""
        return self.market_id
    
    @property
    def full_id(self) -> str:
        """Full ID with choice"""
        if self.choice_index:
            return f"{self.market_id}:{self.choice_index}"
        return self.market_id
    
    @classmethod
    def from_api_response(cls, data: Dict, choice_index: int = 0) -> "MarketEvent":
        """Create from API response"""
        # Parse token IDs from outcomes array if available
        yes_token_id = ""
        no_token_id = ""
        
        outcomes = data.get("outcomes", [])
        for outcome in outcomes:
            name = outcome.get("name", "").lower()
            on_chain_id = str(outcome.get("onChainId", ""))
            if name == "yes" and on_chain_id:
                yes_token_id = on_chain_id
            elif name == "no" and on_chain_id:
                no_token_id = on_chain_id
        
        # Fallback to old format
        if not yes_token_id:
            yes_token_id = data.get("yesTokenId", data.get("tokens", {}).get("yes", ""))
        if not no_token_id:
            no_token_id = data.get("noTokenId", data.get("tokens", {}).get("no", ""))
        
        return cls(
            market_id=data.get("id", data.get("marketId", "")),
            name=data.get("title", data.get("name", "")),
            condition_id=data.get("conditionId", ""),
            status=data.get("status", "active"),
            yes_token_id=yes_token_id,
            no_token_id=no_token_id,
            choice_index=choice_index,
            link=data.get("url", f"https://predict.fun/market/{data.get('id', '')}")
        )


@dataclass
class OrderBook:
    """Order book data"""
    bids: List[tuple] = field(default_factory=list)  # [(price, size), ...]
    asks: List[tuple] = field(default_factory=list)  # [(price, size), ...]
    
    @property
    def best_bid(self) -> float:
        """Best bid price"""
        if self.bids:
            return self.bids[0][0]
        return 0.01
    
    @property
    def best_ask(self) -> float:
        """Best ask price"""
        if self.asks:
            return self.asks[0][0]
        return 0.99
    
    @property
    def spread(self) -> float:
        """Spread between best bid and ask"""
        return self.best_ask - self.best_bid
    
    @classmethod
    def from_api_response(cls, data: Dict) -> "OrderBook":
        """Create from API response"""
        bids = []
        asks = []
        
        # Handle API response wrapper {"success": true, "data": {...}}
        if "data" in data and isinstance(data.get("data"), dict):
            data = data["data"]
        
        # Parse bids
        for bid in data.get("bids", data.get("buys", [])):
            if isinstance(bid, dict):
                price = float(bid.get("price", 0))
                size = float(bid.get("size", bid.get("amount", 0)))
            else:
                price, size = float(bid[0]), float(bid[1])
            bids.append((price, size))
        
        # Parse asks
        for ask in data.get("asks", data.get("sells", [])):
            if isinstance(ask, dict):
                price = float(ask.get("price", 0))
                size = float(ask.get("size", ask.get("amount", 0)))
            else:
                price, size = float(ask[0]), float(ask[1])
            asks.append((price, size))
        
        # Sort: bids descending, asks ascending
        bids.sort(key=lambda x: x[0], reverse=True)
        asks.sort(key=lambda x: x[0])
        
        return cls(bids=bids, asks=asks)


@dataclass
class Order:
    """Order data"""
    order_id: str  # Numeric ID for API queries
    order_hash: str  # Hash for cancel operations
    market_id: str
    outcome: str  # "yes" or "no"
    side: str  # "buy" or "sell"
    price: float
    size: float
    token_id: str = ""  # Token ID for matching YES/NO
    filled: float = 0
    status: str = "open"
    created_at: Optional[datetime] = None
    raw_data: Optional[Dict] = None  # Original API response for SDK operations
    
    @property
    def remaining(self) -> float:
        """Remaining unfilled size"""
        return self.size - self.filled
    
    @property
    def is_filled(self) -> bool:
        """Check if fully filled"""
        return self.filled >= self.size or self.status == "filled"
    
    @property
    def is_buy(self) -> bool:
        return self.side.lower() == "buy"
    
    @property
    def is_sell(self) -> bool:
        return self.side.lower() == "sell"
    
    @classmethod
    def from_api_response(cls, data: Dict) -> "Order":
        """Create from API response"""
        # API returns nested 'order' object with details
        order_obj = data.get("order", {})
        
        # Get order ID - prefer top-level 'id' for API lookups
        order_id = str(data.get("id", data.get("orderId", "")))
        
        # Get order hash - needed for cancel operations
        order_hash = order_obj.get("hash", data.get("hash", data.get("orderHash", "")))
        
        # Get market ID from top level
        market_id = str(data.get("marketId", ""))
        
        # Side: in order object as number (0=BUY, 1=SELL) or string
        side_raw = order_obj.get("side", data.get("side", ""))
        if isinstance(side_raw, int):
            side = "sell" if side_raw == 1 else "buy"
        else:
            side = str(side_raw).lower() if side_raw else ""
        
        # Outcome: may need to determine from tokenId (not always present)
        # For now, try to get from data or leave empty
        outcome = data.get("outcome", "")
        if isinstance(outcome, dict):
            outcome = outcome.get("name", "")
        outcome = str(outcome).lower() if outcome else ""
        
        # Token ID for later matching
        token_id = order_obj.get("tokenId", data.get("tokenId", ""))
        
        # Size/amount - in wei, convert to human readable
        amount_raw = float(data.get("amount", order_obj.get("makerAmount", 0)))
        if amount_raw > 10**15:
            amount = amount_raw / 10**18
        else:
            amount = amount_raw
        
        # Filled amount
        filled_raw = float(data.get("amountFilled", data.get("filled", 0)))
        if filled_raw > 10**15:
            filled = filled_raw / 10**18
        else:
            filled = filled_raw
        
        # Price from makerAmount/takerAmount ratio or pricePerShare
        price = 0.0
        if order_obj.get("makerAmount") and order_obj.get("takerAmount"):
            try:
                maker = float(order_obj["makerAmount"])
                taker = float(order_obj["takerAmount"])
                if taker > 0 and maker > 0:
                    # BUY: makerAmount=USDT, takerAmount=shares -> price = maker/taker
                    # SELL: makerAmount=shares, takerAmount=USDT -> price = taker/maker
                    if side == "buy":
                        price = maker / taker  # USDT per share
                    else:
                        price = taker / maker  # USDT per share
            except:
                pass
        if price == 0:
            price = float(data.get("price", data.get("pricePerShare", 0)))
            if price > 10**15:
                price = price / 10**18
        
        return cls(
            order_id=order_id,
            order_hash=order_hash,
            market_id=market_id,
            outcome=outcome,
            side=side,
            price=price,
            size=amount,
            token_id=str(token_id),  # Store for YES/NO matching
            filled=filled,
            status=data.get("status", "open").lower(),
            raw_data=data  # Store original data for SDK operations
        )


@dataclass
class Position:
    """Token position"""
    market_id: str
    outcome: str  # "yes" or "no"
    balance: float
    avg_price: float = 0
    condition_id: str = ""
    
    @property
    def value(self) -> float:
        """Position value at avg price"""
        return self.balance * self.avg_price
    
    @property
    def is_yes(self) -> bool:
        """Check if this is a YES position"""
        return self.outcome.lower() == "yes"
    
    @property
    def is_no(self) -> bool:
        """Check if this is a NO position"""
        return self.outcome.lower() == "no"
    
    @classmethod
    def from_api_response(cls, data: Dict) -> "Position":
        """Create from API response"""
        # Parse outcome - can be string or dict
        outcome = data.get("outcome", "")
        if isinstance(outcome, dict):
            outcome = outcome.get("name", outcome.get("title", "yes"))
        outcome = str(outcome).lower() if outcome else "yes"
        
        # Parse balance - convert from wei (18 decimals) to human readable
        raw_balance = float(data.get("balance", data.get("amount", data.get("shares", 0))))
        # If balance looks like wei (very large number), convert
        if raw_balance > 10**15:
            raw_balance = raw_balance / 10**18
        
        # Parse market_id - can be in nested 'market' object
        market_id = data.get("marketId", data.get("market_id", ""))
        if not market_id:
            market_obj = data.get("market", {})
            if isinstance(market_obj, dict):
                market_id = market_obj.get("id", "")
        
        # Parse condition_id from market object
        condition_id = ""
        market_obj = data.get("market", {})
        if isinstance(market_obj, dict):
            condition_id = market_obj.get("conditionId", "")
        
        return cls(
            market_id=str(market_id),
            outcome=outcome,
            balance=raw_balance,
            avg_price=float(data.get("avgPrice", data.get("averagePrice", data.get("avg_price", 0)))),
            condition_id=condition_id
        )


@dataclass
class BuyPosition:
    """
    Tracking position after buying YES+NO.
    
    Used to track:
    - How much YES and NO we bought
    - What price we bought at
    - Current fill status of sell orders
    """
    market_id: str
    market_name: str
    condition_id: str
    
    # Buy info
    yes_bought: float = 0
    no_bought: float = 0
    yes_buy_price: float = 0
    no_buy_price: float = 0
    
    # Buy orders (for tracking pending buys)
    yes_buy_order_id: Optional[str] = None
    no_buy_order_id: Optional[str] = None
    
    # Token IDs
    yes_token_id: str = ""
    no_token_id: str = ""
    
    # Sell orders
    yes_sell_order_id: str = ""
    yes_sell_order_hash: str = ""
    no_sell_order_id: str = ""
    no_sell_order_hash: str = ""
    yes_sell_price: float = 0
    no_sell_price: float = 0
    yes_sell_order: Optional["Order"] = None  # Full order object for SDK operations
    no_sell_order: Optional["Order"] = None
    
    # Sold amounts
    yes_sold: float = 0
    no_sold: float = 0
    
    # Status
    merged: float = 0  # Amount merged back to USDT
    stopped_out: bool = False
    
    @property
    def yes_remaining(self) -> float:
        """YES tokens remaining to sell"""
        return self.yes_bought - self.yes_sold - self.merged
    
    @property
    def no_remaining(self) -> float:
        """NO tokens remaining to sell"""
        return self.no_bought - self.no_sold - self.merged
    
    @property
    def is_complete(self) -> bool:
        """Check if position is fully closed"""
        return (
            self.yes_remaining < 0.01 and 
            self.no_remaining < 0.01
        )
    
    @property
    def can_merge(self) -> float:
        """Amount that can be merged (min of YES and NO remaining)"""
        return min(self.yes_remaining, self.no_remaining)
    
    @property
    def total_bought_value(self) -> float:
        """Total value of what we bought"""
        return (self.yes_bought * self.yes_buy_price + 
                self.no_bought * self.no_buy_price)
    
    @property
    def total_sold_value(self) -> float:
        """Total value of what we sold"""
        return (self.yes_sold * self.yes_sell_price + 
                self.no_sold * self.no_sell_price +
                self.merged)  # Merged = $1 per pair
    
    @property
    def pnl(self) -> float:
        """Profit/loss"""
        return self.total_sold_value - self.total_bought_value


@dataclass
class OrderInfo:
    """Order info for coordinator"""
    order_id: str
    account_address: str
    market_id: str
    side: OrderSide
    price: float
    amount: float
    created_at: float = field(default_factory=lambda: datetime.now().timestamp())
    
    def is_expired(self, max_age: int = 3600) -> bool:
        """Check if order is expired"""
        return (datetime.now().timestamp() - self.created_at) > max_age
