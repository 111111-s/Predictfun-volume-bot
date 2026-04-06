#!/usr/bin/env python3
"""
Predict.fun Split Market Making Bot

Usage:
    python main.py
"""

import asyncio
import sys
from pathlib import Path
from random import shuffle, randint, uniform
from datetime import datetime, timezone

# Fix for aiodns on Windows
if sys.platform == 'win32':
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# Add root to path
ROOT_DIR = Path(__file__).parent
sys.path.insert(0, str(ROOT_DIR))

import questionary
from questionary import Style
from loguru import logger

from config import config, INPUT_DIR
from core import MarketMaker, PositionManager, LimitTrader
from modules.browser import PredictBrowser
from services import telegram, database
from utils.logger import setup_logger
from utils.helpers import async_sleep


BANNER = """
    +-------------------------------------------------------+
    |                                                       |
    |        Nodefarm Predict.fun Trading Bot               |
    |                                                       |
    +-------------------------------------------------------+
"""

MENU_STYLE = Style([
    ('qmark', 'fg:#673ab7 bold'),
    ('question', 'fg:#ffffff bold'),
    ('answer', 'fg:#00ff00 bold'),
    ('pointer', 'fg:#00ff00 bold'),
    ('highlighted', 'fg:#00ff00 bold'),
    ('selected', 'fg:#00ff00'),
    ('separator', 'fg:#808080'),
    ('instruction', 'fg:#808080'),
])

MENU_CHOICES = [
    questionary.Choice("Start split trading", value=1),
    questionary.Choice("Start limit trading", value=8),
    questionary.Choice("Monitor existing orders", value=2),
    questionary.Choice("Stop all positions and merge", value=3),
    questionary.Choice("Sell all positions market", value=4),
    questionary.Choice("Cancel all orders", value=7),
    questionary.Choice("Claim resolved positions", value=10),
    questionary.Choice("Check balances", value=5),
    questionary.Choice("Portfolio overview", value=17),
    questionary.Choice("Set approvals (first time setup)", value=6),
    questionary.Separator("--- Wallet Management ---"),
    questionary.Choice("Import wallets from privatekeys.txt", value=11),
    questionary.Choice("Generate wallets from mnemonic", value=12),
    questionary.Choice("Distribute BNB to sub-wallets", value=13),
    questionary.Choice("Distribute USDT to sub-wallets", value=14),
    questionary.Choice("Collect funds to master wallet", value=15),
    questionary.Choice("List all wallets", value=16),
    questionary.Separator(),
    questionary.Choice("Show all markets (get IDs)", value=9),
    questionary.Separator(),
    questionary.Choice("Exit", value=0),
]


class Stats:
    """Статистика запуска"""
    
    def __init__(self):
        self.total = 0
        self.success = 0
        self.failed = 0
        self.stop_losses = 0
        self._lock = asyncio.Lock()
    
    async def add(self, success: bool, stop_loss: bool = False):
        async with self._lock:
            self.total += 1
            if success:
                self.success += 1
            else:
                self.failed += 1
            if stop_loss:
                self.stop_losses += 1


stats = Stats()


def format_proxy(proxy: str) -> str:
    """
    Convert proxy to aiohttp format.
    
    Supports:
    - ip:port:user:pass -> http://user:pass@ip:port
    - user:pass@ip:port -> http://user:pass@ip:port
    - http://... -> as-is
    """
    if not proxy:
        return proxy
    
    # Already in URL format
    if proxy.startswith("http://") or proxy.startswith("https://"):
        return proxy
    
    # Format: user:pass@ip:port
    if "@" in proxy:
        return f"http://{proxy}"
    
    # Format: ip:port:user:pass
    parts = proxy.split(":")
    if len(parts) == 4:
        ip, port, user, password = parts
        return f"http://{user}:{password}@{ip}:{port}"
    elif len(parts) == 2:
        # Just ip:port, no auth
        return f"http://{proxy}"
    
    # Unknown format, return as-is
    return proxy


