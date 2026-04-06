"""
Модели данных для управления кошельками

Датаклассы для хранения информации о кошельках.
"""

from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class WalletInfo:
    """
    Информация о кошельке.

    Хранит приватный ключ, адрес, метку и опциональный Predict Smart Wallet адрес.
    Адрес вычисляется автоматически из приватного ключа при создании.
    """
    label: str
    address: str                              # Вычисляется из private_key
    private_key: str                          # Hex с префиксом 0x
    predict_account: Optional[str] = None     # Адрес Predict Smart Wallet
    is_master: bool = False                   # Мастер-кошелёк для распределения средств
    created_at: str = ""                      # ISO timestamp создания
    derivation_path: str = ""                 # BIP44 путь, если сгенерирован из мнемоники

    def __post_init__(self):
        """Валидация и нормализация полей после создания"""
        # Добавляем 0x префикс к приватному ключу, если отсутствует
        if self.private_key and not self.private_key.startswith("0x"):
            self.private_key = "0x" + self.private_key

        # Устанавливаем время создания, если не задано
        if not self.created_at:
            self.created_at = datetime.utcnow().isoformat() + "Z"

    @property
    def short_address(self) -> str:
        """Сокращённый адрес для отображения: 0x1234...abcd"""
        if len(self.address) <= 10:
            return self.address
        return f"{self.address[:6]}...{self.address[-4:]}"

    @property
    def has_predict_account(self) -> bool:
        """Есть ли привязанный Predict Smart Wallet"""
        return bool(self.predict_account)

    def to_dict(self) -> dict:
        """Сериализация в словарь для сохранения в JSON"""
        return {
            "label": self.label,
            "address": self.address,
            "private_key": self.private_key,
            "predict_account": self.predict_account,
            "is_master": self.is_master,
            "created_at": self.created_at,
            "derivation_path": self.derivation_path,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "WalletInfo":
        """Десериализация из словаря"""
        return cls(
            label=data.get("label", ""),
            address=data.get("address", ""),
            private_key=data.get("private_key", ""),
            predict_account=data.get("predict_account"),
            is_master=data.get("is_master", False),
            created_at=data.get("created_at", ""),
            derivation_path=data.get("derivation_path", ""),
        )

    def to_account_dict(self) -> dict:
        """
        Конвертация в формат, совместимый с load_accounts() из main.py.

        Возвращает словарь вида:
            {"private_key": ..., "label": ..., "predict_account": ..., "proxy": None}
        """
        return {
            "private_key": self.private_key,
            "label": self.label,
            "predict_account": self.predict_account,
            "proxy": None,
        }
