"""
Predict.fun - On-chain операции (split, merge, redeem, approvals)

Использует predict-sdk для взаимодействия с контрактами на BNB Chain.
"""

import asyncio
from typing import Optional
from loguru import logger

from config import config
from ..constants import WEI, MAX_APPROVAL
from .gas import GasManager, NonceManager


class ChainOperationsMixin:
    """Миксин для on-chain операций (split, merge, redeem, buy/sell shares)"""

    def _get_gas_manager(self) -> GasManager:
        """Получить или создать GasManager"""
        if not hasattr(self, '_gas_manager') or self._gas_manager is None:
            contracts = self._order_builder.contracts
            web3 = contracts.usdt.w3
            self._gas_manager = GasManager(web3, config.gas)
        return self._gas_manager

    def _get_nonce_manager(self) -> NonceManager:
        """Получить или создать NonceManager"""
        if not hasattr(self, '_nonce_manager') or self._nonce_manager is None:
            contracts = self._order_builder.contracts
            web3 = contracts.usdt.w3
            self._nonce_manager = NonceManager(web3, self._signer_address)
        return self._nonce_manager

    # ==========================================
    # TRADING HELPERS
    # ==========================================

    async def buy_shares(
        self,
        market_id: str,
        outcome: str,
        amount_usdt: float,
        max_price: float = 0.99
    ) -> Optional[str]:
        """
        Buy shares by placing a buy order.

        Args:
            market_id: Market ID
            outcome: "yes" or "no"
            amount_usdt: Amount of USDT to spend
            max_price: Maximum price to pay

        Returns:
            Order hash if successful
        """
        # Get current orderbook to find best price
        book = await self.get_market_orderbook(market_id, outcome)

        # Calculate shares based on best ask
        price = min(book.best_ask, max_price)
        shares = amount_usdt / price if price > 0 else 0

        if shares <= 0:
            return None

        return await self.create_order(
            market_id=market_id,
            outcome=outcome,
            side="buy",
            price=price,
            size=shares
        )

    async def sell_shares(
        self,
        market_id: str,
        outcome: str,
        shares: float,
        min_price: float = 0.01
    ) -> Optional[str]:
        """
        Sell shares by placing a sell order.

        Args:
            market_id: Market ID
            outcome: "yes" or "no"
            shares: Number of shares to sell
            min_price: Minimum price to accept

        Returns:
            Order hash if successful
        """
        # Get current orderbook to find best price
        book = await self.get_market_orderbook(market_id, outcome)

        # Set price just below best ask (to be first in queue)
        price_step = 0.001
        price = max(book.best_ask - price_step, book.best_bid + price_step, min_price)

        return await self.create_order(
            market_id=market_id,
            outcome=outcome,
            side="sell",
            price=price,
            size=shares
        )

    # ==========================================
    # SDK ON-CHAIN OPERATIONS
    # ==========================================

    async def merge_positions(
        self,
        condition_id: str,
        amount: float,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False
    ) -> bool:
        """
        Merge YES + NO tokens back to USDT.

        Uses predict-sdk for on-chain transaction.

        Args:
            condition_id: Market condition ID
            amount: Amount to merge (in tokens)
            is_neg_risk: Whether market uses neg risk
            is_yield_bearing: Whether market uses yield bearing tokens

        Returns:
            True if successful
        """
        if not self._order_builder:
            logger.error("SDK not initialized, cannot merge")
            return False

        try:
            # Convert to wei
            amount_wei = int(amount * WEI)

            logger.info(f"Merging {amount:.2f} tokens (condition: {condition_id[:12]}...)")

            result = await asyncio.to_thread(
                self._order_builder.merge_positions,
                condition_id,
                amount_wei,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing
            )

            # Check result - SDK returns TransactionResult with success attribute
            if hasattr(result, 'success'):
                if result.success:
                    receipt = getattr(result, 'receipt', None)
                    tx_hash = receipt.get('transactionHash', '') if receipt else ''
                    if tx_hash:
                        tx_hex = tx_hash.hex() if hasattr(tx_hash, 'hex') else str(tx_hash)
                        logger.success(f"Merge successful: {tx_hex[:16]}...")
                    else:
                        logger.success("Merge successful!")
                    return True
                else:
                    cause = getattr(result, 'cause', result)
                    logger.error(f"Merge failed: {cause}")
                    return False
            elif hasattr(result, 'tx_hash') and result.tx_hash:
                logger.success(f"Merge successful: {result.tx_hash[:16]}...")
                return True

            logger.error(f"Merge failed: {result}")
            return False

        except Exception as e:
            logger.error(f"Merge failed: {e}")
            return False

    async def redeem_positions(
        self,
        condition_id: str,
        index_set: int = 1,
        amount: Optional[float] = None,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False
    ) -> bool:
        """
        Redeem resolved positions.

        Args:
            condition_id: Market condition ID
            index_set: 1 for YES, 2 for NO
            amount: Amount to redeem (None for all)
            is_neg_risk: Whether market uses neg risk
            is_yield_bearing: Whether market uses yield bearing tokens

        Returns:
            True if successful
        """
        if not self._order_builder:
            logger.error("SDK not initialized, cannot redeem")
            return False

        try:
            amount_wei = int(amount * WEI) if amount else None

            logger.info(f"Redeeming positions (condition: {condition_id[:12]}...)")

            result = await asyncio.to_thread(
                self._order_builder.redeem_positions,
                condition_id,
                index_set,
                amount_wei,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing
            )

            if hasattr(result, 'tx_hash'):
                logger.success(f"Redeem successful: {result.tx_hash[:16]}...")
                return True

            logger.error(f"Redeem failed: {result}")
            return False

        except Exception as e:
            logger.error(f"Redeem failed: {e}")
            return False

    async def set_approvals(
        self,
        is_yield_bearing: bool = False
    ) -> bool:
        """
        Set necessary token approvals for trading AND splitting.

        Approves USDT for:
        1. CTF Exchange (for orders) - via SDK set_approvals
        2. Conditional Tokens (for split) - manual approval

        Optimization: проверяет существующий allowance перед апрувом.
        Should be called once per wallet before trading.

        Returns:
            True if successful
        """
        if not self._order_builder:
            logger.error("SDK not initialized, cannot set approvals")
            return False

        try:
            # Step 1: SDK approvals (for orders/exchange)
            logger.info("Setting exchange approvals...")

            result = await asyncio.to_thread(
                self._order_builder.set_approvals,
                is_yield_bearing=is_yield_bearing
            )

            logger.success("Exchange approvals set")

            # Step 2: Approve USDT for ALL Conditional Tokens contracts (for split)
            logger.info("Setting split approvals (Conditional Tokens)...")

            contracts = self._order_builder.contracts
            usdt = contracts.usdt
            web3 = usdt.w3  # Get web3 from contract

            gas_mgr = self._get_gas_manager()
            nonce_mgr = self._get_nonce_manager()
            # Сбрасываем nonce перед серией транзакций
            await nonce_mgr.reset()

            # Approve ALL conditional tokens contracts (regular + yield bearing)
            ct_contracts = [
                ("Conditional Tokens", contracts.conditional_tokens),
                ("Yield Bearing CT", contracts.yield_bearing_conditional_tokens),
            ]

            for ct_name, ct_contract in ct_contracts:
                try:
                    # Проверяем существующий allowance перед апрувом
                    existing_allowance = await asyncio.to_thread(
                        usdt.functions.allowance(self._signer_address, ct_contract.address).call
                    )

                    if existing_allowance > 0:
                        logger.info(f"{ct_name} already approved (allowance: {existing_allowance}), skipping")
                        continue

                    # Получаем nonce и gas params
                    nonce = await nonce_mgr.get_nonce()
                    gas_params = await gas_mgr.get_gas_params()

                    # Build approve transaction
                    tx_data = usdt.functions.approve(
                        ct_contract.address, MAX_APPROVAL
                    ).build_transaction({
                        'from': self._signer_address,
                        'nonce': nonce,
                        'gas': 100000,
                        **gas_params,
                    })

                    # Estimate gas с запасом
                    gas_limit = await gas_mgr.estimate_gas_limit(tx_data)
                    tx_data['gas'] = gas_limit

                    # Sign and send
                    signed_tx = self._account.sign_transaction(tx_data)
                    tx_hash = await asyncio.to_thread(
                        web3.eth.send_raw_transaction, signed_tx.raw_transaction
                    )

                    # Wait for receipt
                    receipt = await asyncio.to_thread(
                        web3.eth.wait_for_transaction_receipt, tx_hash, timeout=60
                    )

                    if receipt.status == 1:
                        logger.success(f"{ct_name} approved: {tx_hash.hex()[:16]}...")
                    else:
                        logger.error(f"{ct_name} approval failed")

                except Exception as e:
                    logger.warning(f"{ct_name} approval error: {e}")

            return True

        except Exception as e:
            logger.error(f"Failed to set approvals: {e}")
            return False

    async def split_position(
        self,
        condition_id: str,
        amount: float,
        is_neg_risk: bool = False,
        is_yield_bearing: bool = False
    ) -> bool:
        """
        Split USDT into YES + NO tokens.

        Uses predict-sdk's split_positions method (added in v0.0.10).

        Args:
            condition_id: Market condition ID (bytes32 as hex string)
            amount: Amount of USDT to split (will get equal YES and NO)
            is_neg_risk: Whether market uses neg risk
            is_yield_bearing: Whether market uses yield bearing tokens

        Returns:
            True if successful
        """
        if not self._order_builder:
            logger.error("SDK not initialized, cannot split")
            return False

        try:
            # Convert amount to USDT decimals (18 decimals on BSC!)
            amount_wei = int(amount * WEI)

            logger.info(f"Splitting {amount:.2f} USDT -> YES + NO (condition: {condition_id[:12]}...)")

            # Use SDK's split_positions method (async version)
            result = await asyncio.to_thread(
                self._order_builder.split_positions,
                condition_id,
                amount_wei,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing
            )

            # Check result - SDK returns TransactionResult with success attribute
            if hasattr(result, 'success'):
                if result.success:
                    tx_hash = getattr(result, 'tx_hash', None)
                    if tx_hash:
                        logger.success(f"Split successful: {tx_hash[:16]}...")
                    else:
                        logger.success(f"Split successful!")
                    return True
                else:
                    # TransactionFail - extract error cause
                    cause = getattr(result, 'cause', result)
                    logger.error(f"Split failed: {cause}")
                    return False
            elif hasattr(result, 'tx_hash') and result.tx_hash:
                logger.success(f"Split successful: {result.tx_hash[:16]}...")
                return True

            logger.error(f"Split failed: {result}")
            return False

        except Exception as e:
            logger.error(f"Split failed: {e}")
            return False

    async def split_position_simple(
        self,
        market_id: str,
        amount: float
    ) -> bool:
        """
        Split USDT into YES + NO tokens (simple version).

        Automatically fetches market info and determines correct contract.

        Args:
            market_id: Market ID
            amount: Amount of USDT to split

        Returns:
            True if successful
        """
        try:
            # Get market info to determine condition_id and market type
            market = await self.get_market(market_id)
            if not market:
                logger.error(f"Market not found: {market_id}")
                return False

            condition_id = market.get("conditionId", "")
            if not condition_id:
                logger.error("No condition ID in market data")
                return False

            # Determine market type
            is_neg_risk = market.get("isNegRisk", market.get("negRisk", False))
            is_yield_bearing = market.get("isYieldBearing", market.get("yieldBearing", False))

            return await self.split_position(
                condition_id=condition_id,
                amount=amount,
                is_neg_risk=is_neg_risk,
                is_yield_bearing=is_yield_bearing
            )

        except Exception as e:
            logger.error(f"Split simple failed: {e}")
            return False