def load_accounts() -> list:
    """
    Загрузка акаунтів
    
    Формат privatekeys.txt:
    - privatekey
    - label:privatekey
    - label:privatekey:predict_account (для Smart Wallet)
    
    Для Predict Smart Wallet:
    - privatekey = приватный ключ Privy Wallet (экспортируется из настроек)
    - predict_account = адрес Predict Smart Wallet (deposit address)
    """
    pk_paths = [INPUT_DIR / "privatekeys.txt", Path("privatekeys.txt")]
    private_keys = []
    
    for p in pk_paths:
        if p.exists():
            private_keys = [
                l.strip() for l in p.read_text(encoding='utf-8').splitlines()
                if l.strip() and not l.startswith("#")
            ]
            break
    
    if not private_keys:
        raise FileNotFoundError("privatekeys.txt not found")
    
    # Прокси
    proxy_paths = [INPUT_DIR / "proxies.txt", Path("proxies.txt")]
    proxies = []
    
    for p in proxy_paths:
        if p.exists():
            proxies = [
                format_proxy(l.strip()) for l in p.read_text(encoding='utf-8').splitlines()
                if l.strip() and "ip:port" not in l.lower() and not l.startswith("#")
            ]
            break
    
    # Формуємо акаунти
    accounts = []
    for i, line in enumerate(private_keys):
        label = f"#{i+1}"
        pk = line
        predict_account = None
        
        # Parse line - find predict_account (address = 42 chars with 0x)
        # Format can be: pk, label:pk, label:pk:predict_account, or even more parts
        parts = line.split(":")
        
        # Check if last part is a predict_account address (0x + 40 hex = 42 chars)
        if parts[-1].startswith("0x") and len(parts[-1]) == 42:
            predict_account = parts[-1]
            parts = parts[:-1]  # Remove last part
        
        # Now parts is either [pk], [label, pk], or [label, pk, extra...]
        if len(parts) == 1:
            # Just private key
            pk = parts[0]
        elif len(parts) >= 2:
            # Check if first part is a label (short, not hex)
            if len(parts[0]) < 20 and not parts[0].startswith("0x"):
                label = parts[0]
                # Private key is the rest joined (in case it had : in it somehow)
                pk = parts[1] if len(parts) == 2 else parts[1]
            else:
                # First part is private key
                pk = parts[0]
        
        # Додаємо 0x якщо немає
        if not pk.startswith("0x"):
            pk = "0x" + pk
        
        proxy = proxies[i % len(proxies)] if proxies else None
        accounts.append({
            "private_key": pk,
            "proxy": proxy,
            "label": label,
            "predict_account": predict_account
        })
    
    return accounts


async def trading_worker(semaphore: asyncio.Semaphore, account: dict):
    """Воркер для торгівлі"""
    async with semaphore:
        pk = account["private_key"]
        proxy = account.get("proxy")
        label = account["label"]
        
        browser = None
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=account.get("predict_account"))
            
            mm = MarketMaker(browser=browser, label=label)
            result = await mm.run()
            await stats.add(success=result)
            
            if result:
                logger.opt(colors=True).success(f'[+] <white>{label}</white> | Done')
            else:
                logger.opt(colors=True).warning(f'[!] <white>{label}</white> | Issues')
                
        except Exception as e:
            await stats.add(success=False)
            logger.opt(colors=True).error(f'[X] <white>{label}</white> | {e}')
        
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass


async def limit_trading_worker(semaphore: asyncio.Semaphore, account: dict):
    """Воркер для лімітної торгівлі"""
    async with semaphore:
        pk = account["private_key"]
        proxy = account.get("proxy")
        label = account["label"]
        
        browser = None
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=account.get("predict_account"))
            
            trader = LimitTrader(browser=browser, label=label)
            result = await trader.run()
            await stats.add(success=result)
            
            if result:
                logger.opt(colors=True).success(f'[+] <white>{label}</white> | Limit trading done')
            else:
                logger.opt(colors=True).warning(f'[!] <white>{label}</white> | Issues')
                
        except Exception as e:
            await stats.add(success=False)
            logger.opt(colors=True).error(f'[X] <white>{label}</white> | {e}')
        
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass


