"""
Price Calculator for Predict.fun

Расчёт цен для лимитных ордеров и стоп-лоссов.
Точная копия логики Opinion.trade бота.
"""

from loguru import logger

from models import OrderBook, OrderSide
from config import config


class PriceCalculator:
    """Калькулятор цен для ордеров"""
    
    def calculate_sell_price(self, book: OrderBook, side: OrderSide) -> float:
        """
        Рассчитать цену продажи для лучшего места в стакане.
        
        Ставим цену чуть ниже лучшего ask (или на уровне если мы одни).
        """
        sell_step = config.limits.sell_price_step / 100  # Конвертируем центы в доллары
        min_spread = config.limits.min_spread / 100
        
        best_ask = book.best_ask
        best_bid = book.best_bid
        
        # Базовая цена - чуть ниже текущего ask
        target_price = best_ask - sell_step
        
        # Но не ниже bid + min_spread (защита от самопокупки)
        min_price = best_bid + min_spread
        
        # Финальная цена
        price = max(target_price, min_price)
        
        # Ограничения 0.01 - 0.99
        price = max(0.01, min(0.99, price))
        
        return round(price, 4)
    
    def check_stop_loss(
        self, 
        current_bid: float, 
        entry_price: float, 
        sl_percent: float
    ) -> bool:
        """
        Проверить сработал ли стоп-лосс.
        
        Args:
            current_bid: Текущий лучший bid в стакане
            entry_price: Наша цена входа
            sl_percent: Процент падения для срабатывания SL
            
        Returns:
            True если нужно срабатывать SL
        """
        if entry_price <= 0:
            return False
        
        # Процент падения от цены входа
        drop_percent = ((entry_price - current_bid) / entry_price) * 100
        
        return drop_percent >= sl_percent
    
    def should_repost(
        self, 
        current_price: float, 
        book: OrderBook, 
        side: OrderSide
    ) -> bool:
        """
        Проверить нужно ли перевыставить ордер.
        
        Перевыставляем если появился лучший ask (ниже нашего).
        """
        if not config.limits.repost_if_not_best:
            return False
        
        sell_step = config.limits.sell_price_step / 100
        
        # Лучшая цена в стакане
        best_price = self.calculate_sell_price(book, side)
        
        # Перевыставляем если можем поставить лучше
        # (т.е. best_price ниже нашей текущей на шаг или больше)
        return best_price < current_price - sell_step
    
    def calculate_shares_from_amount(
        self, 
        amount_usdt: float, 
        yes_price: float, 
        no_price: float
    ) -> tuple:
        """
        Рассчитать количество шар для покупки.
        
        При split стратегии покупаем равное количество YES и NO.
        
        Args:
            amount_usdt: Общая сумма USDT
            yes_price: Цена YES
            no_price: Цена NO
            
        Returns:
            (yes_shares, no_shares)
        """
        # Делим сумму пополам
        half = amount_usdt / 2
        
        # Количество токенов
        yes_shares = half / yes_price if yes_price > 0 else 0
        no_shares = half / no_price if no_price > 0 else 0
        
        return (yes_shares, no_shares)
    
    def estimate_profit(
        self,
        yes_shares: float,
        no_shares: float,
        yes_sell_price: float,
        no_sell_price: float,
        yes_buy_price: float,
        no_buy_price: float
    ) -> float:
        """
        Оценить потенциальную прибыль.
        
        Profit = (YES_sell + NO_sell) - (YES_buy + NO_buy)
        """
        revenue = yes_shares * yes_sell_price + no_shares * no_sell_price
        cost = yes_shares * yes_buy_price + no_shares * no_buy_price
        
        return revenue - cost


# Синглтон
price_calculator = PriceCalculator()
