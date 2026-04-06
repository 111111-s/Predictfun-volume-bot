"""
Predict.fun API - Account & Categories endpoints

GET /account, POST /referral, GET /categories
"""

from typing import Optional, Dict, List
from loguru import logger

from .base import PredictAPIError


class AccountsMixin:
    """Миксин для работы с аккаунтом и категориями"""

    # ==========================================
    # ACCOUNT
    # ==========================================

    async def get_account(self) -> Dict:
        """
        GET /account
        Get connected account information.
        """
        return await self._request("GET", "/account", require_auth=True)

    async def set_referral(self, referral_code: str) -> bool:
        """
        POST /referral
        Set a referral code.
        """
        try:
            await self._request("POST", "/referral", data={
                "code": referral_code
            }, require_auth=True)
            return True
        except PredictAPIError:
            return False

    # ==========================================
    # CATEGORIES
    # ==========================================

    async def get_categories(self) -> List[Dict]:
        """
        GET /categories
        Get all categories.
        """
        result = await self._request("GET", "/categories")
        return result.get("categories", result.get("data", []))

    async def get_category(self, slug: str) -> Optional[Dict]:
        """
        GET /categories/{slug}
        Get category by slug.
        """
        try:
            return await self._request("GET", f"/categories/{slug}")
        except PredictAPIError:
            return None