async def monitor_worker(semaphore: asyncio.Semaphore, account: dict):
    """Воркер для моніторингу існуючих ордерів"""
    async with semaphore:
        pk = account["private_key"]
        proxy = account.get("proxy")
        label = account["label"]
        
        browser = None
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=account.get("predict_account"))
            
            mm = MarketMaker(browser=browser, label=label)
            result = await mm.monitor_existing_only()
            await stats.add(success=result)
            
            if result:
                logger.opt(colors=True).success(f'[+] <white>{label}</white> | Monitoring complete')
            else:
                logger.opt(colors=True).info(f'[*] <white>{label}</white> | No open orders')
                
        except Exception as e:
            await stats.add(success=False)
            logger.opt(colors=True).error(f'[X] <white>{label}</white> | {e}')
        
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass


async def merge_worker(semaphore: asyncio.Semaphore, account: dict):
    """Воркер для merge позицій"""
    async with semaphore:
        pk = account["private_key"]
        proxy = account.get("proxy")
        label = account["label"]
        
        browser = None
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=account.get("predict_account"))
            
            if not await browser.authenticate():
                logger.warning(f'[!] {label} | Auth failed')
                return
            
            pm = PositionManager(browser=browser, label=label)
            result = await pm.stop_and_merge()
            
            logger.opt(colors=True).success(
                f'[+] <white>{label}</white> | Cancelled: {result["cancelled"]}, '
                f'Merged: {result["merged"]}, Failed: {result["failed"]}'
            )
                
        except Exception as e:
            logger.opt(colors=True).error(f'[X] <white>{label}</white> | {e}')
        
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass


async def sell_worker(semaphore: asyncio.Semaphore, account: dict):
    """Воркер для market sell позицій"""
    async with semaphore:
        pk = account["private_key"]
        proxy = account.get("proxy")
        label = account["label"]
        
        browser = None
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=account.get("predict_account"))
            
            if not await browser.authenticate():
                logger.warning(f'[!] {label} | Auth failed')
                return
            
            pm = PositionManager(browser=browser, label=label)
            result = await pm.stop_and_sell()
            
            logger.opt(colors=True).success(
                f'[+] <white>{label}</white> | Cancelled: {result["cancelled"]}, '
                f'Sold: {result["sold"]}, Failed: {result["failed"]}'
            )
                
        except Exception as e:
            logger.opt(colors=True).error(f'[X] <white>{label}</white> | {e}')
        
        finally:
            if browser:
                try:
                    await browser.close()
                except:
                    pass


async def run_trading(accounts: list):
    """Запуск торгівлі"""
    if config.shuffle_wallets:
        shuffle(accounts)
    
    markets_range = config.markets.markets_per_account
    logger.info(f"Accounts: {len(accounts)} | Threads: {config.threads}")
    logger.info(f"Markets: {len(config.markets.list)} | Per account: {markets_range[0]}-{markets_range[1]}")
    
    # Telegram
    await telegram.send_start(
        accounts=len(accounts),
        markets=len(config.markets.list),
        threads=config.threads,
        cycles=config.markets.cycles,
        sl_enabled=config.stop_loss.enabled,
        sl_percent=config.stop_loss.percent
    )
    
    semaphore = asyncio.Semaphore(config.threads)
    
    tasks = []
    for i, acc in enumerate(accounts):
        if i > 0 and i % config.threads == 0:
            await async_sleep(config.sleep.between_threads)
        
        task = asyncio.create_task(trading_worker(semaphore, acc))
        tasks.append(task)
    
    await asyncio.gather(*tasks)
    
    # Фініш
    await telegram.send_finish(
        total=stats.total,
        success=stats.success,
        failed=stats.failed,
        stop_losses=stats.stop_losses
    )
    
    logger.success(f"Done! Success: {stats.success}/{stats.total}")


async def run_limit_trading(accounts: list):
    """Запуск лімітної торгівлі"""
    if config.shuffle_wallets:
        shuffle(accounts)
    
    logger.info(f"Accounts: {len(accounts)} | Threads: {config.threads}")
    logger.info(f"Markets: {len(config.markets.list)} | Mode: LIMIT TRADING")
    
    # Telegram
    await telegram.send_start(
        accounts=len(accounts),
        markets=len(config.markets.list),
        threads=config.threads,
        cycles=config.markets.cycles,
        sl_enabled=config.stop_loss.enabled,
        sl_percent=config.stop_loss.percent
    )
    
    semaphore = asyncio.Semaphore(config.threads)
    
    tasks = []
    for i, acc in enumerate(accounts):
        if i > 0 and i % config.threads == 0:
            await async_sleep(config.sleep.between_threads)
        
        task = asyncio.create_task(limit_trading_worker(semaphore, acc))
        tasks.append(task)
    
    await asyncio.gather(*tasks)
    
    # Фініш
    await telegram.send_finish(
        total=stats.total,
        success=stats.success,
        failed=stats.failed,
        stop_losses=stats.stop_losses
    )
    
    logger.success(f"Done! Success: {stats.success}/{stats.total}")


