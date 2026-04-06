"""
Configuration for Predict.fun Market Maker Bot

Загрузка настроек из settings.yaml и markets.yaml.
"""

import yaml
from pathlib import Path
from dataclasses import dataclass, field
from typing import List, Tuple, Optional

from loguru import logger


# Пути
ROOT_DIR = Path(__file__).parent.parent
CONFIG_DIR = ROOT_DIR / "config"
INPUT_DIR = ROOT_DIR / "input_data"
DATABASE_DIR = ROOT_DIR / "databases"
LOGS_DIR = ROOT_DIR / "logs"


@dataclass
class PredictConfig:
    """Настройки Predict.fun API"""
    api_url: str = "https://api.predict.fun"
    api_key: str = ""


@dataclass
class GasConfig:
    """Настройки газа (BNB Chain)"""
    gwei_multiplier: float = 1.2       # Множитель базовой цены газа
    max_gas_price_gwei: float = 10.0   # Максимальная цена газа (gwei)


@dataclass
class GeneralConfig:
    """Общие настройки"""
    shuffle_wallets: bool = True
    threads: int = 1
    retry_count: int = 3
    dry_run: bool = False              # Режим симуляции (без реальных сделок)


@dataclass
class MarketsConfig:
    """Настройки маркетов"""
    markets_per_account: Tuple[int, int] = (5, 10)
    split_amount: Tuple[float, float] = (5.0, 10.0)
    cycles: Tuple[int, int] = (3, 6)
    list: List["MarketCondition"] = field(default_factory=list)


@dataclass
class SleepConfig:
    """Настройки пауз"""
    after_split: Tuple[int, int] = (15, 20)
    after_buy: Tuple[int, int] = (3, 5)
    between_orders: Tuple[int, int] = (2, 4)
    after_order_check: Tuple[int, int] = (5, 10)
    between_cycles: Tuple[int, int] = (5, 15)
    after_account: Tuple[int, int] = (10, 20)
    between_threads: Tuple[int, int] = (1, 3)
    after_repost: Tuple[int, int] = (1, 2)
    small_pause: Tuple[int, int] = (1, 2)


@dataclass
class LimitsConfig:
    """Настройки лимитов"""
    buy_price_step: float = 0.1   # Шаг для BUY ордеров (от best_bid)
    sell_price_step: float = 0.5  # Наценка для SELL ордеров (от цены покупки)
    min_spread: float = 0.1
    check_interval: int = 3
    repost_if_not_best: bool = True
    repost_up_if_gap: bool = True
    
    # Пороги
    min_sell_amount: float = 1.0  # Минимальное количество токенов для продажи
    dust_threshold_usd: float = 1.35
    resplit_threshold_usd: float = 5.0
    
    # Обработка ошибок
    retry_on_fail: int = 3
    order_verify_retries: int = 3
    balance_wait_timeout: int = 30
    balance_check_interval: float = 1.0


@dataclass
class StopLossConfig:
    """Настройки стоп-лосса"""
    enabled: bool = True
    percent: float = 5.0
    blacklist_on_sl: bool = True


@dataclass
class AlertsConfig:
    """Настройки алертов"""
    min_bnb: float = 0.0005
    min_usdt: float = 5.0


@dataclass
class TelegramConfig:
    """Настройки Telegram"""
    enabled: bool = False
    bot_token: str = ""
    user_ids: List[int] = field(default_factory=list)


@dataclass
class StatisticsConfig:
    """Настройки статистики"""
    send_to_telegram: bool = True


@dataclass
class MarketCondition:
    """Условие маркета из конфига"""
    url: str
    choice: str = ""
    
    @property
    def market_id(self) -> str:
        """Извлечь market ID из URL"""
        # https://predict.fun/market/abc123 -> abc123
        if "/market/" in self.url:
            return self.url.split("/market/")[-1].split("/")[0].split("?")[0]
        return self.url
    
    @property
    def full_id(self) -> str:
        """Полный ID включая choice"""
        if self.choice:
            return f"{self.market_id}:{self.choice}"
        return self.market_id


