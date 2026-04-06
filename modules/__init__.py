"""
Modules for Predict.fun bot
"""

from .client import PredictClient
from .api.base import PredictAPIError, AuthTokenExpiredError

# Backwards compatibility alias
PredictBrowser = PredictClient

__all__ = ["PredictClient", "PredictBrowser", "PredictAPIError", "AuthTokenExpiredError"]
