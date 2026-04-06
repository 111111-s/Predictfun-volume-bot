"""
Распределение и сбор средств между кошельками

FundDistributor управляет:
  - Рассылка BNB / USDT с мастер-кошелька на дочерние
  - Сбор BNB / USDT с дочерних на мастер-кошелёк
  - Автоматическая дозаправка (refuel) кошельков с низким балансом BNB

Работает через web3.py, использует EIP-1559 газ (если поддерживается).
"""

import time
from typing import Dict, List, Optional

from loguru import logger
from eth_account import Account

from constants import WEI, DEFAULT_GAS_LIMIT
from wallet.models import WalletInfo


# Стандартный ERC20 ABI для transfer и balanceOf
ERC20_TRANSFER_ABI = [
    {
        "constant": False,
        "inputs": [
            {"name": "_to", "type": "address"},
            {"name": "_value", "type": "uint256"},
        ],
        "name": "transfer",
        "outputs": [{"name": "", "type": "bool"}],
        "type": "function",
    },
    {
        "constant": True,
        "inputs": [{"name": "_owner", "type": "address"}],
        "name": "balanceOf",
        "outputs": [{"name": "balance", "type": "uint256"}],
        "type": "function",
    },
]

# Gas limit для ERC20 transfer (USDT на BSC)
ERC20_GAS_LIMIT = 60_000

# Gas limit для простого перевода BNB
BNB_TRANSFER_GAS = 21_000