async def run_monitor(accounts: list):
    """Моніторинг існуючих ордерів"""
    logger.info(f"Accounts: {len(accounts)} | Monitoring existing orders")
    
    semaphore = asyncio.Semaphore(config.threads)
    
    tasks = []
    for acc in accounts:
        task = asyncio.create_task(monitor_worker(semaphore, acc))
        tasks.append(task)
    
    await asyncio.gather(*tasks)
    
    logger.success("Monitoring complete")


async def run_merge(accounts: list):
    """Відміна ордерів і merge позицій"""
    logger.info(f"Accounts: {len(accounts)} | Stopping & merging")
    
    semaphore = asyncio.Semaphore(config.threads)
    
    tasks = []
    for acc in accounts:
        task = asyncio.create_task(merge_worker(semaphore, acc))
        tasks.append(task)
    
    await asyncio.gather(*tasks)
    
    logger.success("All positions merged")


async def run_sell(accounts: list):
    """Відміна ордерів і market sell"""
    logger.info(f"Accounts: {len(accounts)} | Stopping & selling")
    
    semaphore = asyncio.Semaphore(config.threads)
    
    tasks = []
    for acc in accounts:
        task = asyncio.create_task(sell_worker(semaphore, acc))
        tasks.append(task)
    
    await asyncio.gather(*tasks)
    
    logger.success("All positions sold")


async def check_balances(accounts: list):
    """Перевірити баланси всіх акаунтів"""
    logger.info(f"Checking {len(accounts)} accounts...")
    
    for acc in accounts:
        pk = acc["private_key"]
        proxy = acc.get("proxy")
        label = acc["label"]
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=acc.get("predict_account"))
            
            if await browser.authenticate():
                balance = await browser.get_usdt_balance()
                logger.info(f"[{label}] {browser.address[:12]}... : ${balance:.2f}")
            else:
                logger.warning(f"[{label}] Auth failed")
            
            await browser.close()
            
        except Exception as e:
            logger.error(f"[{label}] Error: {e}")
        
        await asyncio.sleep(1)


async def set_approvals(accounts: list):
    """Set USDT approvals for all accounts (first time setup)"""
    logger.info(f"Setting approvals for {len(accounts)} accounts...")
    
    for acc in accounts:
        pk = acc["private_key"]
        proxy = acc.get("proxy")
        label = acc["label"]
        
        try:
            browser = PredictBrowser(private_key=pk, proxy=proxy, predict_account=acc.get("predict_account"))
            
            if not await browser.authenticate():
                logger.warning(f"[{label}] Auth failed")
                await browser.close()
                continue
            
            logger.info(f"[{label}] Setting USDT approvals...")
            
            success = await browser.set_approvals(is_yield_bearing=False)
            
            if success:
                logger.success(f"[{label}] Approvals set successfully!")
            else:
                logger.error(f"[{label}] Failed to set approvals")
            
            await browser.close()
            
        except Exception as e:
            logger.error(f"[{label}] Error: {e}")
        
        await asyncio.sleep(1)
    
    logger.success("Done setting approvals!")


def show_menu() -> int:
    """Показати меню і отримати вибір"""
    print(BANNER)
    
    try:
        choice = questionary.select(
            "Select action:",
            choices=MENU_CHOICES,
            style=MENU_STYLE,
            instruction="(Use arrow keys)",
            qmark=">>",
        ).ask()
        
        return choice if choice is not None else 0
        
    except (KeyboardInterrupt, EOFError):
        print("\n    Exiting...")
        return 0


