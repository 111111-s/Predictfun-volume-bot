"""
Predict.fun API - Positions endpoints

GET /positions
"""

from typing import Optional, Dict, List
from loguru import logger

from models import Position


class PositionsMixin:
    """Миксин для работы с позициями"""

    async def get_positions(
        self,
        market_id: Optional[str] = None
    ) -> List[Position]:
        """
        GET /positions
        Get token positions for authenticated user.
        """
        params = {}
        if market_id:
            params["marketId"] = market_id

        result = await self._request(
            "GET",
            "/positions",
            params=params,
            require_auth=True
        )
        positions_data = result.get("positions", result.get("data", []))

        # Debug: log raw position data
        if positions_data:
            logger.debug(f"Raw positions ({len(positions_data)}): {positions_data[:2]}...")

        return [Position.from_api_response(p) for p in positions_data]

    async def get_position_balance(
        self,
        market_id: str,
        outcome: str
    ) -> float:
        """Get token balance for specific market outcome"""
        positions = await self.get_positions(market_id=market_id)

        for pos in positions:
            if pos.outcome.lower() == outcome.lower():
                return pos.balance

        return 0.0

    async def get_all_position_balances(
        self,
        market_id: str
    ) -> Dict[str, float]:
        """Get all position balances for a market"""
        balances = {"yes": 0.0, "no": 0.0}

        try:
            # Get ALL positions (API filtering by marketId often fails)
            positions = await self.get_positions()

            logger.debug(f"Got {len(positions)} positions, looking for market_id={market_id}")

            # Filter by market_id on our side
            for pos in positions:
                logger.debug(f"Position: market={pos.market_id} outcome={pos.outcome} balance={pos.balance}")
                # Match by market_id (can be string or int)
                if str(pos.market_id) == str(market_id):
                    outcome = str(pos.outcome).lower()
                    if outcome in balances:
                        balances[outcome] = pos.balance
        except Exception as e:
            logger.debug(f"Failed to get positions: {e}")

        return balances