class FundDistributor:
    """
    Распределение и сбор средств (BNB и USDT) между кошельками.

    Принимает web3 инстанс (уже подключённый к BSC RPC).
    Использует EIP-1559 газ, если доступен, иначе — legacy gas price.
    """

    def __init__(self, web3):
        """
        Args:
            web3: Экземпляр Web3, подключённый к BSC
        """
        self.w3 = web3

    # ------------------------------------------------------------------
    # Газ
    # ------------------------------------------------------------------

    def _get_gas_params(self) -> dict:
        """
        Получение параметров газа.

        Пытается использовать EIP-1559 (maxFeePerGas / maxPriorityFeePerGas),
        если BSC-нода поддерживает. Иначе — legacy gasPrice.
        """
        try:
            # EIP-1559: base_fee + tip
            latest = self.w3.eth.get_block("latest")
            base_fee = latest.get("baseFeePerGas")
            if base_fee is not None:
                # Приоритет: 1 gwei (минимальный для BSC)
                priority_fee = self.w3.to_wei(1, "gwei")
                max_fee = base_fee * 2 + priority_fee
                return {
                    "maxFeePerGas": max_fee,
                    "maxPriorityFeePerGas": priority_fee,
                }
        except Exception:
            pass

        # Fallback: legacy gas price
        gas_price = self.w3.eth.gas_price
        return {"gasPrice": gas_price}

    # ------------------------------------------------------------------
    # Nonce
    # ------------------------------------------------------------------

    def _get_nonce(self, address: str) -> int:
        """Получение текущего nonce для адреса"""
        return self.w3.eth.get_transaction_count(address, "pending")

    # ------------------------------------------------------------------
    # Отправка транзакции
    # ------------------------------------------------------------------

    def _send_tx(self, tx: dict, private_key: str) -> Optional[str]:
        """
        Подпись и отправка транзакции.

        Args:
            tx: Словарь транзакции (to, value, gas, nonce, chainId, ...)
            private_key: Приватный ключ отправителя

        Returns:
            Хэш транзакции (hex-строка) или None при ошибке
        """
        try:
            signed = self.w3.eth.account.sign_transaction(tx, private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.raw_transaction)
            return tx_hash.hex()
        except Exception as e:
            logger.error(f"Ошибка отправки транзакции: {e}")
            return None

    def _wait_receipt(self, tx_hash: str, timeout: int = 60) -> bool:
        """
        Ожидание receipt транзакции.

        Returns:
            True если транзакция успешна (status == 1)
        """
        try:
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=timeout)
            return receipt.get("status") == 1
        except Exception as e:
            logger.warning(f"Таймаут ожидания receipt для {tx_hash[:16]}...: {e}")
            return False

    # ------------------------------------------------------------------
    # Распределение BNB
    # ------------------------------------------------------------------

    def distribute_bnb(
        self,
        master: WalletInfo,
        targets: List[WalletInfo],
        amount_each: float,
    ) -> Dict[str, str]:
        """
        Отправка BNB с мастер-кошелька на целевые кошельки.

        Args:
            master: Мастер-кошелёк (отправитель)
            targets: Список целевых кошельков
            amount_each: Сумма BNB для каждого кошелька

        Returns:
            Словарь {адрес: tx_hash} для успешных переводов
        """
        results: Dict[str, str] = {}
        amount_wei = int(amount_each * WEI)
        gas_params = self._get_gas_params()
        chain_id = self.w3.eth.chain_id

        # Проверка баланса мастера
        master_balance = self.w3.eth.get_balance(master.address)
        total_needed = amount_wei * len(targets) + BNB_TRANSFER_GAS * len(targets) * self.w3.eth.gas_price
        if master_balance < total_needed:
            logger.error(
                f"Недостаточно BNB на мастере: {master_balance / WEI:.6f} BNB, "
                f"нужно ~{total_needed / WEI:.6f} BNB"
            )
            return results

        # Последовательная отправка с инкрементом nonce
        nonce = self._get_nonce(master.address)

        for target in targets:
            tx = {
                "from": master.address,
                "to": target.address,
                "value": amount_wei,
                "gas": BNB_TRANSFER_GAS,
                "nonce": nonce,
                "chainId": chain_id,
                **gas_params,
            }

            tx_hash = self._send_tx(tx, master.private_key)
            if tx_hash:
                results[target.address] = tx_hash
                logger.success(
                    f"BNB: {master.short_address} -> {target.short_address} "
                    f"({amount_each:.6f} BNB) tx={tx_hash[:16]}..."
                )
                nonce += 1
            else:
                logger.error(
                    f"BNB: Не удалось отправить на {target.short_address}"
                )

        logger.info(f"Распределение BNB завершено: {len(results)}/{len(targets)} успешно")
        return results

    # ------------------------------------------------------------------
    # Распределение USDT
    # ------------------------------------------------------------------

    def distribute_usdt(
        self,
        master: WalletInfo,
        targets: List[WalletInfo],
        amount_each: float,
        usdt_contract,
    ) -> Dict[str, str]:
        """
        Отправка USDT с мастер-кошелька на целевые кошельки.

        Args:
            master: Мастер-кошелёк (отправитель)
            targets: Список целевых кошельков
            amount_each: Сумма USDT для каждого кошелька
            usdt_contract: Адрес USDT-контракта (str) или web3 Contract объект

        Returns:
            Словарь {адрес: tx_hash} для успешных переводов
        """
        results: Dict[str, str] = {}

        # Если передали адрес — создаём контракт
        if isinstance(usdt_contract, str):
            contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(usdt_contract),
                abi=ERC20_TRANSFER_ABI,
            )
        else:
            contract = usdt_contract

        amount_wei = int(amount_each * WEI)
        gas_params = self._get_gas_params()
        chain_id = self.w3.eth.chain_id

        # Проверка баланса USDT мастера
        master_usdt = contract.functions.balanceOf(master.address).call()
        total_needed = amount_wei * len(targets)
        if master_usdt < total_needed:
            logger.error(
                f"Недостаточно USDT на мастере: {master_usdt / WEI:.2f}, "
                f"нужно {total_needed / WEI:.2f}"
            )
            return results

        nonce = self._get_nonce(master.address)

        for target in targets:
            try:
                tx = contract.functions.transfer(
                    target.address, amount_wei
                ).build_transaction({
                    "from": master.address,
                    "gas": ERC20_GAS_LIMIT,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **gas_params,
                })

                tx_hash = self._send_tx(tx, master.private_key)
                if tx_hash:
                    results[target.address] = tx_hash
                    logger.success(
                        f"USDT: {master.short_address} -> {target.short_address} "
                        f"(${amount_each:.2f}) tx={tx_hash[:16]}..."
                    )
                    nonce += 1
                else:
                    logger.error(
                        f"USDT: Не удалось отправить на {target.short_address}"
                    )
            except Exception as e:
                logger.error(
                    f"USDT: Ошибка перевода на {target.short_address}: {e}"
                )

        logger.info(f"Распределение USDT завершено: {len(results)}/{len(targets)} успешно")
        return results

    # ------------------------------------------------------------------
    # Сбор BNB
    # ------------------------------------------------------------------

    def collect_bnb(
        self,
        sources: List[WalletInfo],
        master: WalletInfo,
        leave_for_gas: float = 0.001,
    ) -> Dict[str, str]:
        """
        Сбор BNB с дочерних кошельков на мастер.

        Оставляет leave_for_gas BNB на каждом кошельке для будущих транзакций.

        Args:
            sources: Список кошельков-источников
            master: Мастер-кошелёк (получатель)
            leave_for_gas: Сколько BNB оставить на каждом кошельке

        Returns:
            Словарь {адрес: tx_hash} для успешных переводов
        """
        results: Dict[str, str] = {}
        gas_params = self._get_gas_params()
        chain_id = self.w3.eth.chain_id
        leave_wei = int(leave_for_gas * WEI)

        for source in sources:
            try:
                balance = self.w3.eth.get_balance(source.address)

                # Рассчитываем стоимость газа
                gas_price = gas_params.get(
                    "gasPrice",
                    gas_params.get("maxFeePerGas", self.w3.eth.gas_price),
                )
                gas_cost = BNB_TRANSFER_GAS * gas_price

                # Сумма к отправке = баланс - газ - резерв
                send_amount = balance - gas_cost - leave_wei

                if send_amount <= 0:
                    logger.debug(
                        f"Пропуск {source.short_address}: "
                        f"баланс {balance / WEI:.6f} BNB слишком мал"
                    )
                    continue

                nonce = self._get_nonce(source.address)

                tx = {
                    "from": source.address,
                    "to": master.address,
                    "value": send_amount,
                    "gas": BNB_TRANSFER_GAS,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **gas_params,
                }

                tx_hash = self._send_tx(tx, source.private_key)
                if tx_hash:
                    results[source.address] = tx_hash
                    logger.success(
                        f"Collect BNB: {source.short_address} -> {master.short_address} "
                        f"({send_amount / WEI:.6f} BNB) tx={tx_hash[:16]}..."
                    )
                else:
                    logger.error(f"Collect BNB: Ошибка для {source.short_address}")

            except Exception as e:
                logger.error(f"Collect BNB: Ошибка для {source.short_address}: {e}")

        logger.info(f"Сбор BNB завершён: {len(results)}/{len(sources)} успешно")
        return results

    # ------------------------------------------------------------------
    # Сбор USDT
    # ------------------------------------------------------------------

    def collect_usdt(
        self,
        sources: List[WalletInfo],
        master: WalletInfo,
        usdt_contract,
    ) -> Dict[str, str]:
        """
        Сбор USDT с дочерних кошельков на мастер.

        Переводит весь баланс USDT с каждого кошелька.

        Args:
            sources: Список кошельков-источников
            master: Мастер-кошелёк (получатель)
            usdt_contract: Адрес USDT-контракта (str) или web3 Contract объект

        Returns:
            Словарь {адрес: tx_hash} для успешных переводов
        """
        results: Dict[str, str] = {}

        # Если передали адрес — создаём контракт
        if isinstance(usdt_contract, str):
            contract = self.w3.eth.contract(
                address=self.w3.to_checksum_address(usdt_contract),
                abi=ERC20_TRANSFER_ABI,
            )
        else:
            contract = usdt_contract

        gas_params = self._get_gas_params()
        chain_id = self.w3.eth.chain_id

        for source in sources:
            try:
                usdt_balance = contract.functions.balanceOf(source.address).call()

                if usdt_balance <= 0:
                    logger.debug(
                        f"Пропуск {source.short_address}: баланс USDT = 0"
                    )
                    continue

                nonce = self._get_nonce(source.address)

                tx = contract.functions.transfer(
                    master.address, usdt_balance
                ).build_transaction({
                    "from": source.address,
                    "gas": ERC20_GAS_LIMIT,
                    "nonce": nonce,
                    "chainId": chain_id,
                    **gas_params,
                })

                tx_hash = self._send_tx(tx, source.private_key)
                if tx_hash:
                    results[source.address] = tx_hash
                    logger.success(
                        f"Collect USDT: {source.short_address} -> {master.short_address} "
                        f"(${usdt_balance / WEI:.2f}) tx={tx_hash[:16]}..."
                    )
                else:
                    logger.error(f"Collect USDT: Ошибка для {source.short_address}")

            except Exception as e:
                logger.error(f"Collect USDT: Ошибка для {source.short_address}: {e}")

        logger.info(f"Сбор USDT завершён: {len(results)}/{len(sources)} успешно")
        return results

    # ------------------------------------------------------------------
    # Авто-дозаправка (refuel)
    # ------------------------------------------------------------------

    def auto_refuel(
        self,
        master: WalletInfo,
        targets: List[WalletInfo],
        min_bnb: float,
        refuel_amount: float,
    ) -> List[str]:
        """
        Автоматическое пополнение кошельков с низким балансом BNB.

        Проверяет баланс BNB каждого целевого кошелька.
        Если баланс ниже min_bnb — отправляет refuel_amount BNB с мастера.

        Args:
            master: Мастер-кошелёк (отправитель)
            targets: Список кошельков для проверки
            min_bnb: Минимальный порог BNB (ниже — нужна дозаправка)
            refuel_amount: Сколько BNB отправить при дозаправке

        Returns:
            Список адресов, которые были пополнены
        """
        refueled: List[str] = []
        min_wei = int(min_bnb * WEI)
        amount_wei = int(refuel_amount * WEI)
        gas_params = self._get_gas_params()
        chain_id = self.w3.eth.chain_id

        # Проверяем баланс мастера
        master_balance = self.w3.eth.get_balance(master.address)

        # Определяем кого нужно дозаправить
        need_refuel: List[WalletInfo] = []
        for target in targets:
            try:
                balance = self.w3.eth.get_balance(target.address)
                if balance < min_wei:
                    need_refuel.append(target)
                    logger.info(
                        f"Refuel: {target.short_address} имеет "
                        f"{balance / WEI:.6f} BNB (< {min_bnb})"
                    )
            except Exception as e:
                logger.warning(f"Refuel: Ошибка чтения баланса {target.short_address}: {e}")

        if not need_refuel:
            logger.info("Refuel: Все кошельки имеют достаточно BNB")
            return refueled

        # Проверяем хватит ли мастеру
        total_needed = amount_wei * len(need_refuel)
        gas_reserve = BNB_TRANSFER_GAS * len(need_refuel) * self.w3.eth.gas_price
        if master_balance < total_needed + gas_reserve:
            logger.warning(
                f"Refuel: Недостаточно BNB на мастере для дозаправки "
                f"{len(need_refuel)} кошельков. "
                f"Баланс: {master_balance / WEI:.6f}, "
                f"нужно: {(total_needed + gas_reserve) / WEI:.6f}"
            )

        # Отправляем
        nonce = self._get_nonce(master.address)

        for target in need_refuel:
            # Перепроверяем баланс мастера перед каждой транзакцией
            remaining = master_balance - (BNB_TRANSFER_GAS * self.w3.eth.gas_price)
            if remaining < amount_wei:
                logger.warning("Refuel: Баланс мастера исчерпан, остановка")
                break

            tx = {
                "from": master.address,
                "to": target.address,
                "value": amount_wei,
                "gas": BNB_TRANSFER_GAS,
                "nonce": nonce,
                "chainId": chain_id,
                **gas_params,
            }

            tx_hash = self._send_tx(tx, master.private_key)
            if tx_hash:
                refueled.append(target.address)
                # Уменьшаем локальный счётчик баланса
                gas_price = gas_params.get(
                    "gasPrice",
                    gas_params.get("maxFeePerGas", self.w3.eth.gas_price),
                )
                master_balance -= amount_wei + BNB_TRANSFER_GAS * gas_price
                nonce += 1
                logger.success(
                    f"Refuel: {target.short_address} пополнен на "
                    f"{refuel_amount:.6f} BNB (tx={tx_hash[:16]}...)"
                )
            else:
                logger.error(f"Refuel: Не удалось пополнить {target.short_address}")

        logger.info(
            f"Refuel завершён: {len(refueled)}/{len(need_refuel)} кошельков пополнено"
        )
        return refueled
