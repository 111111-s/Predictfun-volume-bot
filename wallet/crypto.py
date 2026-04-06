"""
Шифрование и дешифрование данных кошельков

Опциональная зависимость: cryptography (Fernet + PBKDF2).
Если библиотека не установлена, шифрование недоступно,
но остальной функционал работает без ограничений.
"""

import base64
import os
import json
from typing import Optional

from loguru import logger

# Опциональная зависимость — шифрование работает только при наличии cryptography
try:
    from cryptography.fernet import Fernet, InvalidToken
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    Fernet = None
    InvalidToken = None
    logger.debug("cryptography не установлена — шифрование кошельков недоступно")


def is_encryption_available() -> bool:
    """Проверка доступности модуля шифрования"""
    return CRYPTO_AVAILABLE


def _derive_key(password: str, salt: bytes) -> bytes:
    """
    Получение ключа шифрования из пароля через PBKDF2.

    Использует SHA256, 480000 итераций (рекомендация OWASP 2023+).
    Результат — 32 байта, кодируется в base64 для Fernet.
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography не установлена. Установите: pip install cryptography")

    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=480_000,
    )
    key = base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))
    return key


def encrypt_data(data: str, password: str) -> dict:
    """
    Шифрование строки данных паролем.

    Args:
        data: Строка для шифрования (обычно JSON с приватными ключами)
        password: Пароль пользователя

    Returns:
        Словарь {"salt": base64-строка, "data": зашифрованная base64-строка}

    Raises:
        RuntimeError: Если cryptography не установлена
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography не установлена. Установите: pip install cryptography")

    # Генерируем случайную соль (16 байт)
    salt = os.urandom(16)
    key = _derive_key(password, salt)

    # Шифруем данные через Fernet
    fernet = Fernet(key)
    encrypted = fernet.encrypt(data.encode("utf-8"))

    return {
        "salt": base64.b64encode(salt).decode("utf-8"),
        "data": encrypted.decode("utf-8"),
    }


def decrypt_data(encrypted: dict, password: str) -> str:
    """
    Дешифрование данных, зашифрованных через encrypt_data().

    Args:
        encrypted: Словарь с ключами "salt" и "data"
        password: Пароль пользователя

    Returns:
        Расшифрованная строка (JSON)

    Raises:
        RuntimeError: Если cryptography не установлена
        ValueError: Если неверный пароль или повреждённые данные
    """
    if not CRYPTO_AVAILABLE:
        raise RuntimeError("cryptography не установлена. Установите: pip install cryptography")

    try:
        salt = base64.b64decode(encrypted["salt"])
        key = _derive_key(password, salt)
        fernet = Fernet(key)
        decrypted = fernet.decrypt(encrypted["data"].encode("utf-8"))
        return decrypted.decode("utf-8")
    except InvalidToken:
        raise ValueError("Неверный пароль или повреждённые данные")
    except KeyError as e:
        raise ValueError(f"Отсутствует обязательное поле в зашифрованных данных: {e}")
    except Exception as e:
        raise ValueError(f"Ошибка дешифрования: {e}")
