"""Legacy compatibility - import from modules.client instead"""
from .client import PredictClient as PredictBrowser
from .api.base import PredictAPIError, AuthTokenExpiredError

__all__ = ["PredictBrowser", "PredictAPIError", "AuthTokenExpiredError"]
