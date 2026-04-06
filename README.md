# Predict.fun Trading Bot

Автоматизированный бот для арбитражной торговли на [Predict.fun](https://predict.fun) — рынке предсказаний на BNB Chain.

---

## Обзор

Бот реализует две торговые стратегии на рынке предсказаний Predict.fun:

- **Split Trading** — покупка USDT, разделение на YES + NO токены, продажа каждой стороны по отдельности. Прибыль = сумма продаж минус $1 за каждую пару.
- **Limit Trading** — параллельные лимитные ордера: покупка по best bid, продажа с наценкой.

Поддерживается мультиаккаунт, прокси, Telegram-оповещения, шифрование кошельков и оптимизация газа.

---

## Требования

| Компонент | Минимум |
|---|---|
| Python | 3.10+ |
| BNB | ~0.001 BNB на аккаунт (для on-chain операций) |
| USDT (BEP-20) | Любая сумма для торговли (рекомендуется от $10) |
| API ключ | Бесплатно через Discord Predict.fun |
| Прокси | Опционально |

---

## Быстрый старт

```bash
# 1. Скачать или клонировать проект
git clone <url> Nodefarm-predict-fun-split-bot

# 2. Перейти в директорию
cd Nodefarm-predict-fun-split-bot

# 3. Установить зависимости
pip install -r requirements.txt

# 4. Добавить приватный ключ
#    (см. раздел "Настройка кошельков")
echo "0xВАШ_ПРИВАТНЫЙ_КЛЮЧ" > input_data/privatekeys.txt

# 5. Получить API ключ (см. раздел "Получение API ключа")
#    и вписать в settings.yaml -> predict -> api_key

# 6. Добавить маркеты в input_data/markets.yaml

# 7. Запустить бот
python main.py
```

При запуске бот покажет интерактивное меню с доступными действиями.

---

## Настройка кошельков

### Метод 1: privatekeys.txt (простой)

Файл: `input_data/privatekeys.txt`

Поддерживаемые форматы (по одному ключу на строку):

```
# Просто приватный ключ
0x1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef

# Метка + ключ
account1:0x1234567890abcdef...

# Метка + ключ + Predict Smart Wallet адрес
account1:0xприватный_ключ_privy:0xадрес_predict_smart_wallet
```

Строки, начинающиеся с `#`, игнорируются (комментарии).

### Метод 2: wallets.json (рекомендуется)

Новый формат с поддержкой шифрования. Создается автоматически при импорте.

**Импорт из privatekeys.txt:**
Выберите пункт меню **11 — Import wallets from privatekeys.txt**. Бот предложит установить пароль для шифрования.

Формат незашифрованного wallets.json:
```json
{
  "version": 1,
  "encrypted": false,
  "wallets": [
    {
      "label": "account1",
      "address": "0x...",
      "private_key": "0x...",
      "predict_account": null,
      "is_master": false
    }
  ]
}
```

Формат зашифрованного wallets.json:
```json
{
  "version": 1,
  "encrypted": true,
  "salt": "base64...",
  "data": "fernet-encrypted-data..."
}
```

Шифрование использует Fernet (AES-128-CBC) + PBKDF2 с 480 000 итерациями.

### Метод 3: Генерация из мнемоники

Пункт меню **12 — Generate wallets from mnemonic**.

- Генерирует кошельки из 12-словной BIP39 фразы
- BIP44 деривация: `m/44'/60'/0'/0/{index}`
- Совместимо с MetaMask и Trust Wallet
- Можно ввести существующую фразу или сгенерировать новую
- Новые кошельки добавляются к существующим в wallets.json

### Настройка мастер-кошелька

Мастер-кошелек используется для распределения и сбора средств между суб-кошельками.

Для установки мастера:
1. Выберите пункт **16 — List all wallets**
2. Если мастер не задан, бот предложит выбрать его из списка

Мастер-кошелек может:
- Распределять BNB на суб-кошельки (пункт 13)
- Распределять USDT на суб-кошельки (пункт 14)
- Собирать средства обратно (пункт 15)

### Откуда взять приватный ключ

**MetaMask:**
1. Откройте MetaMask
2. Нажмите на три точки рядом с аккаунтом
3. Account details -> Show private key
4. Введите пароль MetaMask
5. Скопируйте ключ (начинается с `0x`)

**Trust Wallet:**
1. Откройте Trust Wallet -> Settings
2. Wallets -> выберите кошелек
3. Show Private Key
4. Введите пароль
5. Скопируйте ключ

**Predict.fun Smart Wallet (Privy Wallet):**
1. На Predict.fun откройте Settings
2. Embedded Wallets -> Export Private Key
3. Это ключ Privy Wallet (он подписывает транзакции)
4. Predict Smart Wallet адрес — это ваш deposit address на Predict.fun
5. В privatekeys.txt используйте формат: `метка:0xprivy_ключ:0xsmart_wallet_адрес`

---

## Получение API ключа

API ключ **обязателен** для работы с Predict.fun mainnet.

1. Перейдите по ссылке: [https://discord.gg/predictdotfun](https://discord.gg/predictdotfun)
2. Присоединитесь к Discord серверу Predict.fun
3. Найдите канал **#api-access**
4. Запросите API ключ (обычно выдается сразу)
5. Скопируйте полученный ключ
6. Вставьте в `settings.yaml`:

```yaml
predict:
  api_key: "ваш-api-ключ"
```

---

## Настройка settings.yaml

Главный файл конфигурации. Ниже описан каждый раздел.

### predict (API доступ)

```yaml
predict:
  api_key: ""  # ОБЯЗАТЕЛЬНО! Ключ из Discord
```

### general (общие настройки)

```yaml
general:
  shuffle_wallets: true   # Перемешивать порядок кошельков
  threads: 1              # Количество параллельных потоков
  retry_count: 3          # Повторов при ошибках
  dry_run: false          # Режим симуляции (ордера не создаются)
```

| Параметр | Описание | По умолчанию |
|---|---|---|
| `shuffle_wallets` | Рандомизация порядка аккаунтов | `true` |
| `threads` | Параллельных потоков (для 50+ аккаунтов: 5-15) | `1` |
| `retry_count` | Повторов при ошибках API | `3` |
| `dry_run` | Симуляция без реальных сделок | `false` |

### gas (настройки газа BNB Chain)

```yaml
gas:
  gwei_multiplier: 1.2      # Множитель базовой цены газа (+20%)
  max_gas_price_gwei: 10.0   # Максимальная цена газа (защита от спайков)
```

### markets (настройки маркетов)

```yaml
markets:
  markets_per_account: [1, 1]   # Маркетов на аккаунт [min, max]
  split_amount: [10, 15]        # Сумма сплита в USDT [min, max]
  cycles: [3, 6]                # Циклов на аккаунт [min, max]
```

Бот выбирает случайное значение из диапазона `[min, max]` для каждого аккаунта.

### limits (лимитные ордера)

```yaml
limits:
  # BUY ордера
  buy_price_step: 0.0          # Шаг от best_bid (центы)
                                #  > 0: агрессивно (выше best_bid)
                                #  = 0: ровно по best_bid
                                #  < 0: пассивно (ниже best_bid)

  # SELL ордера
  sell_price_step: 0.1         # Наценка от цены покупки (центы)
  min_spread: 0.1              # Мин. спред (защита от самопокупки)
  check_interval: 3            # Интервал проверки стакана (сек)
  repost_if_not_best: true     # Перевыставлять если не лучшая цена
  repost_up_if_gap: true       # Перевыставлять вверх если есть зазор

  # Пороги
  min_sell_amount: 1.0         # Минимум токенов для продажи
  dust_threshold_usd: 1.35     # Минимальная стоимость ордера ($)
  resplit_threshold_usd: 5.0   # Порог для нового цикла

  # Обработка ошибок
  retry_on_fail: 3             # Повторов создания ордера
  order_verify_retries: 3      # Попыток проверки ордера
  balance_wait_timeout: 30     # Таймаут ожидания баланса (сек)
  balance_check_interval: 1    # Интервал проверки баланса (сек)
```

### stop_loss (стоп-лосс)

```yaml
stop_loss:
  enabled: true           # Включен
  percent: 10.0           # % падения для срабатывания
  blacklist_on_sl: true   # Добавлять маркет в черный список после SL
```

### alerts (оповещения)

```yaml
alerts:
  min_bnb: 0.0005    # Алерт при низком BNB
  min_usdt: 5.0      # Алерт при низком USDT
```

### telegram (уведомления)

```yaml
telegram:
  bot_token: ""         # Токен бота от @BotFather
  user_ids: []          # ID пользователей [123456789, 987654321]
```

### sleep (задержки)

Все значения в секундах, формат `[min, max]`:

```yaml
sleep:
  after_split: [5, 10]        # После сплита (синхронизация API)
  between_orders: [2, 5]      # Между ордерами
  after_order_check: [10, 25] # После выставления перед проверкой
  between_cycles: [5, 15]     # Между циклами
  after_account: [10, 20]     # После завершения аккаунта
  between_threads: [1, 3]     # Между запуском потоков
  after_repost: [1, 2]        # После перевыставления
  small_pause: [1, 2]         # Мелкие паузы
```

### rpcs (RPC ноды BSC)

```yaml
rpcs:
  - "https://bsc-dataseed1.binance.org"
  - "https://bsc-dataseed2.binance.org"
  - "https://bsc-dataseed3.binance.org"
  - "https://bsc-dataseed4.binance.org"
  - "https://rpc-bsc.48.club"
  - "https://bsc.meowrpc.com"
```

Можно добавить собственные RPC или использовать платные для повышения надежности.

---

## Настройка markets.yaml

Файл: `input_data/markets.yaml`

### Поддерживаемые форматы

```yaml
markets:
  # Числовой ID маркета
  - "1562"

  # Slug маркета с выбором стороны (yes/no)
  - "btc-100k:yes"

  # Multi-choice маркет (индекс с 1)
  - "1018:1"

  # Полный URL
  - "https://predict.fun/market/some-market-slug"

  # URL с индексом выбора
  - "https://predict.fun/market/some-market-slug:2"
```

### Где найти ID маркетов

Используйте пункт меню **9 — Show all markets (get IDs)**. Бот загрузит информацию обо всех маркетах из конфига и покажет их числовые ID.

Пример вывода:
```
  - "1562"   # English Premier League - Arsenal
  - "5532"   # Will the Federal Reserve... - No change
  - "6133"   # Will BNB reach... - $850
```

---

## Настройка прокси

Файл: `input_data/proxies.txt`

### Поддерживаемые форматы

```
ip:port:user:pass
user:pass@ip:port
http://user:pass@ip:port
```

Прокси назначаются кошелькам по кругу (round-robin). Если прокси 3, а кошельков 6 — каждый прокси обслуживает 2 кошелька.

Строки с `ip:port` (без авторизации) тоже поддерживаются.

---

## Торговые стратегии

### Split Trading (пункт 1 меню)

Основная стратегия арбитража на рынках предсказаний.

**Принцип работы:**

```
    $10 USDT
       |
       v
  [Split on-chain]  (газ ~$0.01)
       |
   +---+---+
   |       |
  10 YES  10 NO
   |       |
   v       v
 [Sell]  [Sell]     (лимитные ордера, без газа)
   |       |
 $5.20   $5.10
   |       |
   +---+---+
       |
    $10.30          (прибыль = $0.30)
```

**Пошагово:**
1. USDT разделяется на равное количество YES и NO токенов (on-chain, требует газа)
2. Бот выставляет sell-ордера для YES и NO по отдельности
3. Когда одна сторона продается, бот мониторит и перевыставляет другую
4. Если обе стороны проданы частично — оставшиеся YES+NO мержатся обратно в USDT
5. Прибыль = (цена_YES + цена_NO) - $1.00 за каждую пару

**Когда выгодно:** Сумма best ask YES + best ask NO > $1.00 (обычно $1.002-$1.010 на ликвидных маркетах).

### Limit Trading (пункт 8 меню)

Параллельная торговля лимитными ордерами на нескольких маркетах.

**Принцип работы:**
1. Бот выставляет BUY ордера на выбранных маркетах
2. Когда ордер исполнен, выставляет SELL с наценкой (`sell_price_step`)
3. Цикл повторяется: BUY -> SELL -> BUY
4. Бот следит за best bid и перевыставляет ордера при необходимости

**Когда использовать:** Высоколиквидные маркеты с активной торговлей.

---

## Меню операций

| # | Действие | Описание |
|---|---|---|
| 1 | Start split trading | Запуск стратегии Split Trading |
| 2 | Monitor existing orders | Мониторинг открытых ордеров (без создания новых) |
| 3 | Stop all positions and merge | Отмена ордеров + merge YES/NO обратно в USDT |
| 4 | Sell all positions market | Отмена ордеров + рыночная продажа всех позиций |
| 5 | Check balances | Проверка USDT балансов всех аккаунтов |
| 6 | Set approvals | Первичная настройка ERC20 approvals (необходимо 1 раз) |
| 7 | Cancel all orders | Отмена всех открытых ордеров |
| 8 | Start limit trading | Запуск стратегии Limit Trading |
| 9 | Show all markets | Показать маркеты из конфига с их числовыми ID |
| 10 | Claim resolved positions | Забрать выигрыш по разрешенным маркетам |
| 11 | Import wallets | Импорт из privatekeys.txt в wallets.json |
| 12 | Generate wallets | Генерация кошельков из мнемонической фразы |
| 13 | Distribute BNB | Раздать BNB из мастер-кошелька на суб-кошельки |
| 14 | Distribute USDT | Раздать USDT из мастер-кошелька на суб-кошельки |
| 15 | Collect funds | Собрать средства с суб-кошельков на мастер-кошелек |
| 16 | List all wallets | Показать список кошельков, установить мастер |
| 17 | Portfolio overview | Обзор портфеля: балансы, ордера, позиции по всем аккаунтам |

Бот также поддерживает запуск через аргумент командной строки:
```bash
python main.py --action 1   # Запуск split trading без меню
python main.py -a 5          # Проверка балансов
```

---

## Оптимизация газа (BNB комиссии)

### Что требует газ, а что нет

| Операция | Газ? | Описание |
|---|---|---|
| Создание/отмена ордеров | Нет | Через API, бесплатно |
| Split (USDT -> YES+NO) | Да | On-chain операция |
| Merge (YES+NO -> USDT) | Да | On-chain операция |
| Redeem (claim выигрыша) | Да | On-chain операция |
| ERC20 Approvals | Да | Один раз при первой настройке |

### Как бот экономит газ

- **EIP-1559 ценообразование** — автоматический расчет оптимальной цены газа
- **Batch approvals** — последовательные nonce для нескольких approve в одном блоке
- **Проверка allowance** — пропуск approve если уже одобрено
- **Порог merge ($2)** — мелкие остатки не мержатся (экономия газа на пыли)
- **Автодозаправка** — автоматическое пополнение BNB из мастер-кошелька

### Советы по экономии

1. Используйте `gwei_multiplier: 1.0` в часы низкой нагрузки сети
2. Установите `max_gas_price_gwei: 5.0` для защиты от дорогих транзакций
3. Настройте approvals один раз (пункт 6) перед началом торговли
4. Увеличьте `split_amount` — комиссия фиксирована, больший объем выгоднее

---

## Настройка Telegram

1. Откройте [@BotFather](https://t.me/BotFather) в Telegram
2. Отправьте `/newbot` и следуйте инструкциям
3. Скопируйте токен бота (формат: `123456789:ABCdefGHI...`)
4. Узнайте свой user_id через [@userinfobot](https://t.me/userinfobot) (отправьте `/start`)
5. Заполните `settings.yaml`:

```yaml
telegram:
  bot_token: "123456789:ABCdefGHIjklMNOpqrsTUVwxyz"
  user_ids: [123456789]
```

Бот отправляет уведомления о старте/финише торговли, стоп-лоссах и предупреждения о низких балансах.

---

## Частые ошибки и решения

| Ошибка | Причина | Решение |
|---|---|---|
| `API key required` | Не указан API ключ | Получите ключ в Discord и впишите в `settings.yaml` |
| `Authentication failed` | Неверный приватный ключ | Проверьте формат ключа (должен начинаться с `0x`) |
| `SDK not initialized` | Не установлен predict-sdk | Выполните `pip install predict-sdk` |
| `Insufficient BNB` | Мало BNB для газа | Пополните BNB (минимум 0.001 BNB) |
| `Order amount too small` | Сумма ниже минимума | Увеличьте `split_amount` в settings.yaml |
| `Rate limit` | Превышен лимит запросов | Уменьшите `threads` или увеличьте задержки |
| `Token expired` | Истекла авторизация | Обычно ре-логин автоматический; проверьте сеть |
| `Merge failed` | Нет пары YES+NO для merge | Убедитесь что есть оба токена (YES и NO) |
| `FileNotFoundError` | Нет privatekeys.txt | Создайте файл `input_data/privatekeys.txt` с ключами |
| `No markets configured` | Пустой markets.yaml | Добавьте маркеты в `input_data/markets.yaml` |

---

## Архитектура (для разработчиков)

```
main.py                       # Точка входа, CLI меню, воркеры
constants.py                  # Константы: адреса, пороги, лимиты
settings.yaml                 # Главный конфиг
input_data/
  markets.yaml                # Список маркетов
  privatekeys.txt             # Приватные ключи (legacy)
  proxies.txt                 # Прокси

config/
  settings.py                 # Загрузка и парсинг конфигурации

modules/
  client.py                   # PredictClient — фасад для всех операций
  browser.py                  # PredictBrowser — HTTP клиент + auth
  api/
    base.py                   # Базовый API клиент, авторизация
    markets.py                # Данные по маркетам
    orders.py                 # Управление ордерами
    positions.py              # Запрос позиций
    accounts.py               # Операции с аккаунтом
    rate_limiter.py           # Rate limiting (240 req/min)
    cache.py                  # TTL кеш для API ответов
  chain/
    gas.py                    # Gas pricing (EIP-1559) + Nonce manager
    operations.py             # On-chain операции (split, merge, redeem)
    balances.py               # Запрос балансов (BNB, USDT, токены)
    multicall.py              # Batch-чтение через Multicall3

core/
  market_maker.py             # Стратегия Split Trading
  limit_trader.py             # Стратегия Limit Trading
  order_manager.py            # Создание и управление ордерами
  position_manager.py         # Управление позициями (merge, sell, claim)
  price_calculator.py         # Расчет цен ордеров

wallet/
  manager.py                  # Загрузка/сохранение кошельков
  models.py                   # Модели данных (WalletInfo)
  crypto.py                   # Шифрование (Fernet + PBKDF2)
  generator.py                # HD генерация из мнемоники (BIP44)
  distributor.py              # Распределение и сбор средств

services/
  database.py                 # JSON база данных (ордера, blacklist)
  telegram.py                 # Telegram уведомления
  entry_prices.py             # Хранение цен входа
  statistics.py               # Статистика торговли

models/
  positions.py                # Модели: Order, Position, MarketEvent

utils/
  logger.py                   # Настройка loguru
  helpers.py                  # Вспомогательные функции
  retry.py                    # Декоратор повторных попыток
```

### Ключевые зависимости

- [predict-sdk](https://pypi.org/project/predict-sdk/) — официальный SDK Predict.fun
- [web3.py](https://pypi.org/project/web3/) — взаимодействие с BNB Chain
- [eth-account](https://pypi.org/project/eth-account/) — управление ключами и подписями
- [aiohttp](https://pypi.org/project/aiohttp/) — асинхронные HTTP запросы
- [loguru](https://pypi.org/project/loguru/) — логирование
- [questionary](https://pypi.org/project/questionary/) — интерактивное CLI меню

---

## Disclaimer

**Данный бот предоставляется исключительно в образовательных целях.**

Торговля на рынках предсказаний сопряжена со значительными финансовыми рисками. Вы можете потерять часть или все вложенные средства. Авторы не несут ответственности за финансовые потери, возникшие в результате использования данного программного обеспечения.

Перед использованием убедитесь, что торговля на рынках предсказаний разрешена в вашей юрисдикции. Используйте на свой страх и риск. Всегда начинайте с небольших сумм и режима `dry_run: true` для тестирования.