@dataclass
class Config:
    """Главный конфиг"""
    general: GeneralConfig = field(default_factory=GeneralConfig)
    predict: PredictConfig = field(default_factory=PredictConfig)
    markets: MarketsConfig = field(default_factory=MarketsConfig)
    sleep: SleepConfig = field(default_factory=SleepConfig)
    limits: LimitsConfig = field(default_factory=LimitsConfig)
    stop_loss: StopLossConfig = field(default_factory=StopLossConfig)
    alerts: AlertsConfig = field(default_factory=AlertsConfig)
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    statistics: StatisticsConfig = field(default_factory=StatisticsConfig)
    gas: GasConfig = field(default_factory=GasConfig)
    rpcs: List[str] = field(default_factory=list)
    
    @property
    def threads(self) -> int:
        return self.general.threads
    
    @property
    def shuffle_wallets(self) -> bool:
        return self.general.shuffle_wallets


def _parse_tuple(value, default: Tuple) -> Tuple:
    """Парсит tuple из списка или значения"""
    if isinstance(value, (list, tuple)):
        if len(value) >= 2:
            return (value[0], value[1])
        elif len(value) == 1:
            return (value[0], value[0])
    elif isinstance(value, (int, float)):
        return (value, value)
    return default


def _load_yaml(filename: str) -> dict:
    """Загрузить YAML файл"""
    filepath = ROOT_DIR / filename
    if not filepath.exists():
        logger.warning(f"File not found: {filepath}")
        return {}
    
    with open(filepath, 'r', encoding='utf-8') as f:
        return yaml.safe_load(f) or {}


def _load_markets() -> List[MarketCondition]:
    """Загрузить markets.yaml"""
    data = _load_yaml("input_data/markets.yaml")
    markets_raw = data.get("markets") or []
    
    result = []
    for item in markets_raw:
        if isinstance(item, str):
            # Поддерживаемые форматы:
            # 1. "1018" - просто числовой ID
            # 2. "1018:yes" или "1018:no" - ID с outcome
            # 3. "slug-name" - slug маркета
            # 4. "slug-name:2" - slug с номером (multi-choice)
            # 5. "https://predict.fun/market/slug" - полный URL
            # 6. "https://predict.fun/market/slug:2" - URL с номером
            
            # Проверяем есть ли протокол (http/https)
            has_protocol = item.startswith("http://") or item.startswith("https://")
            
            if has_protocol:
                # URL формат - разбираем по последнему ':'
                if item.count(":") > 1:
                    # Есть : помимо протокола
                    parts = item.rsplit(":", 1)
                    choice_part = parts[-1]
                    # choice может быть числом (multi-choice) или yes/no
                    if choice_part.isdigit() or choice_part.lower() in ("yes", "no", "y", "n"):
                        result.append(MarketCondition(url=parts[0], choice=choice_part))
                    else:
                        result.append(MarketCondition(url=item))
                else:
                    result.append(MarketCondition(url=item))
            else:
                # Без протокола - может быть ID или slug
                if ":" in item:
                    # Есть выбор: "1018:yes" или "slug:2"
                    parts = item.rsplit(":", 1)
                    result.append(MarketCondition(url=parts[0], choice=parts[1]))
                else:
                    # Просто ID или slug: "1018" или "btc-100k"
                    result.append(MarketCondition(url=item))
        elif isinstance(item, dict):
            result.append(MarketCondition(
                url=item.get("url", item.get("id", "")),
                choice=str(item.get("choice", ""))
            ))
    
    return result


