"""
Multicall3 для пакетного чтения балансов на BSC.
Один RPC-вызов вместо 2*N вызовов для N кошельков.
"""
import asyncio
from typing import List, Dict, Tuple, Optional
from loguru import logger

from constants import MULTICALL3_ADDRESS, WEI


# Multicall3 ABI (only aggregate3 function)
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"name": "target", "type": "address"},
                    {"name": "allowFailure", "type": "bool"},
                    {"name": "callData", "type": "bytes"}
                ],
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"name": "success", "type": "bool"},
                    {"name": "returnData", "type": "bytes"}
                ],
                "name": "returnData",
                "type": "tuple[]"
            }
        ],
        "stateMutability": "payable",
        "type": "function"
    },
    {
        "inputs": [
            {"name": "addr", "type": "address"}
        ],
        "name": "getEthBalance",
        "outputs": [
            {"name": "balance", "type": "uint256"}
        ],
        "stateMutability": "view",
        "type": "function"
    }
]

# ERC20 balanceOf selector
BALANCE_OF_SELECTOR = "0x70a08231"  # balanceOf(address)


class MulticallReader:
    """Пакетное чтение балансов через Multicall3"""

    def __init__(self, web3, usdt_address: str):
        self._web3 = web3
        self._usdt_address = usdt_address
        self._multicall = web3.eth.contract(
            address=web3.to_checksum_address(MULTICALL3_ADDRESS),
            abi=MULTICALL3_ABI
        )

    async def get_all_balances(
        self,
        addresses: List[str]
    ) -> Dict[str, Tuple[float, float]]:
        """
        Получить BNB и USDT балансы для всех адресов одним вызовом.

        Args:
            addresses: Список адресов кошельков

        Returns:
            Dict[address] = (bnb_balance, usdt_balance)
        """
        if not addresses:
            return {}

        try:
            calls = []

            for addr in addresses:
                checksum = self._web3.to_checksum_address(addr)

                # BNB balance via getEthBalance
                bnb_call = self._multicall.functions.getEthBalance(checksum)
                calls.append({
                    "target": MULTICALL3_ADDRESS,
                    "allowFailure": True,
                    "callData": bnb_call._encode_transaction_data()
                })

                # USDT balance via balanceOf
                # Encode: balanceOf(address)
                padded_addr = addr.lower().replace("0x", "").zfill(64)
                call_data = BALANCE_OF_SELECTOR + padded_addr
                calls.append({
                    "target": self._web3.to_checksum_address(self._usdt_address),
                    "allowFailure": True,
                    "callData": bytes.fromhex(call_data[2:]) if call_data.startswith("0x") else bytes.fromhex(call_data)
                })

            # Execute multicall
            result = await asyncio.to_thread(
                self._multicall.functions.aggregate3(calls).call
            )

            # Parse results
            balances = {}
            for i, addr in enumerate(addresses):
                bnb_result = result[i * 2]
                usdt_result = result[i * 2 + 1]

                bnb_balance = 0.0
                if bnb_result[0]:  # success
                    bnb_wei = int.from_bytes(bnb_result[1], 'big')
                    bnb_balance = bnb_wei / WEI

                usdt_balance = 0.0
                if usdt_result[0]:  # success
                    usdt_wei = int.from_bytes(usdt_result[1], 'big')
                    usdt_balance = usdt_wei / WEI

                balances[addr] = (bnb_balance, usdt_balance)

            return balances

        except Exception as e:
            logger.error(f"Multicall failed: {e}")
            # Fallback: return empty (caller should handle)
            return {}