async def cancel_all_orders(accounts: list):
    """Отменить все ордера"""
    logger.info(f"Cancelling orders for {len(accounts)} accounts...")
    
    for account in accounts:
        label = account["label"]
        private_key = account["private_key"]
        proxy = account.get("proxy")
        predict_account = account.get("predict_account")
        
        browser = None
        try:
            browser = PredictBrowser(
                private_key=private_key,
                proxy=proxy,
                predict_account=predict_account
            )
            
            if not await browser.authenticate():
                logger.error(f"[{label}] Authentication failed")
                continue
            
            # Get all open orders (filtered client-side)
            orders = await browser.get_orders(status="open")
            if not orders:
                logger.info(f"[{label}] No open orders to cancel")
                continue
            
            logger.info(f"[{label}] Found {len(orders)} open orders, cancelling...")
            
            cancelled = 0
            for order in orders:
                try:
                    # Use SDK cancel with full order data
                    if order.raw_data:
                        is_neg_risk = order.raw_data.get("isNegRisk", False)
                        is_yield_bearing = order.raw_data.get("isYieldBearing", False)
                        result = await browser.cancel_order_by_data(
                            order.raw_data,
                            is_yield_bearing=is_yield_bearing,
                            is_neg_risk=is_neg_risk
                        )
                        if result:
                            cancelled += 1
                            order_hash = order.order_hash[:16] if order.order_hash else order.order_id[:16]
                            logger.success(f"[{label}] Cancelled order {order_hash}...")
                    else:
                        logger.warning(f"[{label}] No raw_data for order, skipping")
                except Exception as e:
                    logger.warning(f"[{label}] Failed to cancel order: {e}")
            
            logger.success(f"[{label}] Cancelled {cancelled}/{len(orders)} orders")
            
        except Exception as e:
            logger.error(f"[{label}] Error: {e}")
        
        finally:
            if browser:
                await browser.close()
    
    logger.success("Done cancelling orders!")


async def claim_resolved_positions(accounts: list):
    """Клеймить все resolved позиции"""
    from modules.browser import PredictBrowser
    
    logger.info(f"Claiming resolved positions for {len(accounts)} accounts...")
    
    for account in accounts:
        label = account["label"]
        browser = None
        
        try:
            browser = PredictBrowser(
                private_key=account["private_key"],
                proxy=account.get("proxy"),
                predict_account=account.get("predict_account")
            )
            
            # Авторизация
            if not await browser.authenticate():
                logger.error(f"[{label}] Auth failed!")
                continue
            
            logger.info(f"[{label}] Checking positions...")
            
            # Получаем все позиции
            positions = await browser.get_positions()
            if not positions:
                logger.info(f"[{label}] No positions found")
                continue
            
            # Фильтруем resolved позиции с выигрышем
            resolved_positions = []
            for pos in positions:
                # Проверяем статус маркета
                market = await browser.get_market(str(pos.market_id))
                if not market:
                    continue
                
                status = market.get("status", "").upper()
                if status != "RESOLVED":
                    continue
                
                # Проверяем выиграла ли наша позиция
                resolution = market.get("resolution", {})
                winning_outcome = resolution.get("name", "").lower()
                our_outcome = "yes" if pos.is_yes else "no"
                
                if winning_outcome == our_outcome and pos.balance > 0:
                    resolved_positions.append({
                        "position": pos,
                        "market": market,
                        "outcome": our_outcome,
                        "title": market.get("title", market.get("question", "Unknown"))[:40]
                    })
            
            if not resolved_positions:
                logger.info(f"[{label}] No resolved winning positions to claim")
                continue
            
            logger.info(f"[{label}] Found {len(resolved_positions)} resolved positions to claim:")
            
            claimed = 0
            for rp in resolved_positions:
                pos = rp["position"]
                market = rp["market"]
                title = rp["title"]
                
                condition_id = market.get("conditionId", "")
                is_neg_risk = market.get("isNegRisk", False)
                is_yield_bearing = market.get("isYieldBearing", False)
                index_set = 1 if pos.is_yes else 2
                
                logger.info(f"[{label}] Claiming {pos.balance:.2f} {rp['outcome'].upper()} from '{title}'...")
                
                try:
                    success = await browser.redeem_positions(
                        condition_id=condition_id,
                        index_set=index_set,
                        amount=pos.balance,
                        is_neg_risk=is_neg_risk,
                        is_yield_bearing=is_yield_bearing
                    )
                    
                    if success:
                        claimed += 1
                        logger.success(f"[{label}] Claimed ${pos.balance:.2f} from '{title}'")
                    else:
                        logger.warning(f"[{label}] Failed to claim from '{title}'")
                        
                except Exception as e:
                    logger.error(f"[{label}] Claim error: {e}")
            
            logger.success(f"[{label}] Claimed {claimed}/{len(resolved_positions)} positions")
            
        except Exception as e:
            logger.error(f"[{label}] Error: {e}")
        
        finally:
            if browser:
                await browser.close()
    
    logger.success("Done claiming resolved positions!")


