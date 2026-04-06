"""
Генерация HD-кошельков из мнемонической фразы

Использует eth_account с BIP44 деривацией (m/44'/60'/0'/0/{index}).
Опциональная зависимость: mnemonic (для генерации фразы).
eth_account уже является зависимостью проекта.
"""

from typing import List, Optional
from datetime import datetime

from loguru import logger
from eth_account import Account

from wallet.models import WalletInfo


# BIP44 путь для Ethereum (и EVM-совместимых сетей, включая BSC)
BIP44_BASE_PATH = "m/44'/60'/0'/0"

# Включаем поддержку мнемоник в eth_account
Account.enable_unaudited_hdwallet_features()

# Опциональная зависимость для генерации мнемоники
try:
    from mnemonic import Mnemonic
    MNEMONIC_AVAILABLE = True
except ImportError:
    MNEMONIC_AVAILABLE = False
    Mnemonic = None
    logger.debug("mnemonic не установлена — генерация фраз недоступна, "
                 "но импорт существующих мнемоник работает")


def generate_mnemonic(strength: int = 128) -> str:
    """
    Генерация новой 12-словной мнемонической фразы (BIP39).

    Args:
        strength: Битность энтропии (128 = 12 слов, 256 = 24 слова)

    Returns:
        Мнемоническая фраза (12 слов, разделённых пробелами)

    Raises:
        RuntimeError: Если библиотека mnemonic не установлена
    """
    if not MNEMONIC_AVAILABLE:
        raise RuntimeError(
            "Библиотека mnemonic не установлена. "
            "Установите: pip install mnemonic"
        )

    mnemo = Mnemonic("english")
    phrase = mnemo.generate(strength=strength)
    logger.info("Сгенерирована новая мнемоническая фраза (12 слов)")
    return phrase


def generate_from_mnemonic(
    mnemonic: str,
    count: int = 5,
    start_index: int = 0,
    label_prefix: str = "hd",
) -> List[WalletInfo]:
    """
    Генерация кошельков из мнемонической фразы по BIP44.

    Путь деривации: m/44'/60'/0'/0/{index}
    Совместим с MetaMask, Trust Wallet и другими HD-кошельками.

    Args:
        mnemonic: Мнемоническая фраза (12 или 24 слова)
        count: Количество кошельков для генерации
        start_index: Начальный индекс деривации
        label_prefix: Префикс метки (например, "hd" -> "hd-0", "hd-1", ...)

    Returns:
        Список WalletInfo с заполненными derivation_path

    Raises:
        ValueError: Если мнемоника невалидна
    """
    # Валидация мнемоники (базовая проверка длины)
    words = mnemonic.strip().split()
    if len(words) not in (12, 15, 18, 21, 24):
        raise ValueError(
            f"Мнемоника должна содержать 12, 15, 18, 21 или 24 слова "
            f"(получено {len(words)})"
        )

    wallets: List[WalletInfo] = []
    created_at = datetime.utcnow().isoformat() + "Z"

    for i in range(start_index, start_index + count):
        path = f"{BIP44_BASE_PATH}/{i}"

        try:
            # eth_account использует mnemonic + путь для деривации
            account = Account.from_mnemonic(
                mnemonic,
                account_path=path,
            )

            wallet = WalletInfo(
                label=f"{label_prefix}-{i}",
                address=account.address,
                private_key=account.key.hex() if not account.key.hex().startswith("0x") else account.key.hex(),
                derivation_path=path,
                created_at=created_at,
            )
            wallets.append(wallet)

        except Exception as e:
            logger.error(f"Ошибка деривации по пути {path}: {e}")
            raise ValueError(f"Невалидная мнемоника или ошибка деривации: {e}")

    logger.success(
        f"Сгенерировано {len(wallets)} кошельков "
        f"(индексы {start_index}..{start_index + count - 1})"
    )
    return wallets
