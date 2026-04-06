"""
Константы для Predict.fun Split Bot

Все магические числа, адреса и пороги собраны здесь.
"""

# ==========================================
# Wei & Precision
# ==========================================

WEI = 10**18                         # 1 токен = 10^18 wei (USDT на BSC = 18 decimals)
PRICE_PRECISION_WEI = 10**15         # Минимальный шаг цены: 0.001 = 1e15 wei
AMOUNT_PRECISION_WEI = 10**13        # Минимальная гранулярность суммы ордера
WEI_THRESHOLD = 10**15               # Порог для определения формата wei

# ==========================================
# ERC20 / Blockchain
# ==========================================

MAX_APPROVAL = 2**256 - 1            # Бесконечный аппрув ERC20
ZERO_ADDRESS = "0x" + "0" * 40      # Нулевой адрес (0x000...000)
DEFAULT_GAS_LIMIT = 100_000          # Gas limit по умолчанию (для approve)
SPLIT_GAS_LIMIT = 300_000            # Gas limit для split операций
MERGE_GAS_LIMIT = 300_000            # Gas limit для merge операций

# Multicall3 на BSC (для batch-чтения балансов)
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

# ==========================================
# Цены ордеров
# ==========================================

MIN_PRICE = 0.01                     # Минимальная цена ордера
MAX_PRICE = 0.99                     # Максимальная цена ордера
DEFAULT_PRICE_STEP = 0.001           # Шаг цены по умолчанию

# ==========================================
# Пороги
# ==========================================

MIN_ORDER_VALUE_USD = 1.30           # Минимальная стоимость ордера (dust threshold)
MIN_MERGE_VALUE_USD = 2.00           # Минимальная сумма для merge (экономия газа)
MIN_ORDER_SIZE = 1                   # Минимальное количество токенов для ордера

# ==========================================
# Внешние API
# ==========================================

BNB_FALLBACK_PRICE_USD = 700.0       # Fallback цена BNB если Binance API недоступен
BNB_PRICE_API_URL = "https://api.binance.com/api/v3/ticker/price?symbol=BNBUSDT"

# ==========================================
# Predict.fun API
# ==========================================

PREDICT_API_BASE_URL = "https://api.predict.fun"
PREDICT_API_VERSION = "v1"
PREDICT_API_RATE_LIMIT = 240         # Запросов в минуту (по документации)
PREDICT_SAFE_RATE_LIMIT = 200        # Безопасный лимит (с запасом)

# ==========================================
# Order parameters
# ==========================================

DEFAULT_FEE_RATE_BPS = 200           # 0.02% комиссия за ордер
ORDER_SIDE_BUY = 0                   # API: side=0 для BUY
ORDER_SIDE_SELL = 1                  # API: side=1 для SELL

# ==========================================
# Timeouts
# ==========================================

API_TIMEOUT_SECONDS = 30             # Таймаут HTTP запросов
TX_RECEIPT_TIMEOUT = 60              # Таймаут ожидания receipt транзакции
BNB_PRICE_TIMEOUT = 5               # Таймаут запроса цены BNB
