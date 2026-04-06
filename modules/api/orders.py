"""
Predict.fun API - Orders endpoints

GET /orders, GET /orders/{hash}, POST /orders, POST /orders/cancel
"""

import asyncio
import math
from typing import Optional, Dict, List
from decimal import Decimal, ROUND_DOWN
from loguru import logger

from models import Order

from ..constants import WEI, PRICE_PRECISION_WEI, AMOUNT_PRECISION_WEI
from .base import PredictAPIError


class OrdersMixin:
    """Миксин для работы с ордерами"""

    async def get_orders(
        self,
        market_id: Optional[str] = None,
        status: Optional[str] = None
    ) -> List[Order]:
        """
        GET /orders
        Get orders for the authenticated user.

        Args:
            market_id: Filter by market ID
            status: Filter by status on client side (open, filled, cancelled)
        """
        params = {}
        if market_id:
            params["marketId"] = market_id
        # Note: API doesn't support status filter, we filter client-side

        result = await self._request("GET", "/orders", params=params, require_auth=True)
        orders_data = result.get("orders", result.get("data", []))

        if orders_data:
            logger.debug(f"Raw orders data sample: {orders_data[0]}")

        orders = [Order.from_api_response(o) for o in orders_data]

        # Client-side status filter
        if status:
            status_lower = status.lower()
            if status_lower == "open":
                orders = [o for o in orders if o.status.lower() in ("open", "partial", "pending")]
            elif status_lower == "filled":
                orders = [o for o in orders if o.status.lower() == "filled"]
            elif status_lower == "cancelled":
                orders = [o for o in orders if o.status.lower() == "cancelled"]

        return orders

    async def get_order(self, order_hash: str) -> Optional[Order]:
        """
        GET /orders/{hash}
        Get order by hash.
        """
        try:
            result = await self._request(
                "GET",
                f"/orders/{order_hash}",
                require_auth=True
            )
            return Order.from_api_response(result)
        except PredictAPIError:
            return None

    async def get_order_match_events(
        self,
        market_id: Optional[str] = None,
        limit: int = 100
    ) -> List[Dict]:
        """
        GET /order-match-events
        Get order match events (trades).
        """
        params = {"limit": limit}
        if market_id:
            params["marketId"] = market_id

        result = await self._request(
            "GET",
            "/order-match-events",
            params=params,
            require_auth=True
        )
        return result.get("events", result.get("data", []))

    async def create_order(
        self,
        market_id: str,
        outcome: str,
        side: str,
        price: float,
        size: float,
        token_id: Optional[str] = None
    ) -> Optional[str]:
        """
        POST /orders
        Create a new order via API with SDK signing.

        Based on Predict SDK documentation:
        1. Get token ID and market info
        2. Calculate order amounts with getLimitOrderAmounts
        3. Build order with buildOrder (maker/signer = predictAccount for Smart Wallet)
        4. Build typed data with buildTypedData
        5. Sign with signTypedDataOrder
        6. Get hash with buildTypedDataHash
        7. Submit to API

        Args:
            market_id: Market ID
            outcome: "yes" or "no"
            side: "buy" or "sell"
            price: Price (0.01 to 0.99)
            size: Number of shares
            token_id: Token ID (if known, otherwise fetched from market)

        Returns:
            Order ID if successful
        """
        if not self._order_builder:
            raise PredictAPIError("SDK not initialized, cannot create order")

        try:
            from predict_sdk import Side as SDKSide, LimitHelperInput, BuildOrderInput

            # Validate price
            price = max(0.01, min(0.99, price))

            logger.info(
                f"Creating order: {side} {outcome} @ {price:.4f} x {size:.2f} "
                f"[market: {market_id[:8]}...]"
            )

            # Get market info
            market = await self.get_market(market_id)
            if not market:
                raise PredictAPIError(f"Market not found: {market_id}")

            # Get market properties
            is_neg_risk = market.get("isNegRisk", market.get("negRisk", False))
            is_yield_bearing = market.get("isYieldBearing", market.get("yieldBearing", False))
            fee_rate_bps = int(market.get("feeRateBps", market.get("fee", 0)))

            # Get token ID from outcomes array
            if not token_id:
                outcomes = market.get("outcomes", [])
                for o in outcomes:
                    name = o.get("name", "").lower()
                    if name == outcome.lower():
                        token_id = o.get("onChainId", "")
                        break

                if not token_id:
                    raise PredictAPIError(f"Token ID not found for {outcome}")

            # Convert to SDK types
            sdk_side = SDKSide.SELL if side.lower() == "sell" else SDKSide.BUY

            # Price and quantity in wei (18 decimals)
            # API requires: amount % 1e13 == 0 AND maker*price == taker (exact match)

            # Get market tick size (decimalPrecision)
            decimal_precision = market.get("decimalPrecision", 3)
            tick_size = 10 ** (-decimal_precision)  # e.g. 0.001 for precision 3

            # Round price to tick size using Decimal to avoid floating point errors
            # math.floor(0.826 / 0.001) = 825 due to floating point (0.826/0.001 = 825.999...)
            price_dec = Decimal(str(price))
            tick_dec = Decimal(str(tick_size))
            price = float((price_dec / tick_dec).to_integral_value(rounding=ROUND_DOWN) * tick_dec)

            # Convert to wei using integer math (multiply first, then convert)
            # price_wei = price * 10^18, but use Decimal for precision
            price_decimal = Decimal(str(price))
            price_wei = int(price_decimal * Decimal(WEI))

            # Round price_wei to precision (divisible by 1e15 for 3 decimal places)
            price_wei = (price_wei // PRICE_PRECISION_WEI) * PRICE_PRECISION_WEI

            # Round size down to whole number (conservative)
            size = math.floor(size)
            if size < 1:
                raise PredictAPIError("Order size must be at least 1")

            if side.lower() == "sell":
                # Selling shares for USDT
                maker_amount = int(size) * WEI  # shares in wei (exact)
                maker_amount = (maker_amount // AMOUNT_PRECISION_WEI) * AMOUNT_PRECISION_WEI
                taker_amount = (maker_amount * price_wei) // WEI
                taker_amount = (taker_amount // AMOUNT_PRECISION_WEI) * AMOUNT_PRECISION_WEI
            else:
                # Buying shares with USDT
                taker_amount = int(size) * WEI  # shares in wei (exact)
                taker_amount = (taker_amount // AMOUNT_PRECISION_WEI) * AMOUNT_PRECISION_WEI
                maker_amount = (taker_amount * price_wei) // WEI
                maker_amount = (maker_amount // AMOUNT_PRECISION_WEI) * AMOUNT_PRECISION_WEI

            # Ensure we have valid amounts
            if maker_amount < AMOUNT_PRECISION_WEI or taker_amount < AMOUNT_PRECISION_WEI:
                raise PredictAPIError("Order amount too small (below precision threshold)")

            # Step 2: Build order
            # For Predict Account: maker and signer = predictAccount (deposit address)
            # For EOA: maker and signer = wallet address
            order_input = BuildOrderInput(
                side=sdk_side,
                token_id=str(token_id),
                maker_amount=maker_amount,
                taker_amount=taker_amount,
                fee_rate_bps=fee_rate_bps,
                maker=self._address,  # predictAccount or EOA address
                signer=self._address,  # predictAccount or EOA address
            )

            order = await asyncio.to_thread(
                self._order_builder.build_order,
                "LIMIT",
                order_input
            )

            # Step 3: Build typed data (keyword-only arguments)
            typed_data = await asyncio.to_thread(
                lambda: self._order_builder.build_typed_data(
                    order,
                    is_neg_risk=is_neg_risk,
                    is_yield_bearing=is_yield_bearing
                )
            )

            # Step 4: Sign order - returns SignedOrder with hash and signature
            signed_order = await asyncio.to_thread(
                self._order_builder.sign_typed_data_order,
                typed_data
            )

            # Step 5: Get order hash - use build_typed_data_hash as fallback
            order_hash = signed_order.hash
            if order_hash is None:
                order_hash = await asyncio.to_thread(
                    self._order_builder.build_typed_data_hash,
                    typed_data
                )

            if order_hash is None:
                raise PredictAPIError("Failed to compute order hash")

            order_hash_hex = order_hash if isinstance(order_hash, str) else order_hash.hex()

            # Step 6: Submit to API
            # Format per API docs: data.order with numeric side/signatureType
            # side: 0=BUY, 1=SELL
            # signatureType: 0=EOA, 1=POLY_PROXY, 2=POLY_GNOSIS_SAFE
            side_num = 1 if side.lower() == "sell" else 0
            sig_type = order.signature_type.value if hasattr(order.signature_type, 'value') else int(order.signature_type)

            # Use the original price_wei (already rounded to 3 decimals)
            order_data = {
                "data": {
                    "pricePerShare": str(price_wei),
                    "strategy": "LIMIT",
                    "order": {
                        "hash": order_hash_hex,
                        "salt": str(order.salt),
                        "maker": order.maker,
                        "signer": order.signer,
                        "taker": order.taker,
                        "tokenId": str(order.token_id),
                        "makerAmount": str(order.maker_amount),
                        "takerAmount": str(order.taker_amount),
                        "expiration": str(order.expiration),
                        "nonce": str(order.nonce),
                        "feeRateBps": str(order.fee_rate_bps),
                        "side": side_num,
                        "signatureType": sig_type,
                        "signature": signed_order.signature,
                    },
                }
            }

            logger.info(f"Order data: maker={order_data['data']['order']['maker'][:12]}... token={order_data['data']['order']['tokenId'][:20]}...")
            logger.debug(f"Full order payload: {order_data}")

            result = await self._request(
                "POST",
                "/orders",
                data=order_data,
                require_auth=True
            )

            # API returns: {'success': True, 'data': {'code': 'OK', 'orderId': '...', 'orderHash': '...'}}
            data = result.get("data", {})
            order_id = data.get("orderId", data.get("id", result.get("orderId", "")))

            if order_id:
                logger.success(f"Order created: {order_id}")
                return str(order_id)

            # Check if success but no orderId (shouldn't happen)
            if result.get("success"):
                order_hash = data.get("orderHash", "")
                logger.success(f"Order created (hash: {order_hash[:16]}...)")
                return order_hash or "success"

            logger.warning(f"Order response: {result}")
            return None

        except PredictAPIError as e:
            logger.error(f"Order creation failed: {e}")
            raise
        except Exception as e:
            logger.error(f"Order creation error: {e}")
            raise PredictAPIError(str(e))

    async def cancel_orders(self, order_hashes: List[str]) -> bool:
        """
        POST /orders/cancel
        Remove orders from the orderbook.

        Args:
            order_hashes: List of order hashes to cancel
        """
        if not order_hashes:
            return True

        try:
            # Try different payload formats
            await self._request(
                "POST",
                "/orders/cancel",
                data={"data": {"orderHashes": order_hashes}},
                require_auth=True
            )
            logger.info(f"Cancelled {len(order_hashes)} orders")
            return True
        except PredictAPIError as e:
            logger.error(f"Cancel failed: {e}")
            return False

    async def cancel_order_by_data(self, order_data: Dict, is_yield_bearing: bool = True, is_neg_risk: bool = False) -> bool:
        """
        Cancel order using SDK with full order data.

        Args:
            order_data: Full order data from API (with 'order' nested object)
            is_yield_bearing: Market type flag
            is_neg_risk: Market type flag
        """
        if not order_data:
            return False

        try:
            if self._order_builder:
                from predict_sdk import CancelOrdersOptions
                from predict_sdk.types import Order as SDKOrder, Side, SignatureType

                # Extract order object from API response
                order_obj = order_data.get("order", order_data)

                # Create SDK Order from API data
                sdk_order = SDKOrder(
                    salt=str(order_obj.get("salt", "")),
                    maker=order_obj.get("maker", ""),
                    signer=order_obj.get("signer", ""),
                    taker=order_obj.get("taker", "0x0000000000000000000000000000000000000000"),
                    token_id=str(order_obj.get("tokenId", "")),
                    maker_amount=str(order_obj.get("makerAmount", "")),
                    taker_amount=str(order_obj.get("takerAmount", "")),
                    expiration=str(order_obj.get("expiration", "")),
                    nonce=str(order_obj.get("nonce", "0")),
                    fee_rate_bps=str(order_obj.get("feeRateBps", "200")),
                    side=Side.SELL if order_obj.get("side") == 1 else Side.BUY,
                    signature_type=SignatureType(order_obj.get("signatureType", 0))
                )

                options = CancelOrdersOptions(
                    is_yield_bearing=is_yield_bearing,
                    is_neg_risk=is_neg_risk
                )

                result = await asyncio.to_thread(
                    self._order_builder.cancel_orders,
                    [sdk_order],
                    options
                )

                order_hash = order_obj.get("hash", "")[:16]
                logger.info(f"Cancelled order {order_hash}... via SDK")
                return True

        except Exception as e:
            logger.error(f"SDK cancel failed: {e}")
            return False

        return False

    async def cancel_order(self, order_hash_or_id: str) -> bool:
        """
        Cancel a single order by hash or ID using SDK.

        Fetches order data first, then uses SDK for on-chain cancel.
        """
        if not order_hash_or_id:
            return False

        try:
            # First, get all orders and find the one we need
            all_orders = await self.get_orders()

            target_order = None
            for order in all_orders:
                if order.order_hash == order_hash_or_id or order.order_id == order_hash_or_id:
                    target_order = order
                    break

            if not target_order or not target_order.raw_data:
                logger.warning(f"Order not found or no raw_data: {order_hash_or_id[:16]}...")
                return False

            # Use SDK cancel
            is_neg_risk = target_order.raw_data.get("isNegRisk", False)
            is_yield_bearing = target_order.raw_data.get("isYieldBearing", False)

            return await self.cancel_order_by_data(
                target_order.raw_data,
                is_yield_bearing=is_yield_bearing,
                is_neg_risk=is_neg_risk
            )

        except Exception as e:
            logger.error(f"Cancel order failed: {e}")
            return False

    async def cancel_all_orders(self, market_id: Optional[str] = None) -> int:
        """Cancel all open orders using SDK"""
        orders = await self.get_orders(market_id=market_id, status="open")

        if not orders:
            return 0

        cancelled = 0
        for order in orders:
            if order.raw_data:
                try:
                    is_neg_risk = order.raw_data.get("isNegRisk", False)
                    is_yield_bearing = order.raw_data.get("isYieldBearing", False)

                    result = await self.cancel_order_by_data(
                        order.raw_data,
                        is_yield_bearing=is_yield_bearing,
                        is_neg_risk=is_neg_risk
                    )
                    if result:
                        cancelled += 1
                except Exception as e:
                    logger.debug(f"Cancel failed: {e}")

        return cancelled
