#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
Trading Engine: monitors prices and executes trades when thresholds are met.
"""

import logging
import threading
import time
from typing import Callable, Dict, List, Optional

from coinbase_client import CoinbaseClient, CoinbaseAPIError

logger = logging.getLogger(__name__)


class TradingEngine:
    """
    Background price-monitoring loop that fires trades when a coin's price
    moves more than *threshold_percent* from its reference price.
    """

    def __init__(self, client: CoinbaseClient, config: dict, on_event: Optional[Callable] = None):
        """
        :param client:          initialised CoinbaseClient
        :param config:          'trading' section from ConfigManager
        :param on_event:        callback(event_type, data) for GUI / API updates
        """
        self._client = client
        self._config = config
        self._on_event = on_event or (lambda *_: None)

        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # product_id -> reference price captured at engine start / after trade
        self._reference_prices: Dict[str, float] = {}

        # Latest snapshot: list of {currency, balance, price_usd, value_usd, product_id}
        self.portfolio_snapshot: List[dict] = []

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    def start(self):
        """Start the monitoring loop in a background thread."""
        with self._lock:
            if self._active:
                return
            self._active = True
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
            logger.info("Trading engine started.")
            self._on_event("engine_started", {})

    def stop(self):
        """Signal the monitoring loop to stop."""
        with self._lock:
            self._active = False
        logger.info("Trading engine stopping…")
        self._on_event("engine_stopped", {})

    # ------------------------------------------------------------------
    # Monitoring loop
    # ------------------------------------------------------------------

    def _loop(self):
        while self._active:
            try:
                self._tick()
            except Exception as exc:
                logger.error("Error in trading loop: %s", exc)
                self._on_event("error", {"message": str(exc)})
            interval = int(self._config.get("check_interval_seconds", 60))
            for _ in range(interval):
                if not self._active:
                    break
                time.sleep(1)

    def _tick(self):
        """One monitoring cycle."""
        coins = self._client.get_owned_coins_with_prices()
        self.portfolio_snapshot = coins
        self._on_event("portfolio_update", {"coins": coins})

        threshold = float(self._config.get("threshold_percent", 2.0))
        pairs = self._config.get("pairs", [])  # e.g. ['BTC-USD', 'ETH-USD']

        for coin in coins:
            product_id = coin.get("product_id")
            if not product_id:
                continue
            if pairs and product_id not in pairs:
                continue

            current_price = coin["price_usd"]
            if current_price <= 0:
                continue

            ref = self._reference_prices.get(product_id)
            if ref is None:
                self._reference_prices[product_id] = current_price
                logger.info("Reference price set: %s @ %.4f", product_id, current_price)
                continue

            change_pct = ((current_price - ref) / ref) * 100.0
            logger.debug("%s: ref=%.4f current=%.4f change=%.2f%%", product_id, ref, current_price, change_pct)

            if abs(change_pct) >= threshold:
                self._on_threshold_reached(product_id, ref, current_price, change_pct, coin)

    def _on_threshold_reached(self, product_id, ref_price, current_price, change_pct, coin):
        logger.info(
            "Threshold reached for %s: %.2f%% change (ref=%.4f, now=%.4f)",
            product_id, change_pct, ref_price, current_price,
        )
        self._on_event(
            "threshold_reached",
            {
                "product_id": product_id,
                "ref_price": ref_price,
                "current_price": current_price,
                "change_pct": change_pct,
                "balance": coin.get("balance", 0),
            },
        )
        # Reset reference price so we track from the new level
        self._reference_prices[product_id] = current_price

    # ------------------------------------------------------------------
    # Manual trade helpers (called from GUI / API)
    # ------------------------------------------------------------------

    def manual_buy(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual BUY %s %s", base_size, product_id)
        result = self._client.place_market_order(product_id, "BUY", base_size)
        self._on_event("order_placed", {"side": "BUY", "product_id": product_id, "result": result})
        return result

    def manual_sell(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual SELL %s %s", base_size, product_id)
        result = self._client.place_market_order(product_id, "SELL", base_size)
        self._on_event("order_placed", {"side": "SELL", "product_id": product_id, "result": result})
        return result

    # ------------------------------------------------------------------
    # Config update (hot-reload from GUI)
    # ------------------------------------------------------------------

    def update_config(self, new_config: dict):
        self._config = new_config
