"""
Менеджер кошельков

Загрузка, сохранение, импорт и управление списком кошельков.
Поддерживает:
  - wallets.json (новый формат, с опциональным шифрованием)
  - privatekeys.txt (legacy-формат)
"""

import json
from pathlib import Path
from typing import List, Optional
from datetime import datetime

from loguru import logger
from eth_account import Account

from config.settings import ROOT_DIR, INPUT_DIR
from wallet.models import WalletInfo
from wallet.crypto import encrypt_data, decrypt_data, is_encryption_available


# Путь к файлу кошельков по умолчанию
WALLETS_FILE = ROOT_DIR / "wallets.json"

# Текущая версия формата wallets.json
WALLETS_FORMAT_VERSION = 1


class WalletManager:
    """
    Управление кошельками: загрузка, сохранение, импорт из legacy-формата.

    Формат wallets.json (незашифрованный):
        {
            "version": 1,
            "encrypted": false,
            "wallets": [... список WalletInfo.to_dict() ...]
        }

    Формат wallets.json (зашифрованный):
        {
            "version": 1,
            "encrypted": true,
            "salt": "base64...",
            "data": "fernet-encrypted..."
        }
    """

    def __init__(self, wallets_file: Optional[Path] = None):
        """
        Args:
            wallets_file: Путь к wallets.json (по умолчанию ROOT_DIR / wallets.json)
        """
        self.wallets_file = wallets_file or WALLETS_FILE

    # ------------------------------------------------------------------
    # Загрузка
    # ------------------------------------------------------------------

    def load_wallets(self, password: Optional[str] = None) -> List[WalletInfo]:
        """
        Загрузить кошельки из wallets.json.
        Если файл не найден — пытается импортировать из legacy privatekeys.txt.

        Args:
            password: Пароль для расшифровки (если файл зашифрован)

        Returns:
            Список WalletInfo
        """
        if self.wallets_file.exists():
            return self._load_from_json(password)

        # Fallback: пробуем legacy-формат
        logger.info("wallets.json не найден, пробуем импорт из privatekeys.txt")
        legacy_paths = [
            INPUT_DIR / "privatekeys.txt",
            ROOT_DIR / "privatekeys.txt",
        ]
        for path in legacy_paths:
            if path.exists():
                wallets = self.import_from_legacy(str(path))
                if wallets:
                    logger.success(f"Импортировано {len(wallets)} кошельков из {path.name}")
                    return wallets

        logger.warning("Ни wallets.json, ни privatekeys.txt не найдены")
        return []

    def _load_from_json(self, password: Optional[str] = None) -> List[WalletInfo]:
        """Загрузка из wallets.json"""
        try:
            raw = self.wallets_file.read_text(encoding="utf-8")
            data = json.loads(raw)
        except (json.JSONDecodeError, OSError) as e:
            logger.error(f"Ошибка чтения wallets.json: {e}")
            return []

        version = data.get("version", 1)
        encrypted = data.get("encrypted", False)

        if encrypted:
            # Расшифровка
            if not password:
                logger.error("Файл кошельков зашифрован — укажите пароль")
                return []

            if not is_encryption_available():
                logger.error("Для расшифровки установите: pip install cryptography")
                return []

            try:
                decrypted_json = decrypt_data(
                    {"salt": data["salt"], "data": data["data"]},
                    password,
                )
                wallet_dicts = json.loads(decrypted_json)
            except ValueError as e:
                logger.error(f"Не удалось расшифровать: {e}")
                return []
        else:
            wallet_dicts = data.get("wallets", [])

        wallets = []
        for wd in wallet_dicts:
            try:
                wallet = WalletInfo.from_dict(wd)
                wallets.append(wallet)
            except Exception as e:
                logger.warning(f"Пропущен кошелёк (ошибка парсинга): {e}")

        logger.info(f"Загружено {len(wallets)} кошельков из wallets.json")
        return wallets

    # ------------------------------------------------------------------
    # Сохранение
    # ------------------------------------------------------------------

    def save_wallets(
        self, wallets: List[WalletInfo], password: Optional[str] = None
    ) -> None:
        """
        Сохранить кошельки в wallets.json.

        Args:
            wallets: Список кошельков
            password: Если указан — файл будет зашифрован
        """
        wallet_dicts = [w.to_dict() for w in wallets]

        if password:
            if not is_encryption_available():
                logger.error("Для шифрования установите: pip install cryptography")
                return

            wallets_json = json.dumps(wallet_dicts, ensure_ascii=False)
            encrypted = encrypt_data(wallets_json, password)

            data = {
                "version": WALLETS_FORMAT_VERSION,
                "encrypted": True,
                "salt": encrypted["salt"],
                "data": encrypted["data"],
            }
        else:
            data = {
                "version": WALLETS_FORMAT_VERSION,
                "encrypted": False,
                "wallets": wallet_dicts,
            }

        try:
            self.wallets_file.write_text(
                json.dumps(data, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
            mode = "зашифрованном" if password else "открытом"
            logger.success(f"Сохранено {len(wallets)} кошельков в {mode} виде")
        except OSError as e:
            logger.error(f"Ошибка записи wallets.json: {e}")

    # ------------------------------------------------------------------
    # Добавление
    # ------------------------------------------------------------------

    def add_wallet(
        self,
        label: str,
        private_key: str,
        predict_account: Optional[str] = None,
    ) -> WalletInfo:
        """
        Создать WalletInfo из приватного ключа.

        Адрес вычисляется автоматически через eth_account.

        Args:
            label: Метка кошелька (например, "#1" или "main")
            private_key: Приватный ключ (hex, с или без 0x)
            predict_account: Адрес Predict Smart Wallet (опционально)

        Returns:
            Созданный WalletInfo
        """
        # Нормализация ключа
        if not private_key.startswith("0x"):
            private_key = "0x" + private_key

        # Вычисление адреса
        try:
            account = Account.from_key(private_key)
            address = account.address
        except Exception as e:
            raise ValueError(f"Неверный приватный ключ: {e}")

        wallet = WalletInfo(
            label=label,
            address=address,
            private_key=private_key,
            predict_account=predict_account,
        )

        logger.info(f"Добавлен кошелёк [{label}] {wallet.short_address}")
        return wallet

    # ------------------------------------------------------------------
    # Импорт из legacy privatekeys.txt
    # ------------------------------------------------------------------

    def import_from_legacy(
        self, filepath: str = "input_data/privatekeys.txt"
    ) -> List[WalletInfo]:
        """
        Импорт кошельков из legacy-формата privatekeys.txt.

        Поддерживаемые форматы строк:
            0xprivatekey
            label:0xprivatekey
            label:0xprivatekey:0xpredictaccount

        Пустые строки и строки, начинающиеся с #, пропускаются.

        Args:
            filepath: Путь к файлу (относительный от ROOT_DIR или абсолютный)

        Returns:
            Список WalletInfo
        """
        path = Path(filepath)
        if not path.is_absolute():
            path = ROOT_DIR / filepath

        if not path.exists():
            logger.error(f"Файл не найден: {path}")
            return []

        lines = path.read_text(encoding="utf-8").splitlines()
        wallets: List[WalletInfo] = []

        for i, raw_line in enumerate(lines):
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue

            try:
                wallet = self._parse_legacy_line(line, index=i)
                wallets.append(wallet)
            except Exception as e:
                logger.warning(f"Пропущена строка {i + 1} в {path.name}: {e}")

        logger.info(f"Импортировано {len(wallets)} кошельков из {path.name}")
        return wallets

    def _parse_legacy_line(self, line: str, index: int = 0) -> WalletInfo:
        """
        Разбор одной строки legacy-формата.

        Поддерживает:
            0xprivatekey
            label:0xprivatekey
            label:0xprivatekey:0xpredictaccount
        """
        label = f"#{index + 1}"
        private_key = line
        predict_account = None

        parts = line.split(":")

        # Определяем predict_account — последний элемент, если это адрес (0x + 40 hex)
        if (
            len(parts) >= 2
            and parts[-1].startswith("0x")
            and len(parts[-1]) == 42
        ):
            predict_account = parts[-1]
            parts = parts[:-1]

        if len(parts) == 1:
            # Только приватный ключ
            private_key = parts[0]
        elif len(parts) >= 2:
            # Проверяем: первый элемент — метка (короткий, не hex)
            if len(parts[0]) < 20 and not parts[0].startswith("0x"):
                label = parts[0]
                private_key = parts[1]
            else:
                private_key = parts[0]

        return self.add_wallet(
            label=label,
            private_key=private_key,
            predict_account=predict_account,
        )

    # ------------------------------------------------------------------
    # Отображение
    # ------------------------------------------------------------------

    @staticmethod
    def list_wallets(wallets: List[WalletInfo]) -> None:
        """
        Вывод таблицы кошельков в консоль.
        Приватные ключи НЕ показываются.
        """
        if not wallets:
            logger.info("Список кошельков пуст")
            return

        # Заголовок
        header = (
            f"{'#':<4} {'Label':<12} {'Address':<44} "
            f"{'Predict Account':<44} {'Master':<7} {'Path'}"
        )
        separator = "-" * len(header)

        print(f"\n{separator}")
        print(header)
        print(separator)

        for i, w in enumerate(wallets, 1):
            predict = w.predict_account or "-"
            master = "  *" if w.is_master else ""
            path = w.derivation_path or "-"

            print(
                f"{i:<4} {w.label:<12} {w.address:<44} "
                f"{predict:<44} {master:<7} {path}"
            )

        print(separator)
        print(f"Всего: {len(wallets)} кошельков\n")

    # ------------------------------------------------------------------
    # Мастер-кошелёк
    # ------------------------------------------------------------------

    @staticmethod
    def set_master(wallets: List[WalletInfo], label: str) -> List[WalletInfo]:
        """
        Назначить мастер-кошелёк по метке.
        Предыдущий мастер (если был) снимается.

        Args:
            wallets: Список кошельков
            label: Метка кошелька, который станет мастером

        Returns:
            Обновлённый список (тот же объект)

        Raises:
            ValueError: Если кошелёк с указанной меткой не найден
        """
        found = False
        for w in wallets:
            if w.label == label:
                w.is_master = True
                found = True
                logger.info(f"Мастер-кошелёк: [{w.label}] {w.short_address}")
            else:
                w.is_master = False

        if not found:
            raise ValueError(f"Кошелёк с меткой '{label}' не найден")

        return wallets

    @staticmethod
    def get_master(wallets: List[WalletInfo]) -> Optional[WalletInfo]:
        """
        Получить мастер-кошелёк из списка.

        Returns:
            WalletInfo мастер-кошелька или None, если не назначен
        """
        for w in wallets:
            if w.is_master:
                return w
        return None
