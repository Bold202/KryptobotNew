#!/usr/bin/env python3
"""
KryptoBot - Coinbase Crypto Assistant
Trading Engine: monitors prices and executes trades when thresholds are met.
"""

import json
import logging
import os
import threading
import time
from collections import deque
from typing import Callable, Dict, List, Optional, Tuple

from coinbase_client import CoinbaseClient, CoinbaseAPIError

logger = logging.getLogger(__name__)

TRADING_STATE_FILE = os.path.join(os.path.expanduser("~"), ".kryptobot", "trading_state.json")


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
                 session_manager=None, coinbase_config: Optional[dict] = None):
        """
        :param client:           initialised CoinbaseClient
        :param config:           'trading' section from ConfigManager
        :param on_event:         callback(event_type, data) for GUI / API updates
        :param session_manager:  optional SessionManager instance
        :param coinbase_config:  optional 'coinbase' section for sandbox/live check
        """
        self._client = client
        self._config = config
        self._on_event = on_event or (lambda *_: None)
        self._session = session_manager  # Sitzungserfassung
        self._coinbase_config = coinbase_config or {}

        self._active = False
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

        # product_id -> reference price captured at engine start / after trade
        self._reference_prices: Dict[str, float] = {}

        # fixed_eur_steps mode: per-coin state {"last_action": None|"BUY"|"SELL"}
        self._coin_states: Dict[str, dict] = {}

        # Anti-churn: per-coin trade timestamps (deque of float timestamps)
        self._trade_timestamps: Dict[str, deque] = {}

        # Latest snapshot: list of {currency, balance, price_usd, value_usd, product_id}
        self.portfolio_snapshot: List[dict] = []

        # Global reserve/liquidity pool (in USD, for dip purchases)
        self._reserve_pool_usd: float = 0.0
        # Circuit breaker state (blocks automatic BUYs below critical thresholds)
        self._circuit_breaker_active: bool = False

    # ------------------------------------------------------------------
    # Control
    # ------------------------------------------------------------------

    @property
    def is_active(self) -> bool:
        return self._active

    @property
    def reserve_pool_usd(self) -> float:
        """Current balance of the global reserve/liquidity pool in USD."""
        return self._reserve_pool_usd

    @property
    def circuit_breaker_active(self) -> bool:
        """True when the circuit breaker is active (automatic buys paused)."""
        return self._circuit_breaker_active

    def start(self):
        """Start the monitoring loop in a background thread."""
        with self._lock:
            if self._active:
                return
            # Zustand beim Start aus Datei laden (Restart-Toleranz)
            self._load_state()
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

        # Circuit-Breaker-Prüfung (vor Modus-spezifischer Logik)
        total_portfolio_usd = sum(c.get("value_usd", 0.0) for c in coins)
        self._check_and_update_circuit_breaker(total_portfolio_usd, coins)

        mode = self._config.get("mode", "threshold_percent")
        if mode in ("fixed_eur_steps", "fixed_steps"):
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
          - Safe Live Mode: Live-Trading nur wenn live_trading_armed=True
          - max_order_size_percent: Ein Trade darf nicht mehr als X% des Gesamtportfolios umfassen
          - max_position_percent:   Eine Coin-Position darf X% des Portfolios nicht überschreiten
          - max_daily_loss_percent: Bei zu großem Tagesverlust wird der Automatik-Handel gestoppt
        """
        # --- Safe Live Mode check ---
        if not self._is_trading_allowed():
            self._block_trade(product_id, "auto_trade")
            return

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
            # Circuit-Breaker: Automatische Käufe gesperrt?
            if self._circuit_breaker_active:
                msg = (
                    f"Circuit Breaker aktiv: Automatischer Kauf für {product_id} pausiert "
                    f"(Portfolio unter Mindestschwelle)."
                )
                logger.warning(msg)
                self._on_event("limit_blocked", {
                    "reason": "circuit_breaker", "message": msg, "product_id": product_id,
                })
                return

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

        # Profitabilitätsprüfung
        profitable, profit_reason = self._check_profitability(product_id, change_pct, current_price)
        if not profitable:
            logger.warning(profit_reason)
            self._on_event("limit_blocked", {
                "reason": "unprofitable", "message": profit_reason, "product_id": product_id,
            })
            return

        # Trade ausführen
        try:
            result = self._client.place_market_order(product_id, side, str(base_size))
            logger.info("Auto-Trade ausgeführt: %s %s %.6f @ %.4f", side, product_id, base_size, current_price)
            self._record_anti_churn_trade(product_id)
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
            # Reinvestment nach erfolgreichem Verkauf
            if side == "SELL":
                self._apply_reinvestment(product_id, base_size * current_price)
            self._save_state()
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

        Field names: 'base_value'/'step' (preferred) or 'base_value_usd'/'step_usd' (legacy).
        Entries with enabled=False are skipped.
        """
        coin_map = {c.get("product_id"): c for c in coins if c.get("product_id")}
        strategies = self._config.get("coin_strategies", [])

        # Portfolio total for security-limit checks (mirrors threshold mode)
        total_portfolio_usd = sum(c.get("value_usd", 0.0) for c in coins)
        if self._session:
            self._session.set_portfolio_start_value(total_portfolio_usd)

        # Security limits – only applied when explicitly set in config
        max_daily_loss_pct = self._config.get("max_daily_loss_percent")
        max_position_pct = self._config.get("max_position_percent")

        for strategy in strategies:
            product_id = strategy.get("product_id")
            if not product_id:
                continue
            # Respect per-coin enabled flag (default True for backward compat)
            if not strategy.get("enabled", True):
                continue
            # Support both 'base_value' (preferred) and legacy 'base_value_usd'
            base_value = float(strategy.get("base_value", strategy.get("base_value_usd", 25.0)))
            # Support both 'step' (preferred) and legacy 'step_usd'
            step = float(strategy.get("step", strategy.get("step_usd", 0.5)))

            coin = coin_map.get(product_id)
            if coin is None:
                continue

            current_price = float(coin.get("price_usd", 0.0))
            if current_price <= 0:
                continue

            value = float(coin.get("value_usd", 0.0))
            state = self._coin_states.setdefault(product_id, {"last_action": None})
            last_action = state.get("last_action")

            logger.debug(
                "fixed_step %s: value=%.4f base=%.4f step=%.4f last_action=%s",
                product_id, value, base_value, step, last_action,
            )

            if value >= base_value + step:
                if last_action == "BUY":
                    msg = (
                        f"Fixed-Step SELL geblockt für {product_id}: "
                        f"letzter Trade war BUY (anti-Oszillation)."
                    )
                    logger.debug(msg)
                    self._on_event("limit_blocked", {
                        "reason": "anti_oscillation", "message": msg, "product_id": product_id,
                    })
                else:
                    # Safe Live Mode check
                    if not self._is_trading_allowed():
                        self._block_trade(product_id, "fixed_step SELL")
                        continue
                    # Anti-churn check
                    churn_reason = self._check_anti_churn(product_id, strategy)
                    if churn_reason:
                        msg = (
                            f"Trade geblockt für {product_id}: "
                            f"{'Cooldown läuft noch' if churn_reason == 'cooldown' else 'Max. Trades/Stunde erreicht'}."
                        )
                        logger.debug(msg)
                        self._on_event("limit_blocked", {
                            "reason": churn_reason, "message": msg, "product_id": product_id,
                        })
                        continue
                    # Daily-loss check (only when limit is configured)
                    if max_daily_loss_pct is not None and self._session and total_portfolio_usd > 0:
                        start_value = self._session.get_portfolio_start_value()
                        if start_value and start_value > 0:
                            loss_pct = (start_value - total_portfolio_usd) / start_value * 100.0
                            if loss_pct >= float(max_daily_loss_pct):
                                msg = (
                                    f"Tagesverlust-Limit erreicht: Portfolio um {loss_pct:.1f}% "
                                    f"gefallen (Limit: {max_daily_loss_pct:.1f}%). "
                                    f"Kein Auto-Trade für {product_id}."
                                )
                                logger.warning(msg)
                                self._on_event("limit_blocked", {
                                    "reason": "max_daily_loss", "message": msg,
                                    "product_id": product_id,
                                })
                                continue
                    base_size = step / current_price
                    self._execute_fixed_step_trade(
                        product_id, "SELL", base_size, current_price, value, base_value, step,
                    )
                    state["last_action"] = "SELL"

            elif value <= base_value - step:
                # Safe Live Mode check
                if not self._is_trading_allowed():
                    self._block_trade(product_id, "fixed_step BUY")
                    continue
                # Anti-churn check
                churn_reason = self._check_anti_churn(product_id, strategy)
                if churn_reason:
                    msg = (
                        f"Trade geblockt für {product_id}: "
                        f"{'Cooldown läuft noch' if churn_reason == 'cooldown' else 'Max. Trades/Stunde erreicht'}."
                    )
                    logger.debug(msg)
                    self._on_event("limit_blocked", {
                        "reason": churn_reason, "message": msg, "product_id": product_id,
                    })
                    continue
                # Circuit-Breaker: Automatische Käufe gesperrt?
                if self._circuit_breaker_active:
                    msg = (
                        f"Circuit Breaker aktiv: Automatischer Kauf für {product_id} pausiert "
                        f"(Portfolio unter Mindestschwelle)."
                    )
                    logger.warning(msg)
                    self._on_event("limit_blocked", {
                        "reason": "circuit_breaker", "message": msg, "product_id": product_id,
                    })
                    continue
                # Position-limit check for BUY (only when limit is configured)
                if max_position_pct is not None and total_portfolio_usd > 0:
                    max_coin_value = total_portfolio_usd * float(max_position_pct) / 100.0
                    if value + step > max_coin_value:
                        msg = (
                            f"Positions-Limit erreicht für {product_id}: "
                            f"Position ({value:.2f} USD) bereits bei "
                            f"{max_position_pct:.0f}% Limit."
                        )
                        logger.warning(msg)
                        self._on_event("limit_blocked", {
                            "reason": "max_position", "message": msg, "product_id": product_id,
                        })
                        continue
                # Daily-loss check (only when limit is configured)
                if max_daily_loss_pct is not None and self._session and total_portfolio_usd > 0:
                    start_value = self._session.get_portfolio_start_value()
                    if start_value and start_value > 0:
                        loss_pct = (start_value - total_portfolio_usd) / start_value * 100.0
                        if loss_pct >= float(max_daily_loss_pct):
                            msg = (
                                f"Tagesverlust-Limit erreicht: Portfolio um {loss_pct:.1f}% "
                                f"gefallen (Limit: {max_daily_loss_pct:.1f}%). "
                                f"Kein Auto-Trade für {product_id}."
                            )
                            logger.warning(msg)
                            self._on_event("limit_blocked", {
                                "reason": "max_daily_loss", "message": msg,
                                "product_id": product_id,
                            })
                            continue
                base_size = step / current_price
                self._execute_fixed_step_trade(
                    product_id, "BUY", base_size, current_price, value, base_value, step,
                )
                state["last_action"] = "BUY"

    def _execute_fixed_step_trade(self, product_id: str, side: str, base_size: float,
                                  current_price: float, current_value: float,
                                  base_value: float, step: float):
        """Execute a single trade for the fixed-step strategy."""
        # Profitabilitätsprüfung: Step muss Fees + Spread übertreffen
        expected_move_pct = (step / base_value * 100.0) if base_value > 0 else 0.0
        profitable, profit_reason = self._check_profitability(product_id, expected_move_pct, current_price)
        if not profitable:
            logger.warning(profit_reason)
            self._on_event("limit_blocked", {
                "reason": "unprofitable", "message": profit_reason, "product_id": product_id,
            })
            return

        usd_amount = base_size * current_price

        # Reserve-Pool für Nachkäufe nutzen (nur bei BUY, z.B. Markteinbruch)
        if side == "BUY":
            pool_extra = self._maybe_use_liquidity_pool(product_id, usd_amount)
            if pool_extra > 0:
                base_size += pool_extra / current_price
                usd_amount += pool_extra

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
            self._record_anti_churn_trade(product_id)
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
            # Reinvestment nach erfolgreichem Verkauf
            if side == "SELL":
                self._apply_reinvestment(product_id, usd_amount, base_value)
            self._save_state()
        except Exception as exc:
            logger.error("Fixed-Step Trade fehlgeschlagen für %s: %s", product_id, exc)
            self._on_event("error", {"message": f"Fixed-Step Trade Fehler ({product_id}): {exc}"})

    # ------------------------------------------------------------------
    # Manual trade helpers (called from GUI / API)
    # ------------------------------------------------------------------

    def manual_buy(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual BUY %s %s", base_size, product_id)
        if not self._is_trading_allowed():
            self._block_trade(product_id, "manual_buy")
            raise RuntimeError(
                "Live-Trading nicht freigegeben. Setze 'live_trading_armed=true' oder verwende Sandbox."
            )
        # Profitabilitätsprüfung: Warnung bei manuellem Trade (kein Block)
        price = self._get_price_from_snapshot(product_id)
        profitable, profit_reason = self._check_profitability(product_id, 0.0, price)
        if not profitable:
            logger.warning("Manueller Kauf – Profitabilitätswarnung: %s", profit_reason)
            self._on_event("unprofitable_trade_warning", {
                "side": "BUY", "product_id": product_id, "message": profit_reason,
            })
        result = self._client.place_market_order(product_id, "BUY", base_size)
        self._on_event("order_placed", {
            "side": "BUY", "product_id": product_id,
            "base_size": float(base_size), "is_auto": False, "result": result,
        })
        if self._session:
            # Preis aus Portfolio-Snapshot holen, falls vorhanden
            self._session.record_trade("BUY", product_id, float(base_size), price, is_auto=False)
        return result

    def manual_sell(self, product_id: str, base_size: str) -> dict:
        logger.info("Manual SELL %s %s", base_size, product_id)
        if not self._is_trading_allowed():
            self._block_trade(product_id, "manual_sell")
            raise RuntimeError(
                "Live-Trading nicht freigegeben. Setze 'live_trading_armed=true' oder verwende Sandbox."
            )
        # Profitabilitätsprüfung: Warnung bei manuellem Trade (kein Block)
        price = self._get_price_from_snapshot(product_id)
        profitable, profit_reason = self._check_profitability(product_id, 0.0, price)
        if not profitable:
            logger.warning("Manueller Verkauf – Profitabilitätswarnung: %s", profit_reason)
            self._on_event("unprofitable_trade_warning", {
                "side": "SELL", "product_id": product_id, "message": profit_reason,
            })
        result = self._client.place_market_order(product_id, "SELL", base_size)
        self._on_event("order_placed", {
            "side": "SELL", "product_id": product_id,
            "base_size": float(base_size), "is_auto": False, "result": result,
        })
        if self._session:
            self._session.record_trade("SELL", product_id, float(base_size), price, is_auto=False)
        # Reinvestment nach erfolgreichem manuellen Verkauf
        usd_value = float(base_size) * price
        if usd_value > 0:
            self._apply_reinvestment(product_id, usd_value)
            self._save_state()
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

    def update_config(self, new_config: dict, coinbase_config: Optional[dict] = None):
        self._config = new_config
        if coinbase_config is not None:
            self._coinbase_config = coinbase_config

    # ------------------------------------------------------------------
    # Safe Live Mode helpers
    # ------------------------------------------------------------------

    def _is_trading_allowed(self) -> bool:
        """
        Returns True when a trade may be executed:
          - sandbox mode (use_sandbox=True), OR
          - live mode AND live_trading_armed=True
        """
        use_sandbox = self._coinbase_config.get("use_sandbox", False)
        armed = self._config.get("live_trading_armed", False)
        return bool(use_sandbox or armed)

    def _block_trade(self, product_id: str, context: str = ""):
        """Fire trade_blocked_safety event and log a warning."""
        msg = (
            f"Trade blockiert (Safe-Live-Mode): Live-Trading nicht freigegeben. "
            f"Setze 'live_trading_armed=true' oder verwende Sandbox. "
            f"{'(' + context + ')' if context else ''}"
        ).strip()
        logger.warning(msg)
        self._on_event("trade_blocked_safety", {"product_id": product_id, "message": msg})

    # ------------------------------------------------------------------
    # Anti-churn helpers
    # ------------------------------------------------------------------

    def _check_anti_churn(self, product_id: str, strategy: dict) -> Optional[str]:
        """
        Check cooldown and rate-limit for a coin.
        Returns None if trading is allowed, or a reason string if blocked.
        """
        now = time.time()
        cooldown = int(strategy.get("cooldown_seconds", 60))
        max_per_hour = int(strategy.get("max_trades_per_hour", 6))

        timestamps = self._trade_timestamps.setdefault(product_id, deque())

        # Remove timestamps older than 1 hour
        cutoff = now - 3600
        while timestamps and timestamps[0] < cutoff:
            timestamps.popleft()

        # Cooldown: time since last trade
        if timestamps:
            last_trade = timestamps[-1]
            elapsed = now - last_trade
            if elapsed < cooldown:
                return "cooldown"

        # Rate limit: number of trades in last hour
        if len(timestamps) >= max_per_hour:
            return "rate_limit"

        return None

    def _record_anti_churn_trade(self, product_id: str):
        """Record a trade timestamp for anti-churn tracking."""
        timestamps = self._trade_timestamps.setdefault(product_id, deque())
        timestamps.append(time.time())

    def get_cooldown_remaining(self, product_id: str, strategy: dict) -> float:
        """Return seconds remaining in cooldown for a coin (0 if not in cooldown)."""
        timestamps = self._trade_timestamps.get(product_id)
        if not timestamps:
            return 0.0
        cooldown = int(strategy.get("cooldown_seconds", 60))
        elapsed = time.time() - timestamps[-1]
        return max(0.0, cooldown - elapsed)

    def get_trades_last_hour(self, product_id: str) -> int:
        """Return number of trades recorded in the last hour for a coin."""
        timestamps = self._trade_timestamps.get(product_id)
        if not timestamps:
            return 0
        cutoff = time.time() - 3600
        return sum(1 for t in timestamps if t >= cutoff)

    # ------------------------------------------------------------------
    # State persistence (_load_state / _save_state)
    # ------------------------------------------------------------------

    def _load_state(self):
        """Load persisted trading state from file (restart tolerance)."""
        state_file = self._config.get("state_file", TRADING_STATE_FILE)
        try:
            if os.path.exists(state_file):
                with open(state_file, "r") as fh:
                    data = json.load(fh)
                self._coin_states.update(data.get("coin_states", {}))
                for pid, ts_list in data.get("trade_timestamps", {}).items():
                    self._trade_timestamps[pid] = deque(ts_list)
                self._reserve_pool_usd = float(data.get("reserve_pool_usd", 0.0))
                logger.info(
                    "Trading-Zustand geladen: %d Coins, Reserve-Pool: %.2f USD",
                    len(self._coin_states), self._reserve_pool_usd,
                )
        except (json.JSONDecodeError, OSError, KeyError, ValueError) as exc:
            logger.warning("Konnte Trading-Zustand nicht laden: %s", exc)

    def _save_state(self):
        """Persist trading state to disk (restart tolerance)."""
        # Nur persistieren, wenn die Engine aktiv läuft (nicht bei direkten Testaufrufen)
        if not self._active:
            return
        state_file = self._config.get("state_file", TRADING_STATE_FILE)
        data = {
            "coin_states": self._coin_states,
            "trade_timestamps": {
                pid: list(ts) for pid, ts in self._trade_timestamps.items()
            },
            "reserve_pool_usd": self._reserve_pool_usd,
        }
        try:
            os.makedirs(os.path.dirname(state_file), exist_ok=True)
            with open(state_file, "w") as fh:
                json.dump(data, fh, indent=2)
        except OSError as exc:
            logger.warning("Konnte Trading-Zustand nicht speichern: %s", exc)

    # ------------------------------------------------------------------
    # Profitability check
    # ------------------------------------------------------------------

    def _get_spread_pct(self, product_id: str, current_price: float) -> float:
        """Return the current bid/ask spread as a percentage of the mid price."""
        try:
            bba = self._client.get_best_bid_ask([product_id])
            for pb in bba.get("pricebooks", []):
                if pb.get("product_id") == product_id:
                    bids = pb.get("bids", [])
                    asks = pb.get("asks", [])
                    if bids and asks:
                        bid = float(bids[0].get("price", 0) or 0)
                        ask = float(asks[0].get("price", 0) or 0)
                        if bid > 0 and ask > 0:
                            mid = (bid + ask) / 2.0
                            return (ask - bid) / mid * 100.0
        except Exception as exc:
            logger.debug("Spread-Abfrage fehlgeschlagen für %s: %s", product_id, exc)
        return 0.0

    def _check_profitability(
        self,
        product_id: str,
        expected_move_pct: float,
        current_price: float,
    ) -> Tuple[bool, str]:
        """
        Prüft ob ein automatischer Trade profitabel ist.

        :param product_id:        z. B. 'BTC-USD'
        :param expected_move_pct: erwarteter Kursmove in % (aus change_pct oder step/base*100)
        :param current_price:     aktueller Preis (für Spread-Referenz)
        :returns: (profitable, reason) – wenn nicht profitabel, enthält reason die Begründung.
        """
        if not self._config.get("profitability_check_enabled", True):
            return True, ""

        fee_pct = float(self._config.get("round_trip_fee_percent", 1.2))
        spread_pct = self._get_spread_pct(product_id, current_price)
        min_move = fee_pct + spread_pct

        if abs(expected_move_pct) < min_move:
            reason = (
                f"Trade unprofitabel für {product_id}: "
                f"Erwarteter Move {abs(expected_move_pct):.2f}% < "
                f"Fees {fee_pct:.2f}% + Spread {spread_pct:.2f}% = {min_move:.2f}%"
            )
            return False, reason
        return True, ""

    # ------------------------------------------------------------------
    # Reserve-Pool & Reinvestment
    # ------------------------------------------------------------------

    def _apply_reinvestment(
        self,
        product_id: str,
        usd_value: float,
        config_base_value: float = 0.0,
    ):
        """
        Splittet Verkaufserlöse:
          - Ein konfigurierbarer Anteil (reinvest_fraction) wird als kumulierter
            Reinvestitionsbetrag pro Coin verfolgt (für spätere Base-Anpassungen).
          - Der Rest fließt in den globalen Reserve-Pool.

        :param product_id:        z. B. 'BTC-USD'
        :param usd_value:         Gesamterlös des Verkaufs in USD
        :param config_base_value: Konfigurations-base_value (für fixed_step; 0 = ignorieren)
        """
        reinvest_fraction = float(self._config.get("reinvest_fraction", 0.25))
        reinvest_amount = usd_value * reinvest_fraction
        pool_amount = usd_value - reinvest_amount

        # Per-Coin Reinvestitionsbetrag kumulieren (getrennt vom aktiven base_value)
        if reinvest_amount > 0:
            state = self._coin_states.setdefault(product_id, {"last_action": None})
            prev = float(state.get("reinvest_accumulated", 0.0))
            state["reinvest_accumulated"] = prev + reinvest_amount
            logger.info(
                "Reinvestment: %s +%.2f USD → Reinvest-Topf (gesamt: %.2f USD)",
                product_id, reinvest_amount, state["reinvest_accumulated"],
            )

        # Rest in Reserve-Pool
        if pool_amount > 0:
            self._reserve_pool_usd += pool_amount
            logger.info(
                "Reinvestment: %.2f USD → Reserve-Pool (gesamt: %.2f USD)",
                pool_amount, self._reserve_pool_usd,
            )
            self._on_event("reserve_pool_updated", {
                "product_id": product_id,
                "pool_amount_added": pool_amount,
                "reinvest_amount": reinvest_amount,
                "total_pool_usd": self._reserve_pool_usd,
            })

    def _maybe_use_liquidity_pool(self, product_id: str, usd_needed: float) -> float:
        """
        Stellt Mittel aus dem Reserve-Pool für Nachkäufe bei Markteinbrüchen bereit.

        :param product_id: z. B. 'BTC-USD'
        :param usd_needed: Gewünschter USD-Betrag
        :returns: Tatsächlich bereitgestellter Betrag (0 wenn Pool leer/unzureichend).
        """
        min_pool_use = float(self._config.get("min_pool_use_usd", 1.0))
        if self._reserve_pool_usd < min_pool_use:
            return 0.0
        available = min(self._reserve_pool_usd, usd_needed)
        if available <= 0:
            return 0.0
        self._reserve_pool_usd -= available
        logger.info(
            "Reserve-Pool: %.2f USD für %s verwendet (verbleibend: %.2f USD)",
            available, product_id, self._reserve_pool_usd,
        )
        self._on_event("reserve_pool_used", {
            "product_id": product_id,
            "amount_used": available,
            "remaining_pool_usd": self._reserve_pool_usd,
        })
        return available

    # ------------------------------------------------------------------
    # Circuit Breaker
    # ------------------------------------------------------------------

    def _check_and_update_circuit_breaker(
        self, total_portfolio_usd: float, coins: List[dict]
    ):
        """
        Prüft ob der globale Circuit Breaker ausgelöst werden soll.

        Auslösekriterien (jeweils optional/konfigurierbar):
          - circuit_breaker_min_portfolio_usd: Gesamtportfolio unter Schwellenwert
          - circuit_breaker_min_fiat_usd:      Freie Fiat-Reserve unter Schwellenwert

        Bei Auslösung: automatische Käufe pausiert, Event "circuit_breaker_triggered".
        Bei Erholung:  Käufe wieder freigegeben, Event "circuit_breaker_reset".
        """
        min_portfolio = self._config.get("circuit_breaker_min_portfolio_usd")
        min_fiat = self._config.get("circuit_breaker_min_fiat_usd")

        triggered = False

        if min_portfolio is not None and total_portfolio_usd < float(min_portfolio):
            triggered = True

        if min_fiat is not None:
            fiat_usd = sum(
                c.get("value_usd", 0.0) for c in coins
                if c.get("currency") in ("USD", "USDC", "USDT")
            )
            if fiat_usd < float(min_fiat):
                triggered = True

        if triggered and not self._circuit_breaker_active:
            self._circuit_breaker_active = True
            msg = (
                f"Circuit Breaker ausgelöst: Portfolio {total_portfolio_usd:.2f} USD. "
                f"Automatische Käufe pausiert."
            )
            logger.warning(msg)
            self._on_event("circuit_breaker_triggered", {
                "total_portfolio_usd": total_portfolio_usd,
                "message": msg,
            })
        elif not triggered and self._circuit_breaker_active:
            self._circuit_breaker_active = False
            logger.info("Circuit Breaker deaktiviert (Portfolio erholt).")
            self._on_event("circuit_breaker_reset", {
                "total_portfolio_usd": total_portfolio_usd,
            })

