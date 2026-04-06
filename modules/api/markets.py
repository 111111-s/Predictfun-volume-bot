"""
Predict.fun API - Markets endpoints

GET /markets, GET /markets/{id}, GET /markets/{id}/orderbook,
GET /markets/{id}/statistics, GET /markets/{id}/last-sale
"""

from typing import Optional, Dict, List
from loguru import logger

from models import MarketEvent, OrderBook

from .base import PredictAPIError


class MarketsMixin:
    """Миксин для работы с маркетами"""

    async def get_markets(
        self,
        category: Optional[str] = None,
        limit: int = 100,
        offset: int = 0
    ) -> List[Dict]:
        """
        GET /markets
        Get list of markets.

        Args:
            category: Filter by category slug
            limit: Max results
            offset: Pagination offset
        """
        params = {
            "limit": limit,
            "offset": offset
        }
        if category:
            params["category"] = category

        result = await self._request("GET", "/markets", params=params)
        return result.get("markets", result.get("data", []))

    async def get_market(self, market_id: str, choice_index: int = 0) -> Optional[Dict]:
        """
        GET /markets/{id}
        Get market by ID (numeric) or slug.

        Args:
            market_id: Numeric ID or category slug
            choice_index: For multi-choice categories, which market to select (1-based)
        """
        try:
            # Try numeric ID first
            result = await self._request("GET", f"/markets/{market_id}")
            if result.get("success") and "data" in result:
                return result["data"]
            return result
        except PredictAPIError as e:
            # If numeric ID fails, try looking up by category slug
            logger.debug(f"Market ID lookup failed, trying category slug: {market_id} (choice: {choice_index})")
            market = await self.get_market_by_slug(market_id, choice_index)
            if market:
                logger.debug(f"Found market via category: {market.get('id')} - {market.get('title', '')[:40]}")
            else:
                logger.warning(f"Category not found: {market_id}")
            return market

    async def get_market_by_slug(self, slug: str, choice_index: int = 0) -> Optional[Dict]:
        """
        Get market by category slug (URL slug).

        Looks up /v1/categories/{slug} and returns the market at choice_index.
        For multi-choice categories (e.g. Premier League Winner), choice_index
        selects which market (team) to return.

        Args:
            slug: Category slug from URL
            choice_index: Which market to return (0-based, or 1-based from config)
        """
        try:
            result = await self._request("GET", f"/categories/{slug}")
            if result.get("success") and "data" in result:
                data = result["data"]
                # Categories contain a 'markets' array
                markets = data.get("markets", [])
                if markets:
                    # choice_index from config is 1-based (1, 2, 3...) or 0
                    # Convert to 0-based array index
                    idx = max(0, choice_index - 1) if choice_index > 0 else 0
                    idx = min(idx, len(markets) - 1)  # Don't exceed array

                    # Log all markets in category with 1-based numbering for user
                    logger.debug(f"Category '{slug}' has {len(markets)} markets:")
                    for i, m in enumerate(markets[:10]):  # Show first 10
                        marker = " <<<" if i == idx else ""
                        logger.debug(f"  :{i+1} = id={m.get('id')} - {m.get('title', '')[:50]}{marker}")
                    selected_market = markets[idx]
                    logger.debug(f"Selected :{choice_index} = {selected_market.get('title', '')[:30]}")

                    return markets[idx]
            return None
        except PredictAPIError as e:
            logger.debug(f"Category lookup failed for '{slug}': {e}")
            return None

    async def get_market_statistics(self, market_id: str) -> Dict:
        """
        GET /markets/{id}/statistics
        Get market statistics.
        """
        try:
            return await self._request("GET", f"/markets/{market_id}/statistics")
        except PredictAPIError:
            return {}

    async def get_market_last_sale(self, market_id: str) -> Dict:
        """
        GET /markets/{id}/last-sale
        Get market last sale information.
        """
        try:
            return await self._request("GET", f"/markets/{market_id}/last-sale")
        except PredictAPIError:
            return {}

    async def get_market_orderbook(
        self,
        market_id: str,
        outcome: str = "yes"
    ) -> OrderBook:
        """
        GET /markets/{id}/orderbook
        Get the orderbook for a market.

        Note: API always returns YES-based orderbook.
        For NO prices, use complement formula:
        - NO bids = 1 - YES asks
        - NO asks = 1 - YES bids

        Args:
            market_id: Market ID
            outcome: ignored (API always returns YES orderbook)
        """
        result = await self._request(
            "GET",
            f"/markets/{market_id}/orderbook"
            # No params - API doesn't accept outcome parameter
        )

        logger.debug(f"Raw orderbook: {result}")

        return OrderBook.from_api_response(result)

    async def get_market_event(
        self,
        market_id: str,
        choice_index: int = 0
    ) -> Optional[MarketEvent]:
        """Get MarketEvent from market ID or slug"""
        data = await self.get_market(market_id, choice_index)
        if data:
            return MarketEvent.from_api_response(data, choice_index)
        return None