async def show_all_markets():
    """Показать маркеты из markets.yaml с их ID"""
    from modules.browser import PredictBrowser
    import logging
    
    browser = None
    try:
        accounts = load_accounts()
        if not accounts:
            logger.error("No accounts found!")
            return
        
        browser = PredictBrowser(
            private_key=accounts[0]["private_key"],
            proxy=accounts[0].get("proxy"),
            predict_account=accounts[0].get("predict_account")
        )
        
        configured_markets = config.markets.list
        if not configured_markets:
            logger.warning("No markets configured in markets.yaml!")
            return
        
        # Дедупликация по market_id
        seen = set()
        unique_markets = []
        for m in configured_markets:
            if m.market_id not in seen:
                seen.add(m.market_id)
                unique_markets.append(m)
        
        logger.info(f"Fetching {len(unique_markets)} markets...")
        
        # Собираем результаты
        results = []
        
        for i, market_config in enumerate(unique_markets, 1):
            try:
                choice_index = int(market_config.choice) if market_config.choice and market_config.choice.isdigit() else 0
                market = await browser.get_market(market_config.market_id, choice_index)
                
                if market:
                    market_id = market.get("id", "?")
                    title = market.get("title", market.get("question", "Unknown"))
                    question = market.get("question", "")
                    # Если title короткий (Arsenal, Man City) - добавляем question
                    if question and title != question and len(title) < 30:
                        display = f"{question[:50]} - {title}"
                    else:
                        display = title
                    results.append(f'  - "{market_id}"   # {display}')
                else:
                    results.append(f'  - "?"   # {market_config.market_id} (NOT FOUND)')
                    
            except Exception as e:
                results.append(f'  - "?"   # {market_config.market_id} (ERROR)')
        
        # Выводим всё в конце
        print("\n" + "="*60)
        print("            CONFIGURED MARKETS")
        print("="*60 + "\n")
        
        for line in results:
            print(line)
        
        print("\n" + "="*60)
        print("Copy ID to markets.yaml:  - \"1562\"")
        print("="*60 + "\n")
        
    except Exception as e:
        logger.error(f"Error: {e}")
    
    finally:
        if browser:
            await browser.close()


async def portfolio_overview(accounts: list):
    """Показать обзор портфеля по всем кошелькам"""
    logger.info(f"Portfolio overview for {len(accounts)} accounts...")

    total_usdt = 0.0
    total_bnb = 0.0
    total_orders = 0
    total_positions = 0

    rows = []

    for acc in accounts:
        label = acc["label"]
        browser = None
        try:
            browser = PredictBrowser(
                private_key=acc["private_key"],
                proxy=acc.get("proxy"),
                predict_account=acc.get("predict_account")
            )

            if not await browser.authenticate():
                rows.append((label, "AUTH FAIL", "", "", "", ""))
                continue

            usdt = await browser.get_usdt_balance()
            bnb = await browser.get_bnb_balance()

            orders = await browser.get_orders(status="open")
            positions = await browser.get_positions()

            n_orders = len(orders) if orders else 0
            n_positions = len(positions) if positions else 0

            total_usdt += usdt
            total_bnb += bnb
            total_orders += n_orders
            total_positions += n_positions

            rows.append((label, f"${usdt:.2f}", f"{bnb:.4f}", str(n_orders), str(n_positions), browser.address[:12] + "..."))

        except Exception as e:
            rows.append((label, "ERR", "", "", "", str(e)[:20]))
        finally:
            if browser:
                await browser.close()

    # Выводим таблицу
    print("\n" + "=" * 75)
    print("                      PORTFOLIO OVERVIEW")
    print("=" * 75)
    print(f"{'Wallet':<12} {'USDT':>10} {'BNB':>10} {'Orders':>8} {'Positions':>10} {'Address':<16}")
    print("-" * 75)

    for row in rows:
        print(f"{row[0]:<12} {row[1]:>10} {row[2]:>10} {row[3]:>8} {row[4]:>10} {row[5]:<16}")

    print("-" * 75)
    print(f"{'TOTAL':<12} {'$' + f'{total_usdt:.2f}':>10} {f'{total_bnb:.4f}':>10} {str(total_orders):>8} {str(total_positions):>10}")
    print("=" * 75 + "\n")


