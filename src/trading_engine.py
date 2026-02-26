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

    Supported modes (trading.mode):
      - threshold_percent (default): fires when price changes by a set %
      - fixed_eur_steps: buy/sell a fixed USD amount per coin when the
        coin's portfolio value crosses base_value ± step thresholds.
    """

    def __init__(self, client: CoinbaseClient, config: dict, on_event: Optional[Callable] = None,
                 session_manager=None):
        """
        :param client:           initialised CoinbaseClient
        :param config:           'trading' section from ConfigManager
        :param on_event:         callback(event_type, data) for GUI / API updates
        :param session_manager:  optional SessionManager instance
        """
        self._client = client
        self._config = config
        self._on_event = on_event or (lambda *_: None)
        self._session = session_manager  # Sitzungserfassung

        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # product_id -> reference price captured at engine start / after trade
        self._reference_prices: Dict[str, float] = {}

        # fixed_eur_steps mode: per-coin state {"last_action": None|"BUY"|"SELL"}
        self._coin_states: Dict[str, dict] = {}

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
            # Neue Sitzung beginnen
            if self._session:
                self._session.start_session()
                self._on_event("session_started", {})

    def stop(self):
        """Signal the monitoring loop to stop."""
        with self._lock:
            self._active = False
        logger.info("Trading engine stopping…")
        # Sitzung beenden und in Historie speichern
        if self._session:
            self._session.end_session()
            self._on_event("session_ended", {})
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

        mode = self._config.get("mode", "threshold_percent")
        if mode == "fixed_eur_steps":
            self._tick_fixed_eur_steps(coins)
        else:
            self._tick_threshold(coins)

    # ------------------------------------------------------------------
    # Mode: threshold_percent (original logic)
    # ------------------------------------------------------------------

    def _tick_threshold(self, coins: List[dict]):
        """Original threshold-based price monitoring."""
        # Portfolio-Gesamtwert berechnen (für Verlustlimit und Positionsgröße)
        total_portfolio_usd = sum(c.get("value_usd", 0.0) for c in coins)

        # Portfolio-Startwert beim ersten Tick der Sitzung festhalten
        if self._session:
            self._session.set_portfolio_start_value(total_portfolio_usd)

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
                self._on_threshold_reached(product_id, ref, current_price, change_pct, coin, total_portfolio_usd)

    def _on_threshold_reached(self, product_id, ref_price, current_price, change_pct, coin,
                              total_portfolio_usd: float = 0.0):
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

        # Automatischen Trade auslösen, wenn Automatik-Modus aktiv ist
        if self._config.get("auto_trade_enabled", False):
            self._maybe_auto_trade(product_id, current_price, change_pct, coin, total_portfolio_usd)

    # ------------------------------------------------------------------
    # Auto-Trade Logic (threshold mode)
    # ------------------------------------------------------------------

    def _maybe_auto_trade(self, product_id: str, current_price: float, change_pct: float,
                          coin: dict, total_portfolio_usd: float):
        """
        Entscheidet, ob automatisch gehandelt werden soll (Mean-Revert-Strategie):
          - Kurs stark gesunken → Kauf (günstig einkaufen)
          - Kurs stark gestiegen → Verkauf (Gewinne mitnehmen)

        Sicherheitsprüfungen:
          - max_order_size_percent: Ein Trade darf nicht mehr als X% des Gesamtportfolios umfassen
          - max_position_percent:   Eine Coin-Position darf X% des Portfolios nicht überschreiten
          - max_daily_loss_percent: Bei zu großem Tagesverlust wird der Automatik-Handel gestoppt
        """
        order_size_pct = float(self._config.get("order_size_percent", 5.0))
        max_position_pct = float(self._config.get("max_position_percent", 50.0))
        max_daily_loss_pct = float(self._config.get("max_daily_loss_percent", 5.0))

        # --- Tagesverlust-Schutz ---
        # Wenn das Portfolio seit Sitzungsstart zu stark gefallen ist, nicht mehr handeln
        if self._session and total_portfolio_usd > 0:
            start_value = self._session.get_portfolio_start_value()
            if start_value and start_value > 0:
                loss_pct = (start_value - total_portfolio_usd) / start_value * 100.0
                if loss_pct >= max_daily_loss_pct:
                    msg = (
                        f"Tagesverlust-Limit erreicht: Portfolio um {loss_pct:.1f}% gefallen "
                        f"(Limit: {max_daily_loss_pct:.1f}%). Kein Auto-Trade für {product_id}."
                    )
                    logger.warning(msg)
                    self._on_event("limit_blocked", {"reason": "max_daily_loss", "message": msg,
                                                     "product_id": product_id})
                    return

        coin_balance = float(coin.get("balance", 0.0))
        coin_value_usd = float(coin.get("value_usd", 0.0))

        if change_pct < 0:
            # Kurs gefallen → Kaufen
            # Berechneter Kaufbetrag: X% des Gesamtportfolios in USD
            usd_to_spend = total_portfolio_usd * order_size_pct / 100.0

            if usd_to_spend <= 0 or current_price <= 0:
                return

            # Positionslimit prüfen: Coin-Position darf max_position_percent nicht überschreiten
            max_coin_value = total_portfolio_usd * max_position_pct / 100.0
            if coin_value_usd + usd_to_spend > max_coin_value:
                usd_to_spend = max(0.0, max_coin_value - coin_value_usd)
                if usd_to_spend <= 0:
                    msg = (
                        f"Positions-Limit erreicht für {product_id}: "
                        f"Position ({coin_value_usd:.2f} USD) bereits bei {max_position_pct:.0f}% Limit."
                    )
                    logger.warning(msg)
                    self._on_event("limit_blocked", {"reason": "max_position", "message": msg,
                                                     "product_id": product_id})
                    return

            base_size = usd_to_spend / current_price
            side = "BUY"
            decision_text = (
                f"Kurs stark gefallen ({change_pct:+.2f}%) → kaufe {base_size:.6f} {product_id} "
                f"(≈ {usd_to_spend:.2f} USD)"
            )

        else:
            # Kurs gestiegen → Verkaufen
            # Berechneter Verkaufsbetrag: X% des aktuellen Coin-Guthabens
            base_size = coin_balance * order_size_pct / 100.0

            if base_size <= 0:
                return

            side = "SELL"
            usd_to_spend = base_size * current_price
            decision_text = (
                f"Kurs stark gestiegen ({change_pct:+.2f}%) → verkaufe {base_size:.6f} {product_id} "
                f"(≈ {usd_to_spend:.2f} USD)"
            )

        logger.info("Auto-Trade Entscheidung: %s", decision_text)
        self._on_event("auto_trade_decision", {
            "product_id": product_id,
            "side": side,
            "base_size": base_size,
            "price_usd": current_price,
            "change_pct": change_pct,
            "message": decision_text,
        })

        # Trade ausführen
        try:
            result = self._client.place_market_order(product_id, side, str(base_size))
            logger.info("Auto-Trade ausgeführt: %s %s %.6f @ %.4f", side, product_id, base_size, current_price)
            self._on_event("order_placed", {
                "side": side,
                "product_id": product_id,
                "base_size": base_size,
                "price_usd": current_price,
                "is_auto": True,
                "result": result,
            })
            # In Sitzung erfassen
            if self._session:
                self._session.record_trade(side, product_id, base_size, current_price, is_auto=True)
        except Exception as exc:
            logger.error("Auto-Trade fehlgeschlagen für %s: %s", product_id, exc)
            self._on_event("error", {"message": f"Auto-Trade Fehler ({product_id}): {exc}"})

    # ------------------------------------------------------------------
    # Mode: fixed_eur_steps
    # ------------------------------------------------------------------

    def _tick_fixed_eur_steps(self, coins: List[dict]):
        """
        Fixed-step strategy (per-coin):
          - value >= base_value + step AND last_action != "BUY"  → SELL step worth
          - value <= base_value - step                           → BUY step worth

        After a BUY, further SELLs are blocked until a SELL occurs first.
        """
        coin_map = {c.get("product_id"): c for c in coins if c.get("product_id")}
        strategies = self._config.get("coin_strategies", [])

        for strategy in strategies:
            product_id = strategy.get("product_id")
            if not product_id:
                continue
            base_value = float(strategy.get("base_value_usd", 25.0))
            step = float(strategy.get("step_usd", 0.5))

            coin = coin_map.get(product_id)
            if coin is None:
                continue

            current_price = float(coin.get("price_usd", 0.0))
            balance = float(coin.get("balance", 0.0))
            if current_price <= 0:
                continue

            value = balance * current_price
            state = self._coin_states.setdefault(product_id, {"last_action": None})
            last_action = state.get("last_action")

            logger.debug(
                "fixed_step %s: value=%.4f base=%.4f step=%.4f last_action=%s",
                product_id, value, base_value, step, last_action,
            )

            if value >= base_value + step:
                # Sell only if the previous action was not a BUY (anti-oscillation lock)
                if last_action != "BUY":
                    base_size = step / current_price
                    self._execute_fixed_step_trade(
                        product_id, "SELL", base_size, current_price, value, base_value, step,
                    )
                    state["last_action"] = "SELL"
                else:
                    logger.debug(
                        "fixed_step %s: SELL geblockt (last_action=BUY)", product_id,
                    )
            elif value <= base_value - step:
                base_size = step / current_price
                self._execute_fixed_step_trade(
                    product_id, "BUY", base_size, current_price, value, base_value, step,
                )
                state["last_action"] = "BUY"

    def _execute_fixed_step_trade(self, product_id: str, side: str, base_size: float,
                                  current_price: float, current_value: float,
                                  base_value: float, step: float):
        """Execute a single trade for the fixed-step strategy."""
        usd_amount = base_size * current_price
        threshold = base_value + step if side == "SELL" else base_value - step
        op_sym = "≥" if side == "SELL" else "≤"
        action_word = "verkaufe" if side == "SELL" else "kaufe"
        msg = (
            f"Fixed-Step {side}: {product_id} Wert {current_value:.4f} USD "
            f"({op_sym} {threshold:.4f}) → {action_word} {base_size:.6f} (≈ {usd_amount:.4f} USD)"
        )
        logger.info(msg)
        self._on_event("auto_trade_decision", {
            "product_id": product_id,
            "side": side,
            "base_size": base_size,
            "price_usd": current_price,
            "change_pct": 0.0,
            "message": msg,
        })

        try:
            result = self._client.place_market_order(product_id, side, str(base_size))
            logger.info(
                "Fixed-Step Trade ausgeführt: %s %s %.6f @ %.4f",
                side, product_id, base_size, current_price,
            )
            self._on_event("order_placed", {
                "side": side,
                "product_id": product_id,
                "base_size": base_size,
                "price_usd": current_price,
                "is_auto": True,
                "result": result,
            })
            if self._session:
                self._session.record_trade(side, product_id, base_size, current_price, is_auto=True)
        except Exception as exc:
            logger.error("Fixed-Step Trade fehlgeschlagen für %s: %s", product_id, exc)
            self._on_event("error", {"message": f"Fixed-Step Trade Fehler ({product_id}): {exc}"})

    # ------------------------------------------------------------------
    # Manual trade helpers (called from GUI / API)
    # ------------------------------------------------------------------

    def manual_buy(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual BUY %s %s", base_size, product_id)
        result = self._client.place_market_order(product_id, "BUY", base_size)
        self._on_event("order_placed", {
            "side": "BUY", "product_id": product_id,
            "base_size": float(base_size), "is_auto": False, "result": result,
        })
        if self._session:
            # Preis aus Portfolio-Snapshot holen, falls vorhanden
            price = self._get_price_from_snapshot(product_id)
            self._session.record_trade("BUY", product_id, float(base_size), price, is_auto=False)
        return result

    def manual_sell(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual SELL %s %s", base_size, product_id)
        result = self._client.place_market_order(product_id, "SELL", base_size)
        self._on_event("order_placed", {
            "side": "SELL", "product_id": product_id,
            "base_size": float(base_size), "is_auto": False, "result": result,
        })
        if self._session:
            price = self._get_price_from_snapshot(product_id)
            self._session.record_trade("SELL", product_id, float(base_size), price, is_auto=False)
        return result

    def _get_price_from_snapshot(self, product_id: str) -> float:
        """Hilfsfunktion: Aktuellen Preis aus dem Portfolio-Snapshot lesen."""
        for coin in self.portfolio_snapshot:
            if coin.get("product_id") == product_id:
                return float(coin.get("price_usd", 0.0))
        return 0.0

    # ------------------------------------------------------------------
    # Config update (hot-reload from GUI)
    # ------------------------------------------------------------------

    def update_config(self, new_config: dict):
        self._config = new_config

