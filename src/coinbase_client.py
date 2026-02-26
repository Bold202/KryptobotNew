#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
Coinbase Advanced Trade API client.

Authentication uses the legacy Cloud API key format
(api_key + api_secret via HMAC-SHA256).
"""

import hashlib
import hmac
import json
import time
from typing import Dict, List, Optional

import requests


BASE_URL = "https://api.coinbase.com"
SANDBOX_URL = "https://api-public.sandbox.exchange.coinbase.com"


class CoinbaseAPIError(Exception):
    """Raised when the Coinbase API returns an error response."""

    def __init__(self, message: str, status_code: int = 0):
        super().__init__(message)
        self.status_code = status_code


class CoinbaseClient:
    """Thin wrapper around the Coinbase Advanced Trade REST API (v3)."""

    def __init__(self, api_key: str, api_secret: str, use_sandbox: bool = False):
        self.api_key = api_key
        self.api_secret = api_secret
        self.use_sandbox = use_sandbox
        self.base_url = SANDBOX_URL if use_sandbox else BASE_URL
        self._session = requests.Session()
        self._session.headers.update({"Content-Type": "application/json"})

    # ------------------------------------------------------------------
    # Auth
    # ------------------------------------------------------------------

    def _auth_headers(self, method: str, path: str, body: str = "") -> Dict[str, str]:
        timestamp = str(int(time.time()))
        message = timestamp + method.upper() + path + body
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).hexdigest()
        return {
            "CB-ACCESS-KEY": self.api_key,
            "CB-ACCESS-SIGN": signature,
            "CB-ACCESS-TIMESTAMP": timestamp,
        }

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _get(self, path: str, params: Optional[Dict] = None) -> dict:
        url = self.base_url + path
        headers = self._auth_headers("GET", path)
        resp = self._session.get(url, headers=headers, params=params, timeout=10)
        self._check(resp)
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload)
        headers = self._auth_headers("POST", path, body)
        url = self.base_url + path
        resp = self._session.post(url, headers=headers, data=body, timeout=10)
        self._check(resp)
        return resp.json()

    @staticmethod
    def _check(resp: requests.Response):
        if not resp.ok:
            try:
                msg = resp.json().get("message") or resp.json().get("error") or resp.text
            except Exception:
                msg = resp.text
            raise CoinbaseAPIError(msg, resp.status_code)

    # ------------------------------------------------------------------
    # Accounts
    # ------------------------------------------------------------------

    def get_accounts(self) -> List[dict]:
        """Return all accounts that have a non-zero balance."""
        path = "/api/v3/brokerage/accounts"
        data = self._get(path)
        accounts = data.get("accounts", [])
        result = []
        for acc in accounts:
            try:
                bal = float(acc.get("available_balance", {}).get("value", "0") or "0")
            except (ValueError, TypeError):
                bal = 0.0
            if bal > 0:
                result.append(
                    {
                        "uuid": acc.get("uuid", ""),
                        "name": acc.get("name", ""),
                        "currency": acc.get("currency", ""),
                        "balance": bal,
                        "hold": float(
                            acc.get("hold", {}).get("value", "0") or "0"
                        ),
                    }
                )
        return result

    # ------------------------------------------------------------------
    # Products / prices
    # ------------------------------------------------------------------

    def get_products(self, product_type: str = "SPOT") -> List[dict]:
        """Return all available spot products."""
        path = "/api/v3/brokerage/products"
        data = self._get(path, params={"product_type": product_type, "limit": 250})
        return data.get("products", [])

    def get_product(self, product_id: str) -> dict:
        """Return a single product including the current price."""
        path = f"/api/v3/brokerage/products/{product_id}"
        return self._get(path)

    def get_best_bid_ask(self, product_ids: List[str]) -> dict:
        """Return best bid/ask for one or more products."""
        if not product_ids:
            return {}
        path = "/api/v3/brokerage/best_bid_ask"
        params = {"product_ids": product_ids}
        return self._get(path, params=params)

    def get_candles(self, product_id: str, start: int, end: int, granularity: str = "ONE_HOUR") -> List[dict]:
        """Return OHLCV candles for *product_id*."""
        path = f"/api/v3/brokerage/products/{product_id}/candles"
        data = self._get(path, params={"start": start, "end": end, "granularity": granularity})
        return data.get("candles", [])

    # ------------------------------------------------------------------
    # Orders
    # ------------------------------------------------------------------

    def place_market_order(self, product_id: str, side: str, base_size: str) -> dict:
        """Place a market order.

        :param product_id: e.g. 'BTC-USD'
        :param side: 'BUY' or 'SELL'
        :param base_size: size in base currency as a string, e.g. '0.001'
        """
        import uuid as _uuid

        path = "/api/v3/brokerage/orders"
        payload = {
            "client_order_id": str(_uuid.uuid4()),
            "product_id": product_id,
            "side": side.upper(),
            "order_configuration": {
                "market_market_ioc": {
                    "base_size": str(base_size),
                }
            },
        }
        return self._post(path, payload)

    def get_order(self, order_id: str) -> dict:
        path = f"/api/v3/brokerage/orders/historical/{order_id}"
        return self._get(path)

    # ------------------------------------------------------------------
    # Utility
    # ------------------------------------------------------------------

    def get_owned_coins_with_prices(self) -> List[dict]:
        """
        Return the coins owned by the account together with their current
        USD price.  Each entry: {currency, balance, price_usd, value_usd}.
        """
        accounts = self.get_accounts()
        result: List[dict] = []

        # Separate fiat/stablecoin accounts from crypto accounts
        fiat_currencies = ("USD", "USDC", "USDT", "EUR", "GBP")
        crypto_accounts = [acc for acc in accounts if acc["currency"] not in fiat_currencies]

        # Fetch all crypto prices in a single batch request
        product_ids = [f"{acc['currency']}-USD" for acc in crypto_accounts]
        price_map: Dict[str, float] = {}
        if product_ids:
            try:
                raw = self.get_best_bid_ask(product_ids)
                # Response shape: {"pricebooks": [{"product_id": ..., "bids": [...], "asks": [...]}]}
                for entry in raw.get("pricebooks", []):
                    pid = entry.get("product_id")
                    if not pid:
                        continue
                    # Use best ask as the current price (what you'd pay to buy)
                    asks = entry.get("asks", [])
                    bids = entry.get("bids", [])
                    price_str = (
                        (asks[0].get("price") if asks else None)
                        or (bids[0].get("price") if bids else None)
                    )
                    try:
                        price_map[pid] = float(price_str or 0)
                    except (ValueError, TypeError):
                        price_map[pid] = 0.0
            except CoinbaseAPIError:
                pass  # Fall back to 0 prices below

        for acc in accounts:
            currency = acc["currency"]
            balance = acc["balance"]
            if currency in fiat_currencies:
                result.append(
                    {
                        "currency": currency,
                        "balance": balance,
                        "price_usd": 1.0,
                        "value_usd": balance,
                        "product_id": None,
                    }
                )
                continue
            product_id = f"{currency}-USD"
            price = price_map.get(product_id, 0.0)
            result.append(
                {
                    "currency": currency,
                    "balance": balance,
                    "price_usd": price,
                    "value_usd": balance * price,
                    "product_id": product_id,
                }
            )
        return result
