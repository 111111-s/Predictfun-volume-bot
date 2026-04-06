"""
Predict.fun - On-chain балансы (USDT, BNB)
"""

import aiohttp
import asyncio
from loguru import logger

from ..constants import WEI


class BalancesMixin:
    """Миксин для получения балансов"""

    async def get_usdt_balance(self) -> float:
        """Get USDT balance from on-chain (more reliable)"""
        try:
            if not self._order_builder:
                return 0.0

            # Use SDK's balanceOf method
            balance_wei = await asyncio.to_thread(
                self._order_builder.balance_of
            )
            return float(balance_wei) / WEI
        except Exception as e:
            logger.debug(f"SDK balance failed: {e}, trying API...")
            try:
                account = await self.get_account()
                balance = account.get("balance", account.get("usdtBalance", 0))
                return float(balance)
            except:
                return 0.0

    async def get_bnb_balance(self) -> float:
        """Get BNB balance of signer wallet (for gas)"""
        try:
            if not self._order_builder:
                return 0.0

            contracts = self._order_builder.contracts
            web3 = contracts.usdt.w3

            balance_wei = await asyncio.to_thread(
                web3.eth.get_balance, self._signer_address
            )
            return float(balance_wei) / WEI
        except Exception as e:
            logger.debug(f"Failed to get BNB balance: {e}")
            return 0.0

    async def get_bnb_price_usd(self) -> float:
        """Get current BNB price in USD from Binance API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT",
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data.get("price", 0))
        except Exception:
            pass
        return 700.0  # Fallback price

    async def get_bnb_balance_usd(self) -> tuple:
        """Get BNB balance and its USD value"""
        bnb = await self.get_bnb_balance()
        price = await self.get_bnb_price_usd()
        return bnb, bnb * price