async def wallet_import_legacy():
    """Импортировать кошельки из privatekeys.txt в wallets.json"""
    from wallet import WalletManager

    wm = WalletManager()
    wallets = wm.import_from_legacy()

    if not wallets:
        logger.warning("No wallets found in privatekeys.txt")
        return

    # Спрашиваем про пароль
    use_password = questionary.confirm(
        "Encrypt wallets.json with password?",
        default=False,
        style=MENU_STYLE
    ).ask()

    password = None
    if use_password:
        password = questionary.password(
            "Enter password:",
            style=MENU_STYLE
        ).ask()

    wm.save_wallets(wallets, password=password)
    logger.success(f"Imported {len(wallets)} wallets to wallets.json")
    wm.list_wallets(wallets)


async def wallet_generate_mnemonic():
    """Генерация кошельков из мнемоники"""
    from wallet import WalletManager
    from wallet.generator import generate_mnemonic, generate_from_mnemonic

    # Спрашиваем: новая или существующая мнемоника
    use_existing = questionary.confirm(
        "Use existing mnemonic phrase?",
        default=False,
        style=MENU_STYLE
    ).ask()

    if use_existing:
        mnemonic = questionary.text(
            "Enter your 12-word mnemonic:",
            style=MENU_STYLE
        ).ask()
    else:
        mnemonic = generate_mnemonic()
        print(f"\n  YOUR MNEMONIC (SAVE IT!):\n  {mnemonic}\n")
        questionary.confirm("I have saved my mnemonic", default=False, style=MENU_STYLE).ask()

    count = questionary.text(
        "How many wallets to generate?",
        default="5",
        style=MENU_STYLE
    ).ask()

    wallets = generate_from_mnemonic(mnemonic, int(count))

    # Загружаем существующие кошельки
    wm = WalletManager()
    try:
        existing = wm.load_wallets()
    except Exception:
        existing = []

    # Добавляем новые
    existing.extend(wallets)

    # Сохраняем
    password = None
    use_password = questionary.confirm("Encrypt with password?", default=False, style=MENU_STYLE).ask()
    if use_password:
        password = questionary.password("Enter password:", style=MENU_STYLE).ask()

    wm.save_wallets(existing, password=password)
    logger.success(f"Generated {len(wallets)} wallets")
    wm.list_wallets(wallets)


async def wallet_distribute(token: str, accounts: list):
    """Распределить BNB или USDT из мастер-кошелька"""
    from wallet import WalletManager, FundDistributor

    wm = WalletManager()
    try:
        wallets = wm.load_wallets()
    except Exception:
        logger.error("No wallets.json found. Run 'Import wallets' first.")
        return

    master = wm.get_master(wallets)
    if not master:
        logger.error("No master wallet set! Use 'List wallets' and set a master first.")
        return

    subs = [w for w in wallets if not w.is_master]
    if not subs:
        logger.error("No sub-wallets found")
        return

    amount = questionary.text(
        f"Amount of {token} per wallet:",
        default="0.001" if token == "BNB" else "10",
        style=MENU_STYLE
    ).ask()

    # Создаем web3 для операций
    from web3 import Web3
    rpc = config.rpcs[0] if config.rpcs else "https://bsc-dataseed1.binance.org"
    w3 = Web3(Web3.HTTPProvider(rpc))

    distributor = FundDistributor(w3)

    if token == "BNB":
        results = await distributor.distribute_bnb(master, subs, float(amount))
    else:
        # Нужен контракт USDT
        logger.info("USDT distribution requires SDK. Use split trading for now.")
        return

    success = sum(1 for v in results.values() if v)
    logger.success(f"Distributed to {success}/{len(subs)} wallets")


