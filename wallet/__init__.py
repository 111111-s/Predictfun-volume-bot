"""
Модуль управления кошельками для Predict.fun Split Bot

Компоненты:
  - WalletInfo: датакласс с информацией о кошельке
  - WalletManager: загрузка, сохранение, импорт кошельков
  - FundDistributor: распределение и сбор BNB / USDT
  - generate_mnemonic / generate_from_mnemonic: HD-генерация кошельков
"""

from wallet.models import WalletInfo
from wallet.manager import WalletManager
from wallet.distributor import FundDistributor
from wallet.generator import generate_mnemonic, generate_from_mnemonic

__all__ = [
    "WalletInfo",
    "WalletManager",
    "FundDistributor",
    "generate_mnemonic",
    "generate_from_mnemonic",
]
