"""
Predict.fun unified client - drop-in replacement for PredictBrowser

Собирает все миксины в единый класс PredictClient.
"""

from .api.base import BaseAPIClient, PredictAPIError, AuthTokenExpiredError
from .api.markets import MarketsMixin
from .api.orders import OrdersMixin
from .api.positions import PositionsMixin
from .api.accounts import AccountsMixin
from .chain.balances import BalancesMixin
from .chain.operations import ChainOperationsMixin


class PredictClient(
    BaseAPIClient,
    MarketsMixin, OrdersMixin, PositionsMixin, AccountsMixin,
    BalancesMixin, ChainOperationsMixin
):
    """Unified Predict.fun client - drop-in replacement for PredictBrowser"""
    pass
