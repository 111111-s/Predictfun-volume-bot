"""
Predict.fun API Client - Базовый класс

Авторизация, сессия, запросы к API.
"""

import aiohttp
import asyncio
from typing import Optional, Dict
from datetime import datetime
from loguru import logger
from eth_account import Account
from eth_account.messages import encode_defunct

# Official Predict.fun SDK (for on-chain operations)
# Optional - only needed for merge/redeem
try:
    from predict_sdk import (
        OrderBuilder,
        OrderBuilderOptions,
        ChainId,
    )
    SDK_AVAILABLE = True
except ImportError:
    SDK_AVAILABLE = False
    OrderBuilder = None
    OrderBuilderOptions = None
    ChainId = None
    logger.warning("predict-sdk not installed, merge/redeem disabled")

from config import config


class PredictAPIError(Exception):
    """Predict API error"""
    pass


class AuthTokenExpiredError(Exception):
    """Raised when JWT token is invalid/expired (401 Unauthorized)"""
    pass


class BaseAPIClient:
    """
    Predict.fun API Client - базовый класс

    Uses official predict-sdk for order signing and on-chain operations.
    Uses REST API for off-chain order submission and data queries.

    Supports two modes:
    1. EOA mode: private_key is the trading wallet
    2. Predict Account mode: private_key is Privy Wallet, predict_account is Smart Wallet
    """

    def __init__(self, private_key: str, proxy: Optional[str] = None, predict_account: Optional[str] = None):
        self._private_key = private_key
        self._account = Account.from_key(private_key)
        self._signer_address = self._account.address

        # If predict_account is set, use it as the trading address (Smart Wallet mode)
        self._predict_account = predict_account
        self._address = predict_account if predict_account else self._account.address

        self._proxy = proxy
        self._session: Optional[aiohttp.ClientSession] = None
        self._jwt_token: Optional[str] = None
        self._jwt_expires: Optional[datetime] = None

        # API URL from config (v1 API)
        base = config.predict.api_url or "https://api.predict.fun"
        # Ensure we use v1 API
        self._base_url = base.rstrip("/") + "/v1"
        self._api_key = config.predict.api_key

        # Initialize SDK OrderBuilder
        self._order_builder: Optional[OrderBuilder] = None
        self._init_sdk()

    @property
    def address(self) -> str:
        return self._address

    def _init_sdk(self):
        """Initialize predict-sdk OrderBuilder"""
        if not SDK_AVAILABLE:
            self._order_builder = None
            return

        try:
            # Determine chain ID from config
            chain_id = ChainId.BNB_MAINNET
            if "testnet" in self._base_url.lower():
                chain_id = ChainId.BNB_TESTNET

            # Build options - include predict_account if using Smart Wallet
            options = None
            if self._predict_account:
                options = OrderBuilderOptions(predict_account=self._predict_account)
                logger.debug(f"Using Predict Account (Smart Wallet): {self._predict_account[:12]}...")

            # Create OrderBuilder with signer
            self._order_builder = OrderBuilder.make(
                chain_id=chain_id,
                signer=self._private_key,
                options=options
            )
            logger.debug(f"SDK initialized for chain {chain_id.name}")
        except Exception as e:
            logger.warning(f"SDK initialization failed: {e}")
            self._order_builder = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """Get or create aiohttp session"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """Close session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _get_headers(self, include_jwt: bool = True) -> Dict[str, str]:
        """Get request headers"""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # API key is REQUIRED for all Predict.fun API requests
        if self._api_key:
            headers["X-Api-Key"] = self._api_key

        # JWT token for authenticated account operations
        if include_jwt and self._jwt_token:
            headers["Authorization"] = f"Bearer {self._jwt_token}"

        return headers

    async def _request(
        self,
        method: str,
        endpoint: str,
        data: Optional[Dict] = None,
        params: Optional[Dict] = None,
        require_auth: bool = False,
        include_jwt: bool = True
    ) -> Dict:
        """
        Make API request.

        Args:
            method: HTTP method
            endpoint: API endpoint (e.g., /markets)
            data: JSON body data
            params: Query parameters
            require_auth: If True, ensure authenticated first
            include_jwt: If True, include JWT token in headers (if available)
        """
        if require_auth:
            await self.ensure_authenticated()

        session = await self._get_session()
        url = f"{self._base_url}{endpoint}"

        try:
            async with session.request(
                method,
                url,
                json=data,
                params=params,
                headers=self._get_headers(include_jwt=include_jwt),
                proxy=self._proxy
            ) as response:

                # Handle empty responses
                text = await response.text()
                if not text:
                    if response.status < 400:
                        return {}
                    raise PredictAPIError(f"Empty response with status {response.status}")

                try:
                    result = await response.json()
                except:
                    raise PredictAPIError(f"Invalid JSON: {text[:200]}")

                # Handle errors
                if response.status == 401:
                    # Token expired/invalid - raise special exception for auto-relogin
                    error_msg = result.get("message", "Unauthorized")
                    logger.warning(f"Auth token expired: {error_msg}")
                    raise AuthTokenExpiredError(f"Token expired: {error_msg}")

                if response.status >= 400:
                    # Extract full error message including description
                    error_msg = result.get("message", "Unknown error")
                    error_details = result.get("error", {})
                    if isinstance(error_details, dict):
                        description = error_details.get("description", "")
                        if description:
                            error_msg = f"{error_msg}: {description}"
                    elif error_details:
                        error_msg = f"{error_msg}: {error_details}"
                    logger.warning(f"API error response: {result}")
                    raise PredictAPIError(f"API error {response.status}: {error_msg}")

                return result

        except aiohttp.ClientError as e:
            # Avoid leaking proxy credentials in error message
            error_type = type(e).__name__
            raise PredictAPIError(f"Request failed: {error_type} - check network/proxy")

    # ==========================================
    # AUTHORIZATION
    # Based on: https://dev.predict.fun/ - Authorization section
    # ==========================================

    async def get_auth_message(self) -> str:
        """
        GET /v1/auth/message
        Get the message to sign for authentication.
        """
        result = await self._request("GET", "/auth/message", params={
            "address": self._address
        }, include_jwt=False)
        # API returns: {'success': True, 'data': {'message': '...'}}
        if result.get("success") and "data" in result:
            return result["data"].get("message", "")
        return result.get("message", "")

    async def get_jwt_token(self, message: str, signature: str) -> str:
        """
        POST /v1/auth
        Verify signature and get JWT token.

        Body format: {"signer": "0x...", "message": "...", "signature": "0x..."}
        """
        result = await self._request("POST", "/auth", data={
            "signer": self._address,  # API expects "signer", not "address"
            "message": message,
            "signature": signature
        }, include_jwt=False)
        # API returns: {'success': True, 'data': {'token': '...'}}
        if result.get("success") and "data" in result:
            data = result["data"]
            return data.get("token", data.get("jwt", data.get("accessToken", "")))
        return result.get("token", result.get("jwt", ""))

    async def authenticate(self) -> bool:
        """
        Authenticate with Predict.fun API.

        Flow:
        1. GET /auth/message - get message to sign (requires API key)
        2. Sign message with wallet (private key)
        3. POST /auth/jwt - get JWT token

        Note: API key is required for ALL Predict.fun API requests.
        Get it from Discord: https://discord.gg/predictdotfun (#api-access channel)
        """
        try:
            # Check API key
            if not self._api_key:
                raise PredictAPIError(
                    "API key required! Get it from Discord: https://discord.gg/predictdotfun (#api-access)"
                )

            # Step 1: Get auth message
            message = await self.get_auth_message()
            if not message:
                raise PredictAPIError("No auth message received")

            logger.debug(f"Auth message: {message[:50]}...")

            # Step 2: Sign message
            # For Predict Accounts, MUST use SDK's sign_predict_account_message
            # For EOA wallets, use standard eth_account signing
            if self._predict_account and self._order_builder:
                # Predict Account mode - use SDK
                signature = self._order_builder.sign_predict_account_message(message)
                logger.debug("Signed with SDK for Predict Account")
            else:
                # EOA mode - standard signing
                message_hash = encode_defunct(text=message)
                signed = self._account.sign_message(message_hash)
                signature = f"0x{signed.signature.hex()}"
                logger.debug("Signed with eth_account for EOA")

            # Step 3: Get JWT
            self._jwt_token = await self.get_jwt_token(message, signature)

            if not self._jwt_token:
                raise PredictAPIError("No JWT token received")

            logger.debug(f"Authenticated: {self._address[:10]}...")
            return True

        except Exception as e:
            logger.error(f"Authentication failed: {e}")
            return False

    async def ensure_authenticated(self):
        """Ensure we have valid JWT"""
        if not self._jwt_token:
            success = await self.authenticate()
            if not success:
                raise PredictAPIError("Authentication required but failed")

    async def relogin(self) -> bool:
        """
        Re-authenticate when token expires.
        Thread-safe with lock to prevent multiple simultaneous logins.
        """
        if not hasattr(self, '_relogin_lock') or self._relogin_lock is None:
            self._relogin_lock = asyncio.Lock()

        async with self._relogin_lock:
            try:
                # Clear old token
                self._jwt_token = None

                # Re-authenticate
                success = await self.authenticate()

                if success:
                    logger.success(f"Re-login successful for {self._address[:10]}...")
                    return True
                else:
                    logger.error("Re-login failed")
                    return False

            except Exception as e:
                logger.error(f"Re-login error: {e}")
                return False