async def wallet_collect(accounts: list):
    """Собрать средства на мастер-кошелёк"""
    from wallet import WalletManager, FundDistributor

    wm = WalletManager()
    try:
        wallets = wm.load_wallets()
    except Exception:
        logger.error("No wallets.json found")
        return

    master = wm.get_master(wallets)
    if not master:
        logger.error("No master wallet set!")
        return

    subs = [w for w in wallets if not w.is_master]

    from web3 import Web3
    rpc = config.rpcs[0] if config.rpcs else "https://bsc-dataseed1.binance.org"
    w3 = Web3(Web3.HTTPProvider(rpc))

    distributor = FundDistributor(w3)
    results = await distributor.collect_bnb(subs, master)

    success = sum(1 for v in results.values() if v)
    logger.success(f"Collected from {success}/{len(subs)} wallets")


async def wallet_list():
    """Показать список кошельков"""
    from wallet import WalletManager

    wm = WalletManager()
    try:
        wallets = wm.load_wallets()
    except Exception:
        logger.warning("No wallets.json found. Using legacy privatekeys.txt")
        try:
            wallets = wm.import_from_legacy()
        except Exception:
            logger.error("No wallets found at all")
            return

    wm.list_wallets(wallets)

    # Предложить установить мастер
    if not wm.get_master(wallets):
        set_master = questionary.confirm(
            "No master wallet set. Set one now?",
            default=True,
            style=MENU_STYLE
        ).ask()

        if set_master:
            labels = [w.label for w in wallets]
            label = questionary.select(
                "Select master wallet:",
                choices=labels,
                style=MENU_STYLE
            ).ask()

            wallets = wm.set_master(wallets, label)
            wm.save_wallets(wallets)
            logger.success(f"Master wallet set: {label}")


async def run_action(choice: int, accounts: list):
    """Виконати вибрану дію"""
    if choice == 1:
        await run_trading(accounts)
    elif choice == 2:
        await run_monitor(accounts)
    elif choice == 3:
        await run_merge(accounts)
    elif choice == 4:
        await run_sell(accounts)
    elif choice == 5:
        await check_balances(accounts)
    elif choice == 6:
        await set_approvals(accounts)
    elif choice == 7:
        await cancel_all_orders(accounts)
    elif choice == 8:
        await run_limit_trading(accounts)
    elif choice == 9:
        await show_all_markets()
    elif choice == 10:
        await claim_resolved_positions(accounts)
    elif choice == 11:
        await wallet_import_legacy()
    elif choice == 12:
        await wallet_generate_mnemonic()
    elif choice == 13:
        await wallet_distribute("BNB", accounts)
    elif choice == 14:
        await wallet_distribute("USDT", accounts)
    elif choice == 15:
        await wallet_collect(accounts)
    elif choice == 16:
        await wallet_list()
    elif choice == 17:
        await portfolio_overview(accounts)

    # Очистка
    await telegram.close()


def main():
    """Точка входу"""
    import argparse
    
    parser = argparse.ArgumentParser(description="Predict.fun Trading Bot")
    parser.add_argument(
        "--action", "-a",
        type=int,
        choices=list(range(0, 18)),
        help=(
            "Action: 1=Split, 2=Monitor, 3=Merge, 4=Sell, 5=Balances, 6=Approvals, "
            "7=Cancel, 8=Limit, 9=Markets, 10=Claim, 11=ImportWallets, 12=GenMnemonic, "
            "13=DistBNB, 14=DistUSDT, 15=Collect, 16=ListWallets, 17=Portfolio"
        )
    )
    args = parser.parse_args()
    
    # Налаштовуємо логер
    setup_logger()
    
    # Показуємо меню або використовуємо аргумент
    if args.action:
        print(BANNER)
        choice = args.action
    else:
        choice = show_menu()
    
    if choice == 0:
        return
    
    print()
    
    # Завантаження акаунтів
    try:
        accounts = load_accounts()
    except FileNotFoundError as e:
        logger.error(str(e))
        return
    
    # Запускаємо async частину
    asyncio.run(run_action(choice, accounts))


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.warning("Interrupted")
    except Exception as e:
        logger.error(f"Fatal: {e}")
        raise