def load_config() -> Config:
    """Загрузить полный конфиг"""
    settings = _load_yaml("settings.yaml")
    markets = _load_markets()
    
    # General
    general_data = settings.get("general", {})
    general = GeneralConfig(
        shuffle_wallets=general_data.get("shuffle_wallets", True),
        threads=general_data.get("threads", 1),
        retry_count=general_data.get("retry_count", 3),
        dry_run=general_data.get("dry_run", False)
    )
    
    # Predict API
    predict_data = settings.get("predict", {})
    predict = PredictConfig(
        api_url=predict_data.get("api_url", "https://api.predict.fun"),
        api_key=predict_data.get("api_key", "")
    )
    
    # Markets
    markets_data = settings.get("markets", {})
    markets_config = MarketsConfig(
        markets_per_account=_parse_tuple(markets_data.get("markets_per_account"), (5, 10)),
        split_amount=_parse_tuple(markets_data.get("split_amount"), (5.0, 10.0)),
        cycles=_parse_tuple(markets_data.get("cycles"), (3, 6)),
        list=markets
    )
    
    # Sleep
    sleep_data = settings.get("sleep", {})
    sleep = SleepConfig(
        after_split=_parse_tuple(sleep_data.get("after_split"), (15, 20)),
        after_buy=_parse_tuple(sleep_data.get("after_buy"), (3, 5)),
        between_orders=_parse_tuple(sleep_data.get("between_orders"), (2, 4)),
        after_order_check=_parse_tuple(sleep_data.get("after_order_check"), (5, 10)),
        between_cycles=_parse_tuple(sleep_data.get("between_cycles"), (5, 15)),
        after_account=_parse_tuple(sleep_data.get("after_account"), (10, 20)),
        between_threads=_parse_tuple(sleep_data.get("between_threads"), (1, 3)),
        after_repost=_parse_tuple(sleep_data.get("after_repost"), (1, 2)),
        small_pause=_parse_tuple(sleep_data.get("small_pause"), (1, 2))
    )
    
    # Limits
    limits_data = settings.get("limits", {})
    limits = LimitsConfig(
        buy_price_step=limits_data.get("buy_price_step", 0.1),
        sell_price_step=limits_data.get("sell_price_step", 0.5),
        min_spread=limits_data.get("min_spread", 0.1),
        check_interval=limits_data.get("check_interval", 3),
        repost_if_not_best=limits_data.get("repost_if_not_best", True),
        repost_up_if_gap=limits_data.get("repost_up_if_gap", True),
        min_sell_amount=limits_data.get("min_sell_amount", 1.0),
        dust_threshold_usd=limits_data.get("dust_threshold_usd", 1.35),
        resplit_threshold_usd=limits_data.get("resplit_threshold_usd", 5.0),
        retry_on_fail=limits_data.get("retry_on_fail", 3),
        order_verify_retries=limits_data.get("order_verify_retries", 3),
        balance_wait_timeout=limits_data.get("balance_wait_timeout", 30),
        balance_check_interval=limits_data.get("balance_check_interval", 1.0)
    )
    
    # Stop-loss
    sl_data = settings.get("stop_loss", {})
    stop_loss = StopLossConfig(
        enabled=sl_data.get("enabled", True),
        percent=sl_data.get("percent", 5.0),
        blacklist_on_sl=sl_data.get("blacklist_on_sl", True)
    )
    
    # Alerts
    alerts_data = settings.get("alerts", {})
    alerts = AlertsConfig(
        min_bnb=alerts_data.get("min_bnb", 0.0005),
        min_usdt=alerts_data.get("min_usdt", 5.0)
    )
    
    # Telegram
    tg_data = settings.get("telegram", {})
    telegram = TelegramConfig(
        enabled=tg_data.get("enabled", False),
        bot_token=tg_data.get("bot_token", ""),
        user_ids=tg_data.get("user_ids", [])
    )
    
    # Statistics
    stats_data = settings.get("statistics", {})
    statistics = StatisticsConfig(
        send_to_telegram=stats_data.get("send_to_telegram", True)
    )
    
    # Gas
    gas_data = settings.get("gas", {})
    gas = GasConfig(
        gwei_multiplier=gas_data.get("gwei_multiplier", 1.2),
        max_gas_price_gwei=gas_data.get("max_gas_price_gwei", 10.0)
    )

    # RPCs
    rpcs = settings.get("rpcs", [
        "https://bsc-dataseed1.binance.org",
        "https://bsc-dataseed2.binance.org"
    ])

    return Config(
        general=general,
        predict=predict,
        markets=markets_config,
        sleep=sleep,
        limits=limits,
        stop_loss=stop_loss,
        alerts=alerts,
        telegram=telegram,
        statistics=statistics,
        gas=gas,
        rpcs=rpcs
    )


# Загружаем конфиг при импорте
config = load_config()
