"""
Gas & Nonce management для on-chain транзакций (BNB Chain)
"""

import asyncio
from typing import Optional, Dict
from loguru import logger

from config import GasConfig


class NonceManager:
    """
    Потокобезопасный менеджер nonce.

    Получает nonce из сети при первом вызове,
    затем инкрементирует локально для последовательных транзакций.
    """

    def __init__(self, web3, address: str):
        self._web3 = web3
        self._address = address
        self._lock = asyncio.Lock()
        self._nonce: Optional[int] = None

    async def get_nonce(self) -> int:
        async with self._lock:
            if self._nonce is None:
                self._nonce = await asyncio.to_thread(
                    self._web3.eth.get_transaction_count, self._address
                )
            else:
                self._nonce += 1
            return self._nonce

    async def reset(self):
        async with self._lock:
            self._nonce = None


class GasManager:
    """
    Менеджер газа для транзакций.

    Поддерживает EIP-1559 (maxFeePerGas, maxPriorityFeePerGas)
    с fallback на legacy gasPrice.
    """

    def __init__(self, web3, gas_config: GasConfig):
        self._web3 = web3
        self._gas_config = gas_config

    async def get_gas_params(self) -> Dict:
        """
        Return EIP-1559 params: maxFeePerGas, maxPriorityFeePerGas.
        Fallback to legacy gasPrice if EIP-1559 not supported.
        """
        try:
            # Пробуем EIP-1559
            base_fee = await asyncio.to_thread(
                lambda: self._web3.eth.get_block('latest').get('baseFeePerGas')
            )

            if base_fee is not None:
                # EIP-1559 поддерживается
                max_priority = self._web3.to_wei(1, 'gwei')  # 1 gwei tip
                max_fee = int(base_fee * self._gas_config.gwei_multiplier) + max_priority

                # Лимит по конфигу
                max_gas_wei = self._web3.to_wei(
                    self._gas_config.max_gas_price_gwei, 'gwei'
                )
                max_fee = min(max_fee, max_gas_wei)

                return {
                    'maxFeePerGas': max_fee,
                    'maxPriorityFeePerGas': max_priority,
                }
        except Exception as e:
            logger.debug(f"EIP-1559 not available, falling back to legacy: {e}")

        # Fallback: legacy gasPrice
        gas_price = await asyncio.to_thread(
            lambda: self._web3.eth.gas_price
        )
        gas_price = int(gas_price * self._gas_config.gwei_multiplier)

        max_gas_wei = self._web3.to_wei(
            self._gas_config.max_gas_price_gwei, 'gwei'
        )
        gas_price = min(gas_price, max_gas_wei)

        return {
            'gasPrice': gas_price,
        }

    async def estimate_gas_limit(self, tx_data: dict) -> int:
        """Estimate gas with 20% safety margin"""
        try:
            estimated = await asyncio.to_thread(
                self._web3.eth.estimate_gas, tx_data
            )
            # 20% запас
            return int(estimated * 1.2)
        except Exception as e:
            logger.debug(f"Gas estimation failed, using default 100000: {e}")
            return 100000
